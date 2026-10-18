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
        "name": "Backstory London (Signed & Dedicated)",
        "type": "shopify_all",
        "url": "https://backstory.london/collections/signed-dedicated/products.json",
        "base_product_url": "https://backstory.london/products/",
        "require_signed_keyword": False,
        "max_pages": 6,
    },
    {
        "name": "Backstory London (Exclusives)",
        "type": "shopify_all",
        "url": "https://backstory.london/collections/signed-exclusive-editions/products.json",
        "base_product_url": "https://backstory.london/products/",
        "require_signed_keyword": False,
        "max_pages": 6,
    },
    {
        "name": "British Library Shop",
        "type": "shopify_all",
        "plain_headers": True,  # this shop returns nothing when Accept-Language is sent
        "url": "https://shop.bl.uk/collections/signed-copies/products.json",
        "base_product_url": "https://shop.bl.uk/products/",
        "require_signed_keyword": False,
        "max_pages": 6,
    },
    {
        "name": "Cleeve Bookshop",
        "type": "shopify_all",
        "url": "https://thecleevebookshop.co.uk/collections/signed-first-edition-hardback-fiction/products.json",
        "base_product_url": "https://thecleevebookshop.co.uk/products/",
        "require_signed_keyword": False,
        "max_pages": 6,
    },
    {
        "name": "Ivybridge Bookshop",
        "type": "shopify_all",
        "url": "https://www.ivybridgebookshop.com/collections/indie-editions-and-signed-by-the-author/products.json",
        "base_product_url": "https://www.ivybridgebookshop.com/products/",
        "require_signed_keyword": False,
        "max_pages": 6,
    },
    {
        "name": "Perkology Books",
        "type": "shopify_all",
        "url": "https://www.perkology.co.uk/collections/signed-editions/products.json",
        "base_product_url": "https://www.perkology.co.uk/products/",
        "require_signed_keyword": False,
        "max_pages": 6,
    },
    {
        "name": "Bookstation (Ireland)",
        "type": "shopify_all",
        "url": "https://bookstation.ie/collections/signed-books/products.json",
        "base_product_url": "https://bookstation.ie/products/",
        "require_signed_keyword": False,
        "max_pages": 6,
    },
    {
        "name": "Biggles Stones",
        "type": "woocommerce",
        "url": "https://www.bigglestones.com/category/highlights/signed-exclusives/",
        "site": "https://www.bigglestones.com",
        "category_slug": "signed-exclusives",
    },
    {
        "name": "London Review Bookshop",
        "type": "lrb",
        "url": "https://www.londonreviewbookshop.co.uk/booklists/signed-editions",
    },
    {
        "name": "Little Apple Bookshop",
        "type": "wix",
        "url": "https://www.littleapplebookshop.co.uk/signed",
    },
    {
        "name": "Hewson Books",
        "type": "wix",
        "url": "https://www.hewsonbooks.co.uk/signed-books",
    },
    {
        "name": "Station Books",
        "type": "station",
        "url": "https://stationbooks.co.uk/shop/editions/signed/",
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
        resp = requests.get(shop["url"], params={"limit": 250, "page": page}, headers=({"User-Agent": HEADERS["User-Agent"]} if shop.get("plain_headers") else HEADERS), timeout=20)
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
    site = shop.get("site") or shop["url"].split("/category/")[0]
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


def scrape_wix(shop: dict) -> list[dict]:
    """Wix Stores category page: product data is embedded as JSON in the HTML (no dates available)."""
    base = shop["url"].split("/", 3)[0] + "//" + shop["url"].split("/")[2]
    found, seen_parts = [], set()
    for page in range(1, 15):
        resp = requests.get(shop["url"], params={"page": page}, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        t = resp.text
        new_on_page = 0
        for m in re.finditer(r'"urlPart":"([^"]+)"', t):
            part = m.group(1)
            if part in seen_parts:
                continue
            tail = t[m.end(): m.end() + 900]
            head = t[max(0, m.start() - 2500): m.start()]
            name = re.search(r'"name":"((?:[^"\\]|\\.)*)"', tail)
            price = re.search(r'"formattedPrice":"([^"]*)"', tail) or re.search(r'"formattedPrice":"([^"]*)"', head[-700:])
            stock = re.findall(r'"isInStock":(true|false)', head[-300:])
            if not name or not price:
                continue
            seen_parts.add(part)
            new_on_page += 1
            if stock and stock[-1] == "false":
                continue
            title = re.sub(r"\s+", " ", json.loads('"' + name.group(1) + '"')).strip()
            imgs = re.findall(r'"fullUrl":"([^"]+)"', head) or re.findall(r'"fullUrl":"([^"]+)"', t[m.end(): m.end() + 3000])
            image = imgs[-1].replace("\\/", "/").split("/v1/")[0] if imgs else ""
            url = f"{base}/product-page/{part}"
            found.append({
                "id": f"{shop['name']}::{url}",
                "title": title,
                "author": shop["name"],
                "price": price.group(1) or "See site",
                "shop": shop["name"],
                "url": url,
                "image": image,
                "vip": matches_watchlist(title),
                "listed_at": "",
            })
        if new_on_page == 0:
            break
    return found


def scrape_lrb(shop: dict) -> list[dict]:
    """London Review Bookshop signed list (server-rendered cards)."""
    base = "https://www.londonreviewbookshop.co.uk"
    found, seen_urls = [], set()
    for page in range(1, 10):
        resp = requests.get(shop["url"], params={"page": page}, headers=HEADERS, timeout=25)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select(".collection-inline-book")
        if not cards:
            break
        added = 0
        for c in cards:
            a = c.select_one("a.book-preview--titles")
            if not a or not a.get("href"):
                continue
            url = urljoin(base, a["href"])
            if url in seen_urls:
                continue
            seen_urls.add(url)
            added += 1
            t_el = c.select_one("[itemprop=name]")
            au_el = c.select_one("[itemprop=author]")
            title = t_el.get_text(" ", strip=True) if t_el else ""
            author = au_el.get_text(" ", strip=True) if au_el else ""
            data = c.select_one(".ee-book-data")
            price = f"£{float(data['data-price']):.2f}" if data and data.get("data-price") else "See site"
            img = c.select_one("img")
            image = (img.get("data-srcimg") or "") if img else ""
            if not title or "out of stock" in c.get_text(" ").lower():
                continue
            found.append({
                "id": f"{shop['name']}::{url}",
                "title": f"{title} - {author}" if author else title,
                "author": author or shop["name"],
                "price": price,
                "shop": shop["name"],
                "url": url,
                "image": image,
                "vip": matches_watchlist(f"{title} {author}"),
                "listed_at": "",
            })
        if added == 0:
            break
    return found


def scrape_station(shop: dict) -> list[dict]:
    """Station Books signed list (server-rendered cards, no pagination seen)."""
    base = "https://stationbooks.co.uk"
    resp = requests.get(shop["url"], headers=HEADERS, timeout=25)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    found = []
    for c in soup.select("li.product-card"):
        a = c.select_one("a[href]")
        h3 = c.select_one("h3")
        if not a or not h3 or c.select_one(".oos"):
            continue
        url = urljoin(base, a["href"])
        title = h3.get_text(" ", strip=True).replace(" | Preorder", "")
        img = c.select_one("img")
        price_el = c.select_one(".price")
        found.append({
            "id": f"{shop['name']}::{url}",
            "title": title,
            "author": shop["name"],
            "price": extract_uk_price(price_el.get_text(" ", strip=True)) if price_el else "See site",
            "shop": shop["name"],
            "url": url,
            "image": img.get("src", "") if img else "",
            "vip": matches_watchlist(title),
            "listed_at": "",
        })
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
                "id
