import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

# Use curl_cffi if installed to bypass Cloudflare on Waterstones/Hatchards/Foyles
try:
    from curl_cffi import requests as cffi_requests
    HAS_CFFI = True
except ImportError:
    HAS_CFFI = False

# 1. CONFIGURATION
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
SEEN_FILE = "seen_books.json"
FEED_FILE = "books_feed.json"

# Add your favourite authors here for urgent VIP alerts & VIP badges
PRIORITY_WATCHLIST = [
    "Mick Herron",
    "Rebecca Yarros",
    "Richard Osman",
    "Brandon Sanderson",
    "RF Kuang",
    "R. F. Kuang",
    "Ian Rankin",
    "V E Schwab",
    "Elif Shafak",
]

# All 10 UK Bookshops Pre-Configured
BOOKSHOPS = [
    # --- 1. GOLDSBORO BOOKS (Shopify JSON) ---
    {
        "name": "Goldsboro Books",
        "type": "shopify",
        "url": "https://goldsborobooks.com/collections/new-releases/products.json?limit=50",
        "base_product_url": "https://goldsborobooks.com/products/",
        "require_signed_keyword": False,
    },
    {
        "name": "Goldsboro Exclusives",
        "type": "shopify",
        "url": "https://goldsborobooks.com/collections/special-editions-exclusives/products.json?limit=50",
        "base_product_url": "https://goldsborobooks.com/products/",
        "require_signed_keyword": False,
    },
    # --- 2. RARE MAGS (Shopify JSON - Filtered to Signed Books) ---
    {
        "name": "Rare Mags",
        "type": "shopify",
        "url": "https://raremags.co.uk/collections/books/products.json?limit=50",
        "base_product_url": "https://raremags.co.uk/products/",
        "require_signed_keyword": True,
    },
    # --- 3. COLES BOOKS (Magento HTML) ---
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
    # --- 4. BOOKA BOOKSHOP (WooCommerce HTML) ---
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
    # --- 5. LINGHAMS BOOKS (WooCommerce HTML) ---
    {
        "name": "Linghams Books",
        "type": "html",
        "url": "https://linghams.co.uk/product-category/signed/",
        "card_selector": "li.product, .type-product",
        "title_selector": ".woocommerce-loop-product__title, h2, h3",
        "price_selector": "span.price, .woocommerce-Price-amount",
        "link_selector": "a.woocommerce-LoopProduct-link, a[href]",
        "require_signed_keyword": False,
    },
    # --- 6. PORTY BOOKS / THE PORTOBELLO BOOKSHOP ---
    {
        "name": "Porty Books",
        "type": "html",
        "url": "https://www.theportobellobookshop.com/collection-of/special-editions",
        "card_selector": "article, li, .book-card, .product-card, div[class*='item'], div[class*='card']",
        "title_selector": "h2, h3, h4, .title, a",
        "price_selector": ".price, span",
        "link_selector": "a[href]",
        "require_signed_keyword": True,
    },
    # --- 7. WEST END LANE BOOKS ---
    {
        "name": "West End Lane Books",
        "type": "html",
        "url": "https://www.welbooks.co.uk/shop/signed-editions",
        "card_selector": ".product-grid__item, .wsite-com-category-product, .product-card, div[class*='product']",
        "title_selector": ".product-title, .wsite-com-link-text, h2, h3, a",
        "price_selector": ".product-price, .price, span",
        "link_selector": "a[href]",
        "require_signed_keyword": False,
    },
    # --- 8. WATERSTONES ---
    {
        "name": "Waterstones",
        "type": "html",
        "url": "https://www.waterstones.com/campaign/signed-editions",
        "card_selector": "div.book-preview, div.search-result-tab, div.book-thumb",
        "title_selector": "a.title, .title-wrap a",
        "price_selector": "span.price, .book-price",
        "link_selector": "a.title, .image-wrap a",
        "require_signed_keyword": False,
    },
    # --- 9. HATCHARDS ---
    {
        "name": "Hatchards",
        "type": "html",
        "url": "https://www.hatchards.co.uk/signed-editions",
        "card_selector": "div.book-preview, div.search-result-tab, div.book-thumb",
        "title_selector": "a.title, .title-wrap a",
        "price_selector": "span.price, .book-price",
        "link_selector": "a.title, .image-wrap a",
        "require_signed_keyword": False,
    },
    # --- 10. FOYLES ---
    {
        "name": "Foyles",
        "type": "html",
        "url": "https://www.foyles.co.uk/campaign/signed-editions",
        "card_selector": "div.book-preview, div.search-result-tab, div.book-thumb",
        "title_selector": "a.title, .title-wrap a",
        "price_selector": "span.price, .book-price",
        "link_selector": "a.title, .image-wrap a",
        "require_signed_keyword": False,
    },
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Mobile Safari/537.36"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
}


def fetch_url(url: str):
    """Uses browser TLS impersonation when available so Cloudflare sites don't block the scraper."""
    if HAS_CFFI:
        resp = cffi_requests.get(url, headers=HEADERS, impersonate="chrome124", timeout=25)
    else:
        resp = requests.get(url, headers=HEADERS, timeout=25)
    resp.raise_for_status()
    return resp


def clean_header(text: str) -> str:
    return text.encode("latin-1", errors="ignore").decode("latin-1")


def matches_watchlist(text: str) -> bool:
    text_lower = text.lower()
    return any(keyword.lower() in text_lower for keyword in PRIORITY_WATCHLIST)


def extract_uk_price(text: str) -> str:
    """Extracts a clean £XX.XX price from messy HTML text."""
    match = re.search(r"£\s*\d+(?:\.\d{2})?", text)
    return match.group(0).replace(" ", "") if match else "See site"


def send_ntfy_alert(title: str, message: str, book_url: str, cover_url: str = "", is_priority: bool = False):
    if not NTFY_TOPIC:
        print("WARNING: NTFY_TOPIC is not set. Skipping push notification.")
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

    requests.post(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=message.encode("utf-8"),
        headers=headers,
        timeout=15,
    )


def scrape_shopify(shop: dict) -> list[dict]:
    resp = fetch_url(shop["url"])
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

        available = any(v.get("available", False) for v in variants)
        if not available:
            continue

        if shop.get("require_signed_keyword", False):
            combined = f"{title} {body_html} {tags}".lower()
            if "signed" not in combined:
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


def scrape_html(shop: dict) -> list[dict]:
    resp = fetch_url(shop["url"])
    soup = BeautifulSoup(resp.text, "html.parser")

    found = []
    seen_urls = set()
    cards = soup.select(shop["card_selector"])

    for card in cards:
        card_text = card.get_text(" ", strip=True)
        # Skip Sold Out / Out of Stock items
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
            # Fallback to image alt text if title link was generic
            img_alt = card.select_one("img[alt]")
            title = img_alt.get("alt", "").strip() if img_alt else ""
        if not title or len(title) < 3:
            continue

        if shop.get("require_signed_keyword", False):
            if "signed" not in card_text.lower() and "signed" not in title.lower():
                continue

        price_el = card.select_one(shop.get("price_selector", ""))
        raw_price = price_el.get_text(" ", strip=True) if price_el else card_text
        price = extract_uk_price(raw_price)

        img_el = card.select_one("img")
        cover_url = ""
        if img_el:
            raw_img = (
                img_el.get("data-src")
                or img_el.get("data-lazy-src")
                or img_el.get("srcset", "").split(" ")[0]
                or img_el.get("src")
                or ""
            )
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
    previous_shops = {sid.split("::")[0] for sid in seen_ids if "::" in sid}

    for shop in BOOKSHOPS:
        try:
            print(f"Checking {shop['name']}...")
            books = scrape_shopify(shop) if shop["type"] == "shopify" else scrape_html(shop)
            print(f" -> Found {len(books)} available signed editions.")
            for b in books:
                b["discovered_at"] = existing_feed_map.get(b["id"], now_iso)
            all_current_books.extend(books)
        except Exception as e:
            print(f" -> Could not reach {shop['name']} this run: {e}")

    new_books = [b for b in all_current_books if b["id"] not in seen_ids]

    if is_first_run:
        for b in new_books:
            seen_ids.add(b["id"])
        send_ntfy_alert(
            title="10-Shop Signed Book Tracker Active",
            message=f"Loaded {len(new_books)} signed editions across your UK bookshops.",
            book_url="https://goldsborobooks.com/collections/new-releases",
        )
    else:
        # Group newly added bookshops so adding a new shop doesn't spam 50 alerts at once
        newly_added_shop_counts = {}
        for b in new_books:
            shop_name = b["shop"]
            if shop_name not in previous_shops:
                newly_added_shop_counts[shop_name] = newly_added_shop_counts.get(shop_name, 0) + 1
                seen_ids.add(b["id"])
            else:
                prefix = "VIP DROP: " if b["vip"] else "New Signed Edition: "
                send_ntfy_alert(
                    title=f"{prefix}{b['title']}",
                    message=f"Shop: {b['shop']}\nPrice: {b['price']}\nTap below to order.",
                    book_url=b["url"],
                    cover_url=b.get("image", ""),
                    is_priority=b["vip"],
                )
                seen_ids.add(b["id"])

        if newly_added_shop_counts:
            summary = ", ".join(f"{k} ({v})" for k, v in newly_added_shop_counts.items())
            send_ntfy_alert(
                title="New Bookshops Synced to Dashboard",
                message=f"Cataloged initial stock for: {summary}. Future drops will alert individually!",
                book_url="https://goldsborobooks.com/collections/new-releases",
            )

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
    
