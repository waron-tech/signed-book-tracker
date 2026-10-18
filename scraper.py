import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

# Playwright for headless browser scraping on protected sites like Waterstones
try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

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
    "R. F. Kuang",
    "Ian Rankin",
    "V E Schwab",
]

# Bookshops including Waterstones via headless browser
BOOKSHOPS = [
    {
        "name": "Goldsboro Books",
        "type": "shopify",
        "url": "https://goldsborobooks.com/collections/new-releases/products.json?limit=50",
        "base_product_url": "https://goldsborobooks.com/products/",
        "require_signed_keyword": False,
    },
    {
        "name": "Rare Mags",
        "type": "shopify",
        "url": "https://raremags.co.uk/collections/books/products.json?limit=50",
        "base_product_url": "https://raremags.co.uk/products/",
        "require_signed_keyword": True,
    },
    {
        "name": "Porty Books",
        "type": "shopify",
        "url": "https://theportobellobookshop.com/collections/signed-editions/products.json?limit=50",
        "base_product_url": "https://theportobellobookshop.com/products/",
        "require_signed_keyword": False,
    },
    {
        "name": "Waterstones",
        "type": "browser",
        "url": "https://www.waterstones.com/campaign/signed-editions",
    },
    {
        "name": "Coles Books",
        "type": "html",
        "url": "https://coles-books.co.uk/signed/books?product_list_order=created_at",
        "card_selector": "li.product-item, div.product-item-info",
        "title_selector": "a.product-item-link, strong.product-item-name a",
        "price_selector": "span.price",
        "link_selector": "a.product-item-link",
        "require_signed_keyword": False,
    },
    {
        "name": "Booka Bookshop",
        "type": "html",
        "url": "https://www.bookabookshop.co.uk/category/signed-copies/",
        "card_selector": "li.product, .type-product",
        "title_selector": ".woocommerce-loop-product__title, h2, h3",
        "price_selector": "span.price, .woocommerce-Price-amount",
        "link_selector": "a.woocommerce-LoopProduct-link, a[href]",
        "require_signed_keyword": False,
    },
    {
        "name": "West End Lane Books",
        "type": "html",
        "url": "https://www.welbooks.co.uk/shop/signed-editions",
        "card_selector": ".product-grid__item, .wsite-com-category-product, .product-card",
        "title_selector": ".product-title, .wsite-com-link-text, h2, h3, a",
        "price_selector": ".product-price, .price, span",
        "link_selector": "a[href]",
        "require_signed_keyword": False,
    },
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
}


def clean_header(text: str) -> str:
    return text.encode("latin-1", errors="ignore").decode("latin-1")


def matches_watchlist(text: str) -> bool:
    text_lower = text.lower()
    return any(keyword.lower() in text_lower for keyword in PRIORITY_WATCHLIST)


def extract_uk_price(text: str) -> str:
    match = re.search(r"£\s*\d+(?:\.\d{2})?", text)
    return match.group(0).replace(" ", "") if match else "See site"


def send_ntfy_alert(title: str, message: str, book_url: str, cover_url: str = "", is_priority: bool = False):
    if not NTFY_TOPIC:
        return
    priority = "urgent" if is_priority else "high"
    tags = "rotating_light,books" if is_priority else "books,sparkles"
    headers = {
        "Title": clean_header(title[:120]),
        "Priority": priority,
        "Tags": tags,
        "Click": book_url,
        "Actions": clean_header(f"view, Buy Now, {book_url}, clear=false"),
    }
    if cover_url and cover_url.startswith("http"):
        headers["Attach"] = cover_url
    try:
        requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=message.encode("utf-8"), headers=headers, timeout=10)
    except Exception:
        pass


def scrape_shopify(shop: dict) -> list[dict]:
    resp = requests.get(shop["url"], headers=HEADERS, timeout=15)
    resp.raise_for_status()
    products = resp.json().get("products", [])

    found = []
    for prod in products:
        title = prod.get("title", "").strip()
        handle = prod.get("handle", "")
        vendor = prod.get("vendor", "")
        body_html = prod.get("body_html", "") or ""
        tags = " ".join(prod.get("tags", [])) if isinstance(prod.get("tags"), list) else str(prod.get("tags", ""))
        variants = prod.get("variants", [])
        images = prod.get("images", [])

        if not any(v.get("available", False) for v in variants):
            continue

        if shop.get("require_signed_keyword", False):
            if "signed" not in f"{title} {body_html} {tags}".lower():
                continue

        price = variants[0].get("price", "0.00") if variants else "N/A"
        cover_url = images[0].get("src", "") if images else ""
        book_url = f"{shop['base_product_url']}{handle}"
        display_title = f"{title} — {vendor}" if vendor and vendor.lower() not in title.lower() else title

        found.append({
            "id": f"{shop['name']}::{handle}",
            "title": display_title,
            "author": vendor or shop["name"],
            "price": f"£{price}",
            "shop": shop["name"],
            "url": book_url,
            "image": cover_url,
            "vip": matches_watchlist(display_title),
        })
    return found


def scrape_browser(shop: dict) -> list[dict]:
    """Uses a real headless browser to bypass Waterstones Cloudflare bot protection."""
    if not HAS_PLAYWRIGHT:
        print("Playwright not installed.")
        return []

    found = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=HEADERS["User-Agent"])
        try:
            page.goto(shop["url"], timeout=30000, wait_until="domcontentloaded")
            page.wait_for_selector("div.book-preview, div.search-result-tab", timeout=10000)
            
            cards = page.locator("div.book-preview, div.search-result-tab").all()
            for card in cards:
                text = card.inner_text()
                if any(out in text.lower() for out in ["out of stock", "sold out"]):
                    continue

                title_el = card.locator("a.title, .title-wrap a").first
                title = title_el.inner_text().strip() if title_el.count() > 0 else ""
                href = title_el.get_attribute("href") if title_el.count() > 0 else ""
                if not title or not href:
                    continue

                book_url = urljoin(shop["url"], href)
                price_el = card.locator("span.price, .book-price").first
                price_text = price_el.inner_text() if price_el.count() > 0 else text
                price = extract_uk_price(price_text)

                img_el = card.locator("img").first
                cover_url = img_el.get_attribute("src") if img_el.count() > 0 else ""

                found.append({
                    "id": f"{shop['name']}::{book_url}",
                    "title": title,
                    "author": shop["name"],
                    "price": price,
                    "shop": shop["name"],
                    "url": book_url,
                    "image": cover_url,
                    "vip": matches_watchlist(f"{title} {text}"),
                })
        except Exception as e:
            print(f"Browser scrape error for {shop['name']}: {e}")
        finally:
            browser.close()
    return found


def scrape_html(shop: dict) -> list[dict]:
    resp = requests.get(shop["url"], headers=HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    found = []
    seen_urls = set()
    cards = soup.select(shop["card_selector"])

    for card in cards:
        card_text = card.get_text(" ", strip=True)
        if any(out in card_text.lower() for out in ["out of stock", "sold out", "unavailable"]):
            continue

        link_el = card.select_one(shop["link_selector"])
        if not link_el or not link_el.get("href"):
            continue

        book_url = urljoin(shop["url"], link_el.get("href", "").strip())
        if book_url in seen_urls or book_url.rstrip("/") == shop["url"].rstrip("/"):
            continue

        title_el = card.select_one(shop["title_selector"])
        title = title_el.get_text(" ", strip=True) if title_el else link_el.get("title", "").strip()
        if not title or len(title) < 3 or title.lower() in ["find out more", "read more", "add to basket", "view"]:
            img_alt = card.select_one("img[alt]")
            title = img_alt.get("alt", "").strip() if img_alt else ""
        if not title or len(title) < 3:
            continue

        price_el = card.select_one(shop.get("price_selector", ""))
        raw_price = price_el.get_text(" ", strip=True) if price_el else card_text
        price = extract_uk_price(raw_price)

        img_el = card.select_one("img")
        cover_url = ""
        if img_el:
            raw_img = img_el.get("data-src") or img_el.get("src") or ""
            if raw_img and not raw_img.startswith("data:"):
                cover_url = urljoin(shop["url"], raw_img)

        seen_urls.add(book_url)
        found.append({
            "id": f"{shop['name']}::{book_url}",
            "title": title,
            "author": shop["name"],
            "price": price,
            "shop": shop["name"],
            "url": book_url,
            "image": cover_url,
            "vip": matches_watchlist(f"{title} {card_text}"),
        })
    return found


def main():
    is_first_run = not os.path.exists(SEEN_FILE)
    seen_ids = set()
    if not is_first_run:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            seen_ids = set(json.load(f))

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
            if shop["type"] == "shopify":
                books = scrape_shopify(shop)
            elif shop["type"] == "browser":
                books = scrape_browser(shop)
            else:
                books = scrape_html(shop)
            print(f" -> Found {len(books)} available signed editions.")
            for b in books:
                b["discovered_at"] = existing_feed_map.get(b["id"], now_iso)
            all_current_books.extend(books)
        except Exception as e:
            print(f" -> Skipped {shop['name']} due to error: {e}")

    new_books = [b for b in all_current_books if b["id"] not in seen_ids]

    if is_first_run:
        for b in new_books:
            seen_ids.add(b["id"])
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
