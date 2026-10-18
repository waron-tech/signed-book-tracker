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
        "vendor_is_author": True,
        "url": "https://raremags.co.uk/collections/books/products.json?limit=50",
        "base_product_url": "https://raremags.co.uk/products/",
        "require_signed_keyword": True,
    },
    {
        "name": "Porty Books",
        "type": "html",
        "url": "https://www.theportobellobookshop.com/collection-of/signed-dedicated-editions",
        "card_selector": "a.card",
        "author_selector": ".card-text.mb-2.text-muted",
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
        "vendor_is_author": True,
        "url": "https://www.perkology.co.uk/collections/signed-editions/products.json",
        "base_product_url": "https://www.perkology.co.uk/products/",
        "require_signed_keyword": False,
        "max_pages": 6,
    },
    {
        "name": "Bookstation (Ireland)",
        "type": "shopify_all",
        "vendor_is_author": True,
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


_BAD_NAME = re.compile(
    r"signed|edition|hardback|paperback|pre-?order|publish|copy|copies|exclusive|indie|dedicated|sprayed|first|limited|special|book|the |"
    r"\d", re.I)
_NAME_TOKEN = re.compile(r"^(?:[A-Z][a-zA-Z'\u2019\-]*[a-z][a-zA-Z'\u2019\-]*|[A-Z]\.?|[A-Z]\.[A-Z]\.?|de|del|der|di|van|von|le|la|ten|bin|mac|st\.?)$")
_CLEAN = [
    r"\*+\s*signed[^*]*\*+", r"\(\s*signed[^)]*\)", r"\(\s*pre-?order\s*\)", r"\bpre-?\s?order( now)?\b",
    r"\bchristmas signed( & personalised)?\b", r",?\s*signed[^,]*,?\s*publishes.*$", r",?\s*publishes\s+\w+\s+\d{4}\s*$",
    r"\(\s*(hardback|paperback)\s*\).*$", r"\s+[\u2014\u2013]\s+[A-Z][^\u2014\u2013]*$",
]


def _valid_name(text: str) -> bool:
    text = (text or "").strip(" .,;:-\u2013\u2014")
    if not text or _BAD_NAME.search(text):
        return False
    people = re.split(r"\s*(?:,|&|\band\b)\s*", text)
    for person in people:
        toks = person.split()
        if not 2 <= len(toks) <= 4 or not all(_NAME_TOKEN.match(t) for t in toks):
            return False
    return len(people) <= 3


def derive_author(title: str, text: str = "") -> str:
    """Best-effort author from the title (or a short blurb). Returns '' rather than guessing."""
    t = title or ""
    for pat in _CLEAN:
        t = re.sub(pat, " ", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip(" -\u2013\u2014,")
    candidates = []
    m = re.search(r"\bby ([^,()|*:\u2013\u2014]+?)\s*(?:-\s*signed.*)?$", t, re.I)
    if m:
        candidates.append(m.group(1))
    m = re.match(r"^([^-\u2013]+?)\s+[-\u2013]\s+.+\(\s*SIGNED COPY\s*\)", title or "", re.I)
    if m:
        candidates.append(m.group(1))
    m = re.search(r"signed[^-\u2013]*[-\u2013]\s*([^()\-\u2013]+?)\s*(?:\(|$)", title or "", re.I)
    if m:
        candidates.append(m.group(1))
    m = re.search(r"\s[-\u2013\u2014]\s+([^-\u2013\u2014]+)$", t)
    if m:
        candidates.append(m.group(1))
    m = re.search(r"\bsigned\*?\s+([^()]+?)\s*(?:\(|$)", title or "", re.I)
    if m:
        candidates.append(m.group(1))
    for cand in candidates:
        if _valid_name(cand):
            return cand.strip(" .,;:-")
    m = re.search(r"\bby ([^\n.;()]+)", text or "")
    if m and _valid_name(m.group(1)):
        return m.group(1).strip(" .,;:-")
    return ""
