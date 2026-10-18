"""Merge listings for shops GitHub cannot read (Waterstones, Hatchards, Foyles, Linghams, Booka, Porty).

Another process posts one JSON comment per shop on a public GitHub issue. This reads the latest complete
batch per shop. Any problem (issue missing, bad JSON, rate limit) is reported in shop_status and never
stops the main run.
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone

import requests

REPO = "waron-tech/signed-book-tracker"
ISSUE_NUMBER = os.environ.get("ISSUE_FEED_NUMBER", "").strip() or "1"
RECENT_DAYS = 365
FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def _get_comments():
    """Newest comments only: the issue keeps growing, so read the last two pages of 100."""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "signed-book-tracker"}
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"https://api.github.com/repos/{REPO}/issues/{ISSUE_NUMBER}/comments"
    first = requests.get(url, params={"per_page": 100, "page": 1}, headers=headers, timeout=25)
    first.raise_for_status()
    last_page = 1
    m = re.search(r'[?&]page=(\d+)[^>]*>;\s*rel="last"', first.headers.get("Link", ""))
    if m:
        last_page = int(m.group(1))
    if last_page == 1:
        return first.json()
    out = []
    for page in sorted({max(1, last_page - 1), last_page}):
        r = requests.get(url, params={"per_page": 100, "page": page}, headers=headers, timeout=25)
        r.raise_for_status()
        out.extend(r.json())
    return out


def _payloads(comments):
    for c in comments:
        for m in FENCE.finditer(c.get("body") or ""):
            try:
                data = json.loads(m.group(1))
            except Exception:
                continue
            if isinstance(data, dict) and data.get("shop"):
                h = re.search(r"part\s+(\d+)\s*/\s*(\d+)", (c.get("body") or "").split("\n", 1)[0])
                if h and "part" not in data:
                    data["part"], data["parts"] = int(h.group(1)), int(h.group(2))
                yield c.get("created_at") or "", data


def fetch_issue_shops(matches_watchlist, to_utc_iso):
    """Returns {shop_name: (books, status_text, ok)}. Never raises."""
    if not ISSUE_NUMBER:
        return {}
    try:
        comments = _get_comments()
    except Exception as e:
        return {"Issue feed": ([], f"ERROR: {str(e)[:120]}", False)}

    by_shop = {}
    for created, p in _payloads(comments):
        by_shop.setdefault(str(p["shop"]), []).append((created, p))

    cutoff = (datetime.now(timezone.utc) - timedelta(days=RECENT_DAYS)).isoformat()
    result = {}
    for shop, entries in by_shop.items():
        try:
            by_batch = {}
            for created, p in entries:
                by_batch.setdefault(str(p.get("batch")), []).append((created, p))
            chosen = None
            problem = ""
            for batch_id, group in sorted(by_batch.items(), key=lambda kv: max(c for c, _ in kv[1]), reverse=True):
                newest = max(group, key=lambda e: e[0])[1]
                if newest.get("status") == "error":
                    problem = f"ERROR (feed): {str(newest.get('error', ''))[:100]}"
                    break
                want = int(newest.get("parts", 1) or 1)
                have = {int(p.get("part", 1) or 1) for _, p in group if p.get("status") != "error"}
                if have >= set(range(1, want + 1)):
                    chosen = (batch_id, group)
                    break
                problem = f"ERROR (feed): incomplete batch {len(have)}/{want}"
            if not chosen:
                result[shop] = ([], problem or "ERROR (feed): no complete batch", False)
                continue
            batch_id, group = chosen
            parts = [p for _, p in group]
            latest = max(group, key=lambda e: e[0])[1]
            latest_created = max(c for c, _ in group)
            books, seen = [], set()
            for p in parts:
                for it in p.get("items", []) or []:
                    url = str(it.get("url", "")).strip()
                    title = str(it.get("title", "")).strip()
                    if not url.startswith("http") or not title or url in seen:
                        continue
                    seen.add(url)
                    # The feed's listed_at is when the watcher first saw the item, not the shop's date,
                    # so ignore it: scraper.py applies its own first-seen/baseline rule to undated items.
                    listed = ""
                    price = str(it.get("price", "")).strip() or "See site"
                    books.append({
                        "id": f"{shop}::{url}",
                        "title": title,
                        "author": str(it.get("author") or shop),
                        "price": price if price.startswith("£") or price == "See site" else f"£{price}",
                        "shop": shop,
                        "url": url,
                        "image": str(it.get("image", "") or ""),
                        "vip": matches_watchlist(f"{title} {it.get('author', '')}"),
                        "listed_at": listed,
                    })
            age = f" (as of {latest.get('fetched_at') or latest_created})"
            result[shop] = (books, f"{len(books)} items via feed{age}", True)
        except Exception as e:
            result[shop] = ([], f"ERROR (feed): {str(e)[:100]}", False)
    return result
