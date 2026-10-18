import json
import os
from datetime import datetime, timezone
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

# 1. CONFIGURATION
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
SEEN_FILE = "seen_books.json"
FEED_FILE = "books_feed.json"

PRIORITY_WATCHLIST = [
    "Mick Herron",
    "Rebecca Yarros",
    "Richard Osman",
    "Brandon Sanderson",
    "RF Kuang",
]

BOOKSHOPS = [
    {
        "name": "Goldsboro Books (New Releases)",
        "type": "shopify",
        "url": "https://goldsborobooks.com/collections/new-releases/products.json?limit=50",
        "base_product_url": "https://goldsborobooks.com/products/",
        "require_signed_keyword": False,
    },
    {
        "name": "Goldsboro Books (Special Editions)",
        "type": "shopify",
        "url": "https://goldsborobooks.com/collections/special-editions-exclusives/products.json?limit=50",
        "base_product_url": "https://goldsborobooks.com/products/",
        "require_signed_keyword": False,
    },
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Mobile Safari/537.36"
    )
}


def clean_header(text: str) -> str:
    return text.encode("latin-1", errors="ignore").decode("latin-1")


def matches_watchlist(text: str) -> bool:
    text_lower = text.lower()
    return any(keyword.lower() in text_lower for keyword in PRIORITY_WATCHLIST)


def send_ntfy_alert(title: str, message: str, book_url: str, cover_url: str = "", is_priority: bool = False):
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
        "Actions": clean_header(f"view, Buy Now, {book_url}, clear=false"),
    }
    # Attach cover art directly to the Android push notification if available
    if cover_url and cover_url.startswith("http"):
        headers["Attach"] = cover_url

    response = requests.post(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=message.encode("utf-8"),
        headers=headers,
        timeout=15,
    )
    response.raise_for_status()


def scrape_shopify(shop: dict) -> list[dict]:
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
        images = prod.get("images", [])

        available = any(v.get("available", False) for v in variants)
        if not available:
            continue

        if shop.get("require_signed_keyword", False):
            if "signed" not in f"{title} {body_html}".lower():
                continue

        price = variants[0].get("price", "0.00") if variants else "N/A"
        cover_url = images[0].get("src", "") if images else ""
        book_url = f"{shop['base_product_url']}{handle}"
        display_title = f"{title} — {vendor}" if vendor and vendor not in title else title

        found.append({
            "id": f"{shop['name']}::{handle}",
            "title": display_title,
            "author": vendor or "Signed Edition",
            "price": f"£{price}",
            "shop": shop["name"],
            "url": book_url,
            "image": cover_url,
            "vip": matches_watchlist(display_title),
        })
    return found


def scrape_html(shop: dict) -> list[dict]:
    resp = requests.get(shop["url"], headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    found = []
    for card in soup.select(shop["card_selector"]):
        title_el = card.select_one(shop["title_selector"])
        link_el = card.select_one(shop["link_selector"])
        if not title_el or not link_el:
            continue

        title = title_el.get_text(" ", strip=True)
        book_url = urljoin(shop["url"], link_el.get("href", ""))

        price_el = card.select_one(shop.get("price_selector", ""))
        price = price_el.get_text(strip=True) if price_el else "See site"

        img_el = card.select_one("img")
        cover_url = ""
        if img_el:
            raw_img = img_el.get("data-src") or img_el.get("src") or ""
            cover_url = urljoin(shop["url"], raw_img)

        if shop.get("require_signed_keyword", False):
            if "signed" not in card.get_text(" ", strip=True).lower():
                continue

        found.append({
            "id": f"{shop['name']}::{book_url}",
            "title": title,
            "author": "Signed Edition",
            "price": price,
            "shop": shop["name"],
            "url": book_url,
            "image": cover_url,
            "vip": matches_watchlist(title),
        })
    return found


def main():
    is_first_run = not os.path.exists(SEEN_FILE)
    seen_ids = set()
    if not is_first_run:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            seen_ids = set(json.load(f))

    # Load previous feed to preserve 'discovered_at' timestamps
    existing_feed_map = {}
    if os.path.exists(FEED_FILE):
        try:
            with open(FEED_FILE, "r", encoding="utf-8") as f:
                old_data = json.load(f)
                for item in old_data.get("books", []):
                    existing_feed_map[item["id"]] = item.get("discovered_at")
        except Exception:
            pass

    now_iso = datetime.now(timezone.utc).isoformat()
    all_current_books = []

    for shop in BOOKSHOPS:
        try:
            print(f"Checking {shop['name']}...")
            books = scrape_shopify(shop) if shop["type"] == "shopify" else scrape_html(shop)
            for b in books:
                b["discovered_at"] = existing_feed_map.get(b["id"], now_iso)
            all_current_books.extend(books)
        except Exception as e:
            print(f"Error scraping {shop['name']}: {e}")

    new_books = [b for b in all_current_books if b["id"] not in seen_ids]

    if is_first_run:
        for b in new_books:
            seen_ids.add(b["id"])
        send_ntfy_alert(
            title="Signed Book Dashboard Active",
            message=f"Loaded {len(new_books)} signed books with cover art into your feed.",
            book_url="https://goldsborobooks.com/collections/new-releases",
        )
    else:
        for b in new_books:
            prefix = "VIP DROP: " if b["vip"] else "New Signed Edition: "
            send_ntfy_alert(
                title=f"{prefix}{b['title']}",
                message=f"Shop: {b['shop']}\nPrice: {b['price']}\nTap below to order.",
                book_url=b["url"],
                cover_url=b.get("image", ""),
                is_priority=b["vip"],
            )
            seen_ids.add(b["id"])

    # Sort newest spotted books to the top of the dashboard
    all_current_books.sort(key=lambda x: x.get("discovered_at", ""), reverse=True)

    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(seen_ids)), f, indent=2)

    with open(FEED_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {
                "last_updated": now_iso,
                "total_available": len(all_current_books),
                "books": all_current_books,
            },
            f,
            indent=2,
        )


if __name__ == "__main__":
    main()
    
