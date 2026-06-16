#!/usr/bin/env python3
"""
Automotive news parser — fetches RSS feeds, classifies BMW vs general auto,
and writes two JSON files: data/bmw-news.json and data/auto-news.json.

Runs hourly via GitHub Actions.

Sources were hand-tested for:
  - Working RSS endpoint (HTTP 200 with valid feed)
  - Quality photos embedded in feed (media:content / enclosures / <img>)
  - Recent, relevant automotive content
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import feedparser
import requests

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("nws")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
HTTP_TIMEOUT = 20
MAX_ITEMS_PER_FEED = 30  # cap so one noisy feed can't dominate
MAX_AGE_DAYS = 7         # only keep items newer than this

# ─────────────────────────────────────────────────────────────────────────────
# Curated source list — hand-tested 2025-06
# Each source has a quality image in its RSS items (media:content / enclosure / <img>)
# ─────────────────────────────────────────────────────────────────────────────
SOURCES: list[dict[str, str]] = [
    # ── BMW-specific (high signal) ────────────────────────────────────────────
    {"name": "BMW Blog",          "url": "https://bmwblog.com/feed/",                       "category": "bmw"},
    {"name": "BMW Blog M",        "url": "https://bmwblog.com/category/bmw-m/feed/",        "category": "bmw"},
    {"name": "BMW Blog i",        "url": "https://bmwblog.com/category/bmw-i/feed/",        "category": "bmw"},
    {"name": "BimmerFile",        "url": "https://bimmerfile.com/feed/",                    "category": "bmw"},

    # ── General automotive (broad world coverage) ─────────────────────────────
    {"name": "CarScoops",         "url": "https://www.carscoops.com/feed/",                 "category": "auto"},
    {"name": "Car and Driver",    "url": "https://www.caranddriver.com/rss/all.xml",        "category": "auto"},
    {"name": "Autocar",           "url": "https://www.autocar.co.uk/rss",                   "category": "auto"},
    {"name": "AutoExpress",       "url": "https://www.autoexpress.co.uk/rss",               "category": "auto"},
    {"name": "CarExpert",         "url": "https://carexpert.com.au/feed/",                  "category": "auto"},
    {"name": "Jalopnik",          "url": "https://jalopnik.com/rss",                        "category": "auto"},
    {"name": "The Drive",         "url": "https://www.thedrive.com/feed",                   "category": "auto"},
    {"name": "Electrek",          "url": "https://electrek.co/feed/",                       "category": "auto"},
    {"name": "InsideEVs",         "url": "https://insideevs.com/feed/",                     "category": "auto"},
    {"name": "Motorious",         "url": "https://motorious.com/feed/",                     "category": "auto"},
    {"name": "GM Authority",      "url": "https://gmauthority.com/blog/feed/",              "category": "auto"},
]

# ─────────────────────────────────────────────────────────────────────────────
# BMW classification keywords
#
# Two tiers:
#   STRONG  — always indicate BMW (exact-word match required, e.g. "bmw", "bimmer")
#   MODEL   — BMW model codes; matched with strict word boundary so we don't
#             confuse "ix" with "six", "x5" with "EX5", "s63" with "AMG S63" etc.
#
# An item is BMW-relevant if it has >=1 STRONG match OR >=2 distinct MODEL matches
# (the latter catches "M3 G80" / "X5 M Competition" without an explicit "BMW").
# ─────────────────────────────────────────────────────────────────────────────
BMW_STRONG_KEYWORDS: list[str] = [
    "bmw", "bimmer", "beemer", "beamer",
    "бмв", "баварски",
    "bmw motorrad", "bmw m", "bmw i",
    "alpina",  # BMW subsidiary since 2024
    "neue klasse", "neueklasse",
    "ring taxi",
    "bimmercode", "ista",  # BMW-specific software
]

# Match model codes with strict word boundaries to avoid false positives.
# (?<![A-Za-z0-9]) ensures the match is NOT preceded by a letter/digit
# (?![A-Za-z0-9])  ensures the match is NOT followed by a letter/digit
BMW_MODEL_PATTERNS: list[re.Pattern] = [
    re.compile(r"(?<![A-Za-z0-9])M(?:Power|Performance|Division)(?![A-Za-z0-9])", re.I),
    re.compile(r"(?<![A-Za-z0-9])M[2-8](?![A-Za-z0-9])"),          # M2..M8
    re.compile(r"(?<![A-Za-z0-9])XM(?![A-Za-z0-9])"),              # XM (BMW XM SUV)
    re.compile(r"(?<![A-Za-z0-9])X[1-7](?![A-Za-z0-9])"),          # X1..X7
    re.compile(r"(?<![A-Za-z0-9])iX[1-3]?(?![A-Za-z0-9])"),        # iX, iX1..iX3
    re.compile(r"(?<![A-Za-z0-9])i[3-8](?![A-Za-z0-9])"),          # i3..i8
    re.compile(r"(?<![A-Za-z0-9])(?:G20|G80|G82|G87|F90|G60|G70|G99|G30|G11|F30|F80|F82|F87|E30|E36|E46|E39|E60|F10|F15|G05|F25|G01)(?![A-Za-z0-9])"),
    re.compile(r"(?<![A-Za-z0-9])(?:N55|B58|S58|S63|B48|S68|S55|N52|S65|B38|B46)(?![A-Za-z0-9])"),
    re.compile(r"(?<![A-Za-z0-9])xDrive(?![A-Za-z0-9])", re.I),
    re.compile(r"(?<![A-Za-z0-9])(?:Valvetronic|VANOS)(?![A-Za-z0-9])", re.I),
    re.compile(r"(?<![A-Za-z0-9])(?:Nürburgring|Nurburgring)(?![A-Za-z0-9])"),
    re.compile(r"(?<![A-Za-z0-9])[1-8]\s+series(?![A-Za-z0-9])", re.I),  # "3 series", "5 series"
]


def is_bmw_relevant(title: str, summary: str) -> bool:
    """Return True if the item is BMW-relevant.

    Tier 1: any STRONG keyword in title+summary → True
    Tier 2: >=2 distinct MODEL pattern matches → True
    """
    text = f"{title} {summary}"
    text_lower = text.lower()
    # Tier 1: STRONG keywords — use word boundary so "ista" doesn't match "assistant"
    for kw in BMW_STRONG_KEYWORDS:
        # \b works for English; for Cyrillic we fall back to substring (Python \b
        # only treats ASCII word chars as word chars by default).
        if re.search(r"\b" + re.escape(kw) + r"\b", text, re.IGNORECASE):
            return True
        # Substring fallback for Cyrillic keywords
        if any(ord(c) > 127 for c in kw) and kw.lower() in text_lower:
            return True
    # Tier 2: count distinct model pattern matches
    distinct_matches: set[str] = set()
    for pat in BMW_MODEL_PATTERNS:
        for m in pat.finditer(text):
            distinct_matches.add(m.group(0).lower())
    return len(distinct_matches) >= 2

# Blocklist — non-auto or noise we never want
BLOCKLIST: list[str] = [
    "lada", "лада", "уаз", "uaz", "газ", "volga",
    "kia", "daewoo",
    "трактор", "комбайн",
    "porn", "casino", "viagra",
]


# ─────────────────────────────────────────────────────────────────────────────
# HTTP helpers
# ─────────────────────────────────────────────────────────────────────────────
def fetch_url(url: str) -> tuple[int | None, bytes | None, str | None]:
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml, text/xml, */*"}, timeout=HTTP_TIMEOUT)
        return r.status_code, r.content, None
    except Exception as e:
        return None, None, str(e)


def extract_image(entry: Any) -> str | None:
    """Try every standard RSS image location."""
    # 1. enclosures
    for enc in getattr(entry, "enclosures", []) or []:
        href = enc.get("href", "")
        if href:
            t = enc.get("type", "").lower()
            if t.startswith("image") or any(href.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif")):
                return href
    # 2. media_content
    for m in getattr(entry, "media_content", []) or []:
        url = m.get("url", "")
        if url:
            return url
    # 3. media_thumbnail
    for m in getattr(entry, "media_thumbnail", []) or []:
        url = m.get("url", "")
        if url:
            return url
    # 4. <img> in summary/content
    for field in ("summary", "description", "content"):
        val = getattr(entry, field, None)
        if not val:
            continue
        if isinstance(val, list) and val:
            val = val[0].get("value", "")
        m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', str(val))
        if m:
            return m.group(1)
    return None


def strip_html(s: str) -> str:
    """Remove HTML tags, decode entities, collapse whitespace."""
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def parse_date(entry: Any) -> str:
    """Return ISO-8601 UTC string or '' if unknown."""
    for field in ("published_parsed", "updated_parsed", "created_parsed"):
        t = getattr(entry, field, None)
        if t:
            try:
                dt = datetime(*t[:6], tzinfo=timezone.utc)
                return dt.isoformat()
            except Exception:
                continue
    # fall back to string fields
    for field in ("published", "updated", "date"):
        val = getattr(entry, field, "")
        if val:
            try:
                t = feedparser._parse_date(val)
                if t:
                    dt = datetime(*t[:6], tzinfo=timezone.utc)
                    return dt.isoformat()
            except Exception:
                continue
    return ""


def item_id(url: str, title: str) -> str:
    raw = (url or "") + "|" + (title or "")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ─────────────────────────────────────────────────────────────────────────────
# Fetching
# ─────────────────────────────────────────────────────────────────────────────
def fetch_one(source: dict[str, str]) -> list[dict[str, Any]]:
    """Fetch and parse a single RSS source into normalized items."""
    name = source["name"]
    url = source["url"]
    category = source["category"]
    log.info("Fetching %s (%s)", name, url)
    status, content, err = fetch_url(url)
    if status != 200 or content is None:
        log.warning("  ✗ %s: HTTP %s (%s)", name, status, err or "")
        return []
    try:
        feed = feedparser.parse(content)
    except Exception as e:
        log.warning("  ✗ %s: parse error %s", name, e)
        return []
    if feed.bozo and not feed.entries:
        log.warning("  ✗ %s: malformed feed (%s)", name, getattr(feed, "bozo_exception", "?"))
        return []

    parsed_url = urlparse(url)
    base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
    items: list[dict[str, Any]] = []
    for entry in feed.entries[:MAX_ITEMS_PER_FEED]:
        title = strip_html(getattr(entry, "title", ""))
        if not title:
            continue
        # Get the most descriptive body text
        full_text = ""
        c = getattr(entry, "content", None)
        if c:
            if isinstance(c, list) and c:
                full_text = c[0].get("value", "")
            elif isinstance(c, str):
                full_text = c
        summary = strip_html(getattr(entry, "summary", "") or full_text)
        if not summary and full_text:
            summary = strip_html(full_text)
        # Cap summary at 600 chars (full article body would be too much for JSON)
        if len(summary) > 600:
            summary = summary[:597].rsplit(" ", 1)[0] + "…"

        link = getattr(entry, "link", "") or ""
        image = extract_image(entry)
        published = parse_date(entry)

        # Blocklist check on combined text
        combined = f"{title} {summary}".lower()
        if any(bl in combined for bl in BLOCKLIST):
            continue

        # Determine BMW relevance (used by classifier later, but pre-compute)
        is_bmw = is_bmw_relevant(title, summary)

        items.append({
            "id": item_id(link, title),
            "title": title,
            "summary": summary,
            "url": link,
            "image": image or "",
            "source": name,
            "source_url": base_url,
            "category": category,
            "published": published,
            "is_bmw": is_bmw,
        })

    log.info("  ✓ %s: %d items", name, len(items))
    return items


def fetch_all(sources: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Fetch all sources in parallel and return aggregated items."""
    all_items: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fetch_one, s): s["name"] for s in sources}
        for fut in as_completed(futures):
            try:
                all_items.extend(fut.result())
            except Exception as e:
                log.warning("Source %s crashed: %s", futures[fut], e)
    return all_items


# ─────────────────────────────────────────────────────────────────────────────
# Filtering, dedup, sort
# ─────────────────────────────────────────────────────────────────────────────
def is_recent(published_iso: str, max_age_days: int = MAX_AGE_DAYS) -> bool:
    """True if published is within the last `max_age_days` (or unknown)."""
    if not published_iso:
        return True  # keep items with unknown date — feed may not include one
    try:
        dt = datetime.fromisoformat(published_iso.replace("Z", "+00:00"))
    except Exception:
        return True
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    return dt >= cutoff


def dedup(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate by id (URL+title hash). When collisions occur, prefer the item
    that already has an image."""
    by_id: dict[str, dict[str, Any]] = {}
    for it in items:
        existing = by_id.get(it["id"])
        if existing is None:
            by_id[it["id"]] = it
            continue
        # Prefer the one with an image, else the one with a longer summary
        if not existing["image"] and it["image"]:
            by_id[it["id"]] = it
        elif existing["image"] and not it["image"]:
            continue
        elif len(it["summary"]) > len(existing["summary"]):
            by_id[it["id"]] = it
    return list(by_id.values())


def sort_newest_first(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort by published desc. Items without date go to the end."""
    def key(it: dict[str, Any]) -> tuple[int, str]:
        p = it.get("published", "")
        return (0 if p else 1, p or "")
    return sorted(items, key=key, reverse=True)


# ─────────────────────────────────────────────────────────────────────────────
# Output
# ─────────────────────────────────────────────────────────────────────────────
def build_output(items: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    """Wrap the items list with metadata."""
    sources_used = sorted({it["source"] for it in items})
    return {
        "kind": kind,  # "bmw" or "auto"
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_at_human": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "total_items": len(items),
        "sources_used": sources_used,
        "sources_count": len(sources_used),
        "items": items,
    }


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    log.info("Wrote %s (%d items, %d bytes)",
             path, data["total_items"], path.stat().st_size)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def main() -> int:
    log.info("=" * 70)
    log.info("Automotive news parser — starting run")
    log.info("=" * 70)

    repo_root = Path(__file__).resolve().parent
    data_dir = repo_root / "data"

    # 1. Fetch
    raw = fetch_all(SOURCES)
    log.info("Total raw items fetched: %d", len(raw))
    if not raw:
        log.error("No items fetched from any source — aborting")
        return 1

    # 2. Dedup
    deduped = dedup(raw)
    log.info("After dedup: %d items", len(deduped))

    # 3. Recency filter (keep recent + unknown-date)
    recent = [it for it in deduped if is_recent(it["published"])]
    log.info("After recency filter (%dd): %d items", MAX_AGE_DAYS, len(recent))

    # 4. Split
    bmw_items = [it for it in recent if it["is_bmw"]]
    # Auto file = all automotive news EXCEPT pure BMW-specific items
    # (we still keep BMW items mentioned in general auto sources, but for clarity
    #  we exclude any item where source.category == "bmw" from the auto file,
    #  since those are already in the BMW file).
    auto_items = [it for it in recent if it["category"] != "bmw"]

    # Re-dedup auto vs bmw: an item from a general source may be is_bmw=True.
    # In that case it appears in BOTH files (BMW-relevant AND auto) — that's fine
    # and arguably desirable. We only strip pure-BMW-source items from auto.

    # 5. Prefer items with images, but keep all (image field may be "")
    #    — the user explicitly wants quality photos. We sort items WITH image first.
    def image_first_key(it: dict[str, Any]) -> tuple[int, str]:
        has_img = 0 if it.get("image") else 1
        return (has_img, "")
    bmw_items_sorted = sort_newest_first(
        sorted(bmw_items, key=image_first_key)
    )
    auto_items_sorted = sort_newest_first(
        sorted(auto_items, key=image_first_key)
    )

    # 6. Trim to reasonable cap (top 100 each)
    bmw_items_sorted = bmw_items_sorted[:100]
    auto_items_sorted = auto_items_sorted[:150]

    # 7. Drop helper fields that were internal-only
    def clean(it: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": it["id"],
            "title": it["title"],
            "summary": it["summary"],
            "url": it["url"],
            "image": it["image"],
            "source": it["source"],
            "source_url": it["source_url"],
            "published": it["published"],
        }

    bmw_clean = [clean(it) for it in bmw_items_sorted]
    auto_clean = [clean(it) for it in auto_items_sorted]

    # 8. Write outputs
    write_json(data_dir / "bmw-news.json", build_output(bmw_clean, "bmw"))
    write_json(data_dir / "auto-news.json", build_output(auto_clean, "auto"))

    log.info("=" * 70)
    log.info("Run complete. BMW=%d, Auto=%d", len(bmw_clean), len(auto_clean))
    log.info("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
