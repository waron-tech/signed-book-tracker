from common import *  # noqa: F401,F403


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
        display_title = title
        author_name = vendor if shop.get("vendor_is_author") and vendor else ""
        blurb = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body_html))[:400]

        found.append({
            "id": f"{shop['name']}::{handle}",
            "title": display_title,
            "author": author_name,
            "_text": blurb,
            "price": f"£{price}",
            "shop": shop["name"],
            "url": book_url,
            "image": cover_url,
            "vip": matches_watchlist(f"{display_title} {author_name}"),
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
                "author": "",
                "price": price,
                "shop": shop["name"],
                "url": it["permalink"],
                "image": images[0].get("src", "") if images else "",
                "vip": matches_watchlist(f"{title} {html.unescape(blurb)}"),
                "_text": re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", it.get("short_description", ""))))[:300],
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
                "author": "",
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
                "author": author,
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
            "author": "",
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
                "id": f"{shop['name']}::{url}",
                "title": title,
                "author": "",
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

        author_text = ""
        if shop.get("author_selector"):
            a_el = card.select_one(shop["author_selector"])
            if a_el:
                author_text = re.sub(r"\s*[-\u2013]\s*(hardback|paperback|hardcover|signed.*)$", "", a_el.get_text(" ", strip=True), flags=re.I)
        seen_urls.add(book_url)
        found.append({
            "id": f"{shop['name']}::{book_url}",
            "title": title,
            "author": author_text,
            "price": price,
            "shop": shop["name"],
            "url": book_url,
            "image": cover_url,
            "vip": matches_watchlist(f"{title} {card_text}"),
            "listed_at": "",
        })
    return found
