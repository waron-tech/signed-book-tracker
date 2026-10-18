import json
import os
import re
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

# 1. CONFIGURATION
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
SEEN_FILE = "seen_books.json"

# Optional: Add favourite authors or keywords here (case-insensitive).
# Matching books trigger a high-priority alarm notification on Android.
PRIORITY_WATCHLIST = [
    "Mick Herron",
    "Rebecca Yarros",
    "Richard Osman",
    "Brandon Sanderson",
    "RF Kuang",
]

# Configure the bookshops you want to monitor.
# Use type "shopify" for Shopify-powered stores (Goldsboro, Inkstone, etc.)
# Use type "html" for standard HTML pages with CSS selectors.
BOOKSHOPS = [
    {
        "name": "Goldsboro Books (New Signed Releases)",
        "type": "shopify",
        "url": "https://goldsborobooks.com/collections/new-releases/products.json?limit=50",
        "base_product_url": "https://goldsborobooks.com/products/",
        "require_signed_keyword": False,  # Goldsboro new releases are signed by default
    },
    {
        "name": "Goldsboro Books (Special Editions & Exclusives)",
        "type": "shopify",
        "url": "https://goldsborobooks.com/collections/special-editions-exclusives/products.json?limit=50",
        "base_product_url": "https://goldsborobooks.com/products/",
        "require_signed_keyword": False,
    },
    # Example HTML config you can enable or copy for other bookshops:
    # {
    #     "name": "Waterstones Signed Editions",
    #     "type": "html",
    #     "url": "https://www.waterstones.com/campaign/signed-editions",
    #     "card_selector": "div.book-preview",
    #     "title_selector": "a.title",
    #     "price_selector": "span.price",
    #     "link_selector": "a.title",
    #     "require_signed_keyword": True,
    # }
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Mobile Safari/537.36"
    )
}


def clean_header(text: str) -> str:
    """Ensures HTTP headers sent to ntfy are Latin-1 safe."""
    return text.encode("latin-1", errors="ignore").decode("latin-1")


def send_ntfy_alert(title: str, message: str, book_url: str, is_priority: bool = False):
    """Pushes an instant notification to the Android ntfy app with a 1-tap buy button."""
    if not NTFY_TOPIC:
        print("WARNING: NTFY_TOPIC is not set. Skipping push notification.")
        return

    priority = "urgent" if is_priority else "high"
    tags = "rotating_light,books" if is_priority else "books,sparkles"

    headers = {
        "Title": clean_header(title),
        "Priority": priority,
        "Tags": tags,
        "Click": book_url,
        "Actions": clean_header(f"view, Open Book Page, {book_url}, clear=false"),
    }

    response = requests.post(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=message.encode("utf-8"),
        headers=headers,
        timeout=15,
    )
    response.raise_for_status()


def scrape_shopify(shop: dict) -> list[dict]:
    """Fetches books from any Shopify-based bookshop collection."""
    resp = requests.get(shop["url"], headers=HEADERS, timeout=20)
    resp.raise_for_status()
    products = resp.json().get("products", [])

    found = []
    for prod in products:
        title = prod.get("title", "").strip()
        handle = prod.get("handle", "")
        vendor = prod.get("vendor", "")
        body_html = prod.get("body_html", "") or ""
        variants = prod.get("variants", [])

        # Check if at least one variant is available to order/pre-order
        available = any(v.get("available", False) for v in variants)
        if not available:
            continue

        price = variants[0].get("price", "0.00") if variants else "N/A"
        book_url = f"{shop['base_product_url']}{handle}"

        # Optional keyword filter if monitoring a mixed signed/unsigned collection
        if shop.get("require_signed_keyword", False):
            combined_text = f"{title} {body_html}".lower()
            if "signed" not in combined_text:
                continue

        display_title = f"{title} ({vendor})" if vendor and vendor not in title else title
        found.append({
            "id": f"{shop['name']}::{handle}",
            "title": display_title,
            "price": f"£{price}",
            "shop": shop["name"],
            "url": book_url,
        })
    return found


def scrape_html(shop: dict) -> list[dict]:
    """Fetches books from standard HTML bookshop pages using CSS selectors."""
    resp = requests.get(shop["url"], headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    found = []
    cards = soup.select(shop["card_selector"])
    for card in cards:
        title_el = card.select_one(shop["title_selector"])
        link_el = card.select_one(shop["link_selector"])
        if not title_el or not link_el:
            continue

        title = title_el.get_text(" ", strip=True)
        href = link_el.get("href", "")
        book_url = urljoin(shop["url"], href)

        price_el = card.select_one(shop.get("price_selector", ""))
        price = price_el.get_text(strip=True) if price_el else "See site"

        if shop.get("require_signed_keyword", False):
            card_text = card.get_text(" ", strip=True).lower()
            if "signed" not in card_text:
                continue

        found.append({
            "id": f"{shop['name']}::{book_url}",
            "title": title,
            "price": price,
            "shop": shop["name"],
            "url": book_url,
        })
    return found


def matches_watchlist(text: str) -> bool:
    text_lower = text.lower()
    return any(keyword.lower() in text_lower for keyword in PRIORITY_WATCHLIST)


def main():
    is_first_run = not os.path.exists(SEEN_FILE)
    if is_first_run:
        seen_ids = set()
    else:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            seen_ids = set(json.load(f))

    all_current_books = []
    for shop in BOOKSHOPS:
        try:
            print(f"Checking {shop['name']}...")
            if shop["type"] == "shopify":
                books = scrape_shopify(shop)
            else:
                books = scrape_html(shop)
            print(f" -> Found {len(books)} available signed editions.")
            all_current_books.extend(books)
        except Exception as e:
            print(f"Error scraping {shop['name']}: {e}")

    new_books = [b for b in all_current_books if b["id"] not in seen_ids]

    if is_first_run:
        print(f"First run: cataloged {len(new_books)} existing books without spamming alerts.")
        for b in new_books:
            seen_ids.add(b["id"])
        send_ntfy_alert(
            title="Signed Book Tracker Active",
            message=f"Cataloged {len(new_books)} existing signed editions. You will be alerted as soon as a new book drops!",
            book_url="https://goldsborobooks.com/collections/new-releases",
            is_priority=False,
        )
    else:
        for b in new_books:
            is_vip = matches_watchlist(b["title"])
            prefix = "VIP WATCHLIST DROP: " if is_vip else "New Signed Edition: "
            send_ntfy_alert(
                title=f"{prefix}{b['title']}",
                message=f"Shop: {b['shop']}\nPrice: {b['price']}\nTap below to order immediately.",
                book_url=b["url"],
                is_priority=is_vip,
            )
            seen_ids.add(b["id"])
            print(f"Alert sent for: {b['title']}")

    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(seen_ids)), f, indent=2)


if __name__ == "__main__":
    main()
