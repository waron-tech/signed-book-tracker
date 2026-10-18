import json
import os
import html
import re
import unicodedata
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

# 1. CONFIGURATION
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
SEEN_FILE = "seen_books.json"
FEED_FILE = "books_feed.json"
RECENT_DAYS = 365  # "new signed copies" = listed on the shop in the last 12 months (items with no date are kept)
RETENTION_DAYS = 30  # Keeps books in your dashboard feed for 30 days

PRIORITY_WATCHLIST = [
    # Living authors from Aaron's library (most-held first), plus stated targets.
    "Haruki Murakami", "Barbara Kingsolver", "Robert Caro", "Kazuo Ishiguro", "Pat Barker",
    "Madeline Miller", "Salman Rushdie", "Alan Bennett", "M.L. Rio", "M L Rio", "Donna Tartt",
    "Hiromi Kawakami", "Curtis Sittenfeld", "Douglas Stuart", "Bernardine Evaristo",
    "Margaret Atwood", "Bret Easton Ellis", "J.M. Coetzee", "JM Coetzee", "Don DeLillo",
    "Deborah Levy", "Tim Key", "Arundhati Roy", "Elif Shafak", "Max Porter", "Ottessa Moshfegh",
    "Colson Whitehead", "Jonathan Franzen", "John Banville", "Claudia Roden", "Madhur Jaffrey",
    "John Cooper Clarke", "Nina Mingya Powles", "Olga Tokarczuk", "Thom Yorke",
    "Hanya Yanagihara", "Ben Lerner", "Edward St Aubyn", "Patricia Lockwood",
    "Toshikazu Kawaguchi", "Stephen King",
]

BOOKSHOPS = [
    {
        "name": "Goldsboro Books",
        "type": "shopify_all",
        "url": "https://goldsborobooks.com/products.json",
        "base_product_url": "https://goldsborobooks.com/products/",
        "require_signed_keyword": True,
        "max_pages": 12,
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
        "type": "html",
        "url": "https://www.theportobellobookshop.com/collection-of/signed-dedicated-editions",
        "card_selector": "a.card",
        "title_selector": ".card-text.fw-bold:not(.text-primary)",
        "price_selector": ".text-primary",
        "link_selector": "a.card",
        "require_signed_keyword": False,
    },
    {
        "name": "Booka Bookshop",
        "type": "woocommerce",
        "url": "https://www.bookabookshop.co.uk/category/signed-copies/",
        "category_slug": "signed-copies",
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
        "name": "West End Lane Books",
        "type": "squarespace",
        "url": "https://www.welbooks.co.uk/shop/signed-editions",
        "card_selector": ".product-list-item",
        "title_selector": ".product-list-item-title",
        "price_selector": ".product-list-item-price",
        "link_selector": "a[href]",
        "require_signed_keyword": True,
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


def to_utc_iso(value) -> str:
    """Shop-supplied date (ISO string or epoch millis) -> UTC ISO string, or '' if unknown."""
    if not value:
        return ""
    try:
        if isinstance(value, (int, float)):
            dt = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return ""


def looks_signed(text: str) -> bool:
    t = text.lower()
    t = re.sub(r"\bun-?signed\b", "", t)
    return "signed" in t


def clean_header(text: str) -> str:
    return text.encode("latin-1", errors="ignore").decode("latin-1")


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = text.replace(".", "")
    return " " + re.sub(r"[^a-z0-9]+", " ", text).strip() + " "


_WATCH_NORMALISED = [_norm(n) for n in PRIORITY_WATCHLIST]


def matches_watchlist(text: str) -> bool:
    haystack = _norm(text)
    return any(name in haystack for name in _WATCH_NORMALISED)


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


def scrape_shopify_all(shop: dict) -> list[dict]:
    """Page through a shop-wide products.json (collections can be empty or renamed)."""
    products = []
    for page in range(1, shop.get("max_pages", 8) + 1):
        resp = requests.get(shop["url"], params={"limit": 250, "page": page}, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        batch = resp.json().get("products", [])
        if not batch:
            break
        products.extend(batch)
    return scrape_shopify(shop, products)


def scrape_shopify(shop: dict, products=None) -> list[dict]:
    if products is None:
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

        listed = to_utc_iso(prod.get("published_at") or prod.get("created_at"))
        if listed and listed < (datetime.now(timezone.utc) - timedelta(days=RECENT_DAYS)).isoformat():
            continue

        if shop.get("require_signed_keyword", False):
            if not looks_signed(f"{title} {body_html} {tags}"):
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
            "listed_at": listed,
        })
    return found


def scrape_woocommerce(shop: dict) -> list[dict]:
    """WooCommerce Store API (newest first) + WP REST for publish dates. Stops at the recency cut-off."""
    site = shop["url"].split("/category/")[0]
    cutoff = datetime.now(timezone.utc) - timedelta(days=RECENT_DAYS)
    found = []
    for page in range(1, 30):
        resp = requests.get(
            f"{site}/wp-json/wc/store/v1/products",
            params={"category": shop["category_slug"], "per_page": 100, "page": page, "orderby": "date", "order": "desc"},
            headers=HEADERS, timeout=25,
        )
        resp.raise_for_status()
        items = resp.json()
        if not items:
            break
        ids = [str(i["id"]) for i in items]
        dres = requests.get(
            f"{site}/wp-json/wp/v2/product",
            params={"include": ",".join(ids), "per_page": 100, "_fields": "id,date_gmt"},
            headers=HEADERS, timeout=25,
        )
        dres.raise_for_status()
        dates = {d["id"]: to_utc_iso(d.get("date_gmt")) for d in dres.json()}
        oldest_in_page = None
        for it in items:
            listed = dates.get(it["id"], "")
            if listed:
                oldest_in_page = listed if oldest_in_page is None else min(oldest_in_page, listed)
            if listed and listed < cutoff.isoformat():
                continue
            if not it.get("is_in_stock", True):
                continue
            title = html.unescape(it.get("name", "")).strip()
            blurb = re.sub(r"<[^>]+>", " ", f"{it.get('short_description', '')} {it.get('description', '')}")
            prices = it.get("prices") or {}
            try:
                price = f"£{int(prices.get('price', '0')) / 100:.2f}"
            except ValueError:
                price = "See site"
            images = it.get("images") or []
            found.append({
                "id": f"{shop['name']}::{it['permalink']}",
                "title": title,
                "author": shop["name"],
                "price": price,
                "shop": shop["name"],
                "url": it["permalink"],
                "image": images[0].get("src", "") if images else "",
                "vip": matches_watchlist(f"{title} {html.unescape(blurb)}"),
                "listed_at": listed,
            })
        if oldest_in_page and oldest_in_page < cutoff.isoformat():
            break
    return found


def scrape_squarespace(shop: dict) -> list[dict]:
    """Squarespace shop: ?format=json gives real publish dates, tags and stock."""
    base = shop["url"].split("/shop/")[0]
    found, offset = [], None
    for _ in range(25):
        params = {"format": "json"}
        if offset:
            params["offset"] = offset
        resp = requests.get(shop["url"], params=params, headers=HEADERS, timeout=25)
        resp.raise_for_status()
        data = resp.json()
        for it in data.get("items", []):
            title = (it.get("title") or "").strip()
            tags = " ".join(it.get("tags", []))
            if not looks_signed(f"{title} {tags}"):
                continue
            variants = (it.get("structuredContent") or {}).get("variants", [])
            if variants and not any(v.get("unlimited") or (v.get("qtyInStock") or 0) > 0 for v in variants):
                continue
            price = "See site"
            if variants:
                val = (variants[0].get("priceMoney") or {}).get("value")
                if val:
                    price = f"£{val}"
            listed = to_utc_iso(it.get("publishOn") or it.get("addedOn"))
            if listed and listed < (datetime.now(timezone.utc) - timedelta(days=RECENT_DAYS)).isoformat():
                continue
            url = base + it.get("fullUrl", "")
            found.append({
                "id": f"{shop['name']}::{url}",
                "title": title,
                "author": shop["name"],
                "price": price,
                "shop": shop["name"],
                "url": url,
                "image": it.get("assetUrl", ""),
                "vip": matches_watchlist(title),
                "listed_at": listed,
            })
        pg = data.get("pagination") or {}
        if not pg.get("nextPage") or not pg.get("nextPageOffset"):
            break
        offset = pg["nextPageOffset"]
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

        link_el = card if card.name == "a" and card.get("href") else card.select_one(shop["link_selector"])
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

        if shop.get("require_signed_keyword", False):
            classes = " ".join(card.get("class", []))
            if not looks_signed(f"{card_text} {title}") and "tag-signed" not in classes:
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
            "listed_at": "",
        })
    return found


def main():
    is_first_run = not os.path.exists(SEEN_FILE)
    seen_ids = set()
    if not is_first_run:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            seen_ids = set(json.load(f))

    # Load existing feed items with their discovery timestamps
    existing_books_map = {}
    if os.path.exists(FEED_FILE):
        try:
            with open(FEED_FILE, "r", encoding="utf-8") as f:
                old_data = json.load(f)
                for item in old_data.get("books", []):
                    existing_books_map[item["id"]] = item
        except Exception:
            pass

    now_iso = datetime.now(timezone.utc)
    cutoff_time = now_iso - timedelta(days=RETENTION_DAYS)

    # 1. Scrape currently active books from websites
    current_scraped_books = []
    for shop in BOOKSHOPS:
        try:
            print(f"Checking {shop['name']}...")
            kind = shop["type"]
            books = scrape_shopify(shop) if kind == "shopify" else scrape_shopify_all(shop) if kind == "shopify_all" else scrape_woocommerce(shop) if kind == "woocommerce" else scrape_squarespace(shop) if kind == "squarespace" else scrape_html(shop)
            print(f" -> Found {len(books)} available signed editions.")
            for b in books:
                bid = b["id"]
                # Preserve original discovery timestamp if we've seen it before, else use now
                if bid in existing_books_map:
                    b["discovered_at"] = existing_books_map[bid].get("discovered_at", now_iso.isoformat())
                else:
                    b["discovered_at"] = now_iso.isoformat()
                # Shops with no listing date (Coles, Porty): fall back to first-seen time.
                if not b.get("listed_at"):
                    b["listed_at"] = b["discovered_at"]
                current_scraped_books.append(b)
        except Exception as e:
            print(f" -> Skipped {shop['name']} due to error: {e}")

    # 2. Retain older books that were discovered within the last 30 days (even if currently off-site)
    master_books_map = {b["id"]: b for b in current_scraped_books}
    for bid, book in existing_books_map.items():
        disc_str = book.get("discovered_at")
        if disc_str:
            try:
                disc_time = datetime.fromisoformat(disc_str)
                # If discovered within 30 days, keep it in the feed
                if disc_time >= cutoff_time and bid not in master_books_map:
                    master_books_map[bid] = book
            except Exception:
                pass

    final_books_list = list(master_books_map.values())
    new_books = [b for b in current_scraped_books if b["id"] not in seen_ids]

    if is_first_run:
        for b in current_scraped_books:
            seen_ids.add(b["id"])
    else:
        # Only alert on recently listed items (a newly readable shop has a big back catalogue),
        # and cap alerts per run. Everything is still added to the dashboard feed.
        recent_cutoff = (now_iso - timedelta(hours=48)).isoformat()
        alert_books = [b for b in new_books if (b.get("listed_at") or "") >= recent_cutoff][:15]
        for b in new_books:
            if b not in alert_books:
                seen_ids.add(b["id"])
        for b in alert_books:
            prefix = "VIP DROP: " if b["vip"] else "New Signed Edition: "
            send_ntfy_alert(
                title=f"{prefix}{b['title']}",
                message=f"Shop: {b['shop']}\nPrice: {b['price']}\nTap below to order.",
                book_url=b["url"],
                cover_url=b.get("image", ""),
                is_priority=b["vip"],
            )
            seen_ids.add(b["id"])

    # Sort newest spotted books to the top
    final_books_list.sort(key=lambda x: x.get("listed_at") or x.get("discovered_at", ""), reverse=True)

    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(seen_ids)), f, indent=2)

    with open(FEED_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {
                "last_updated": now_iso.isoformat(),
                "total_available": len(final_books_list),
                "books": final_books_list,
            },
            f,
            indent=2,
        )


if __name__ == "__main__":
    main()
