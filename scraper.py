from common import *  # noqa: F401,F403
from shops import *  # noqa: F401,F403
from extra import fetch_issue_shops


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
    baseline_iso = (now_iso - timedelta(days=30)).isoformat()
    seen_shops = {i.split("::")[0] for i in seen_ids}
    cutoff_time = now_iso - timedelta(days=RETENTION_DAYS)

    # 1. Scrape currently active books from websites
    current_scraped_books = []
    shop_status = {}
    failed_shops = set()
    def register(shop_name, books, keep_earliest=False):
        shop_status[shop_name] = shop_status.get(shop_name) or f"{len(books)} items"
        for pos, b in enumerate(books):
            bid = b["id"]
            a = (b.get("author") or "").strip()
            if not a or a == shop_name:
                a = derive_author(b.get("title", ""), b.pop("_text", ""))
            b.pop("_text", None)
            b["author"] = a
            if a and not b.get("vip"):
                b["vip"] = matches_watchlist(f"{b.get('title', '')} {a}")
            # Preserve original discovery timestamp if we've seen it before, else use now
            if bid in existing_books_map:
                b["discovered_at"] = existing_books_map[bid].get("discovered_at", now_iso.isoformat())
            else:
                b["discovered_at"] = now_iso.isoformat()
            # Shops with no listing date: keep any estimate we already made; otherwise spread the shop's
            # existing stock (in the shop's own page order, assumed newest first) over the last 3-30 days so
            # shops mix through the page. Genuinely new arrivals later are stamped with the time first seen.
            if not b.get("listed_at"):
                old = existing_books_map.get(bid, {})
                if old.get("listed_at") and old.get("est") in ("pos", "seen"):
                    b["listed_at"], b["est"] = old["listed_at"], old["est"]
                elif bid in seen_ids or shop_name not in seen_shops:
                    frac = pos / max(len(books), 1)
                    b["listed_at"] = (now_iso - timedelta(days=3 + 27 * frac)).isoformat()
                    b["est"] = "pos"
                else:
                    b["listed_at"], b["est"] = b["discovered_at"], "seen"
            current_scraped_books.append(b)

    for shop in BOOKSHOPS:
        try:
            print(f"Checking {shop['name']}...")
            kind = shop["type"]
            books = scrape_shopify(shop) if kind == "shopify" else scrape_shopify_all(shop) if kind == "shopify_all" else scrape_wix(shop) if kind == "wix" else scrape_lrb(shop) if kind == "lrb" else scrape_station(shop) if kind == "station" else scrape_woocommerce(shop) if kind == "woocommerce" else scrape_squarespace(shop) if kind == "squarespace" else scrape_html(shop)
            print(f" -> Found {len(books)} available signed editions.")
            register(shop["name"], books)
        except Exception as e:
            print(f" -> Skipped {shop['name']} due to error: {e}")
            shop_status[shop["name"]] = f"ERROR: {str(e)[:120]}"
            failed_shops.add(shop["name"])

    # Shops GitHub cannot read directly: listings posted to a public issue by another process.
    try:
        for name, (books, status, ok) in fetch_issue_shops(matches_watchlist, to_utc_iso).items():
            direct_ok = name in shop_status and not str(shop_status[name]).startswith("ERROR")
            if direct_ok:
                continue  # we could read this shop ourselves this run; no need for the feed copy
            print(f"Feed {name}: {status}")
            shop_status[name] = status
            if ok:
                failed_shops.discard(name)
                register(name, books, keep_earliest=True)
            else:
                failed_shops.add(name)
    except Exception as e:
        print(f"Issue feed skipped: {e}")

    # 2. If a shop could not be read this run, keep its recent items so they do not vanish from the page.
    #    Items from shops that were read fine are NOT kept once they disappear (sold out / delisted).
    master_books_map = {b["id"]: b for b in current_scraped_books}
    for bid, book in existing_books_map.items():
        if book.get("shop") not in failed_shops or bid in master_books_map:
            continue
        disc_str = book.get("discovered_at")
        if disc_str:
            try:
                if datetime.fromisoformat(disc_str) >= cutoff_time:
                    book.setdefault("listed_at", disc_str)
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
                "shop_status": shop_status,
                "books": final_books_list,
            },
            f,
            indent=2,
        )


if __name__ == "__main__":
    main()
