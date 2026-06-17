#!/usr/bin/env python3
"""
Automotive news parser — fetches RSS feeds, classifies BMW vs general auto,
and writes two JSON files: data/bmw-news.json and data/auto-news.json.

Runs hourly via GitHub Actions.

Sources were hand-tested for:
  - Working RSS endpoint (HTTP 200 with valid feed)
  - Quality photos embedded in feed (media:content / enclosures / <img>)
  - Recent, relevant automotive content
  - Multi-photo support: top-tier sources have `scrape_gallery=True` so the
    parser fetches the article page and extracts up to N additional photos
    (satisfying the "preferably with multiple photos" requirement).

All extracted images are filtered through is_garbage_image() to guarantee
no logos, icons, trackers, or placeholders ever land in the JSON output.
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
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse, parse_qs

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
HTTP_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
    "Accept-Language": "en-US,en;q=0.9",
}
HTML_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html, application/xhtml+xml, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

HTTP_TIMEOUT = 20
HTML_TIMEOUT = 15
MAX_ITEMS_PER_FEED = 30      # cap so one noisy feed can't dominate
MAX_AGE_DAYS = 7             # only keep items newer than this
MAX_GALLERY_SCRAPE_PER_SOURCE = 3   # how many article pages to fetch per gallery source
MAX_IMAGES_PER_ITEM = 6      # cap on the `images` array (incl. lead image)

# ─────────────────────────────────────────────────────────────────────────────
# Curated source list — hand-tested 2026-06
#
# Each source has a quality image in its RSS items (media:content / enclosure /
# <img>) and is verified to return HTTP 200 with recent automotive content.
#
# Sources marked `scrape_gallery=True` are top-tier sites where the article page
# contains a multi-photo gallery; for these, the parser fetches up to
# MAX_GALLERY_SCRAPE_PER_SOURCE articles and extracts additional photos.
# ─────────────────────────────────────────────────────────────────────────────
SOURCES: list[dict[str, Any]] = [
    # ── BMW-specific (high signal) ────────────────────────────────────────────
    {"name": "BMW Blog",          "url": "https://bmwblog.com/feed/",                       "category": "bmw"},
    {"name": "BMW Blog M",        "url": "https://bmwblog.com/category/bmw-m/feed/",        "category": "bmw"},
    {"name": "BMW Blog i",        "url": "https://bmwblog.com/category/bmw-i/feed/",        "category": "bmw"},
    {"name": "BMW Blog X",        "url": "https://bmwblog.com/category/bmw-x/feed/",        "category": "bmw"},
    {"name": "BMW Blog 3",        "url": "https://bmwblog.com/category/bmw-3-series/feed/", "category": "bmw"},
    {"name": "BMW Blog 5",        "url": "https://bmwblog.com/category/bmw-5-series/feed/", "category": "bmw"},
    {"name": "BMW Blog M2",       "url": "https://bmwblog.com/category/bmw-m2/feed/",       "category": "bmw"},
    {"name": "BMW Blog M3",       "url": "https://bmwblog.com/category/bmw-m3/feed/",       "category": "bmw"},
    {"name": "BMW Blog M4",       "url": "https://bmwblog.com/category/bmw-m4/feed/",       "category": "bmw"},
    {"name": "BMW Blog M5",       "url": "https://bmwblog.com/category/bmw-m5/feed/",       "category": "bmw"},
    {"name": "BMW Blog M8",       "url": "https://bmwblog.com/category/bmw-m8/feed/",       "category": "bmw"},
    {"name": "BMW Blog concepts", "url": "https://bmwblog.com/category/concepts/feed/",     "category": "bmw"},
    {"name": "BMW Blog Alpina",   "url": "https://bmwblog.com/tag/alpina/feed/",            "category": "bmw"},
    {"name": "BMW Blog Mini",     "url": "https://bmwblog.com/tag/mini/feed/",              "category": "bmw"},
    {"name": "BMW Blog X5",       "url": "https://bmwblog.com/tag/x5/feed/",                "category": "bmw"},
    {"name": "BMW Blog X7",       "url": "https://bmwblog.com/tag/x7/feed/",                "category": "bmw"},
    {"name": "BMW Blog XM",       "url": "https://bmwblog.com/tag/xm/feed/",                "category": "bmw"},
    {"name": "BimmerFile",        "url": "https://bimmerfile.com/feed/",                    "category": "bmw"},
    {"name": "BimmerToday DE",    "url": "https://www.bimmertoday.de/feed/",                "category": "bmw"},
    {"name": "Car and Driver BMW","url": "https://www.caranddriver.com/rss/bmw.xml",        "category": "bmw", "scrape_gallery": True},
    {"name": "CarScoops BMW",     "url": "https://www.carscoops.com/tag/bmw/feed/",         "category": "bmw", "scrape_gallery": True},
    {"name": "Electrek BMW",      "url": "https://electrek.co/guides/bmw/feed/",            "category": "bmw"},
    {"name": "Autocar BMW",       "url": "https://www.autocar.co.uk/rss/bmw",               "category": "bmw", "scrape_gallery": True},
    {"name": "Motor1 BMW",        "url": "https://www.motor1.com/rss/articles/make/bmw/",   "category": "bmw", "scrape_gallery": True},

    # ── General automotive (broad world coverage) ─────────────────────────────
    {"name": "CarScoops",             "url": "https://www.carscoops.com/feed/",                          "category": "auto", "scrape_gallery": True},
    {"name": "Car and Driver",        "url": "https://www.caranddriver.com/rss/all.xml",                 "category": "auto", "scrape_gallery": True},
    {"name": "Car and Driver News",   "url": "https://www.caranddriver.com/rss/news.xml",                "category": "auto", "scrape_gallery": True},
    {"name": "Car and Driver Reviews","url": "https://www.caranddriver.com/rss/reviews.xml",             "category": "auto", "scrape_gallery": True},
    {"name": "Autocar",               "url": "https://www.autocar.co.uk/rss",                            "category": "auto", "scrape_gallery": True},
    {"name": "AutoExpress",           "url": "https://www.autoexpress.co.uk/rss",                        "category": "auto", "scrape_gallery": True},
    {"name": "CarExpert",             "url": "https://carexpert.com.au/feed/",                           "category": "auto"},
    {"name": "Jalopnik",              "url": "https://jalopnik.com/rss",                                 "category": "auto"},
    {"name": "The Drive",             "url": "https://www.thedrive.com/feed",                            "category": "auto"},
    {"name": "Electrek",              "url": "https://electrek.co/feed/",                                "category": "auto"},
    {"name": "InsideEVs",             "url": "https://insideevs.com/feed/",                              "category": "auto"},
    {"name": "Motorious",             "url": "https://motorious.com/feed/",                              "category": "auto"},
    {"name": "GM Authority",          "url": "https://gmauthority.com/blog/feed/",                       "category": "auto"},
    {"name": "CarBuzz",               "url": "https://carbuzz.com/feed/",                                "category": "auto", "scrape_gallery": True},

    # ── NEW: Premium multi-photo sources ──────────────────────────────────────
    {"name": "Motor1",                "url": "https://www.motor1.com/rss/articles/all/",                 "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 News",           "url": "https://www.motor1.com/rss/articles/category/news/",       "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Reviews",        "url": "https://www.motor1.com/rss/articles/category/reviews/",    "category": "auto", "scrape_gallery": True},
    {"name": "Road & Track",          "url": "https://www.roadandtrack.com/rss/all.xml",                 "category": "auto", "scrape_gallery": True},
    {"name": "Road & Track News",     "url": "https://www.roadandtrack.com/rss/news.xml",                "category": "auto", "scrape_gallery": True},
    {"name": "Road & Track Reviews",  "url": "https://www.roadandtrack.com/rss/reviews.xml",             "category": "auto", "scrape_gallery": True},
    {"name": "HotCars",               "url": "https://www.hotcars.com/feed/",                            "category": "auto", "scrape_gallery": True},
    {"name": "TopSpeed",              "url": "https://www.topspeed.com/feed/",                           "category": "auto", "scrape_gallery": True},
    {"name": "AutoWeek News",         "url": "https://www.autoweek.com/rss/news/",                       "category": "auto", "scrape_gallery": True},
    {"name": "Hagerty Media",         "url": "https://www.hagerty.com/media/feed/",                      "category": "auto", "scrape_gallery": True},
    {"name": "BarnFinds",             "url": "https://barnfinds.com/feed/",                              "category": "auto", "scrape_gallery": True},
    {"name": "ClassicCars Journal",   "url": "https://journal.classiccars.com/feed/",                    "category": "auto", "scrape_gallery": True},

    # ── NEW: Brand-specific tag feeds on proven-good hosts ─────────────────────
    {"name": "CarScoops Audi",        "url": "https://www.carscoops.com/tag/audi/feed/",                 "category": "auto", "scrape_gallery": True},
    {"name": "CarScoops Porsche",     "url": "https://www.carscoops.com/tag/porsche/feed/",              "category": "auto", "scrape_gallery": True},
    {"name": "CarScoops Ferrari",     "url": "https://www.carscoops.com/tag/ferrari/feed/",              "category": "auto", "scrape_gallery": True},
    {"name": "CarScoops Tesla",       "url": "https://www.carscoops.com/tag/tesla/feed/",                "category": "auto", "scrape_gallery": True},
    {"name": "Car and Driver Toyota", "url": "https://www.caranddriver.com/rss/toyota.xml",              "category": "auto", "scrape_gallery": True},
    {"name": "Car and Driver Mercedes","url": "https://www.caranddriver.com/rss/mercedes-benz.xml",      "category": "auto", "scrape_gallery": True},
    {"name": "Car and Driver Audi",   "url": "https://www.caranddriver.com/rss/audi.xml",                "category": "auto", "scrape_gallery": True},
    {"name": "Car and Driver Porsche","url": "https://www.caranddriver.com/rss/porsche.xml",             "category": "auto", "scrape_gallery": True},
    {"name": "Car and Driver Ferrari","url": "https://www.caranddriver.com/rss/ferrari.xml",             "category": "auto", "scrape_gallery": True},
    {"name": "Car and Driver Lexus",  "url": "https://www.caranddriver.com/rss/lexus.xml",               "category": "auto", "scrape_gallery": True},
    {"name": "Autocar Porsche",       "url": "https://www.autocar.co.uk/rss/porsche",                    "category": "auto", "scrape_gallery": True},
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
    for kw in BMW_STRONG_KEYWORDS:
        if re.search(r"\b" + re.escape(kw) + r"\b", text, re.IGNORECASE):
            return True
        if any(ord(c) > 127 for c in kw) and kw.lower() in text_lower:
            return True
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
# Garbage image detection
#
# A "garbage" image is anything that is NOT a content photo: logos, favicons,
# icons, sprites, tracker pixels, avatars, placeholder/transparent PNGs, ads,
# social-share buttons, author profile pics, theme/decoration images.
#
# Items whose only image is garbage are dropped entirely (user requirement:
# "убедись что в json файлы не попадают новости с мусорными фото").
# ─────────────────────────────────────────────────────────────────────────────
GARBAGE_URL_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE) for p in [
        # Logos, icons, favicons, sprites
        r"/logo[s]?\b",
        r"/icons?/",
        r"/favicon",
        r"/sprite[s]?\b",
        r"\blogo[s]?\b",
        r"\bfavicon\b",

        # Trackers & ad pixels
        r"/pixel[s]?\b",
        r"/tracker",
        r"/beacon",
        r"doubleclick",
        r"google-analytics",
        r"facebook\.com/tr",
        r"googletagmanager",
        r"scorecardresearch",
        r"/ads?/",
        r"\bad[-_]?server",
        r"advertising",
        r"/sponsored/",
        r"/sponsor/",

        # Avatars, profile pics, author bylines
        r"/avatar",
        r"/authors?/",
        r"/profile[-_]?pic",
        r"/byline",
        r"gravatar",
        r"wp-content/uploads/.*\bavatar\b",

        # Placeholders, blanks, transparent spacers
        r"/blank\.",
        r"placeholder",
        r"\btransparent\b",
        r"\b16x9-tr\b",
        r"\bdefault[-_]?image\b",
        r"\bno[-_]?image\b",
        r"\bmissing[-_]?image\b",

        # 1x1 / tiny dimension hints
        r"\b1x1\b",
        r"width[=:]1\b",
        r"height[=:]1\b",

        # Social media buttons & share icons
        r"/social/",
        r"/share[-_]?icon",
        r"twitter\.com/",
        r"instagram\.com/",
        r"youtube\.com/",
        r"tiktok\.com/",
        r"facebook\.com/",
        r"linkedin\.com/",
        r"pinterest\.com/",
        r"reddit\.com/",
        r"whatsapp\.com/",
        r"telegram\.org/",
        r"/newsletter/",
        r"/subscribe/",
        r"/sign[-_]?up/",
        r"/comment[s]?/",

        # Theme & site chrome
        r"/themes?/",
        r"/templates?/",
        r"/assets/",
        r"/static/(?!uploads)",
        r"/wp-content/themes/",
        r"/wp-content/plugins/",
        r"/wp-includes/",
        r"/img/(?!uploads)",

        # Emoji
        r"emoji",
        r"/emoticons?/",

        # Shopping / affiliate
        r"amazon\.com/",
        r"shopify",
        r"/shop/",
        r"/store/",

        # GIFs are almost never content photos in feeds
        r"\.gif($|\?)",
    ]
]


def is_garbage_image(url: str) -> bool:
    """Return True if the URL looks like a non-content image
    (logo/icon/tracker/avatar/placeholder/social/theme/etc.).
    """
    if not url:
        return True
    # data: URIs are never content photos in this context
    if url.startswith("data:"):
        return True
    # Tiny dimension hints in query (?w=1&h=1, ?resize=1x1, etc.) — authors byline pics
    q = parse_qs(urlparse(url).query)
    for k in ("w", "width", "h", "height"):
        if k in q and q[k]:
            try:
                if int(q[k][0]) <= 32:
                    return True
            except ValueError:
                pass
    # Small WordPress size suffix like "-90x90.jpg", "-32x32.png", "-1x1.gif"
    if re.search(r"-(\d{1,2})x(\d{1,2})\.(?:jpg|jpeg|png|webp|gif)(?:\?|$)", url, re.I):
        return True
    # WordPress author/profile pics
    if re.search(r"wp-content/uploads/.*(?:avatar|profile|author)", url, re.I):
        return True
    # Apply regex list
    for pat in GARBAGE_URL_PATTERNS:
        if pat.search(url):
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# HTTP helpers
# ─────────────────────────────────────────────────────────────────────────────
def fetch_url(url: str, want_html: bool = False) -> tuple[int | None, bytes | None, str | None]:
    """Fetch a URL. If want_html=True, use the HTML Accept header set."""
    headers = HTML_HEADERS if want_html else HTTP_HEADERS
    try:
        r = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT if not want_html else HTML_TIMEOUT)
        return r.status_code, r.content, None
    except Exception as e:
        return None, None, str(e)


def extract_image(entry: Any) -> str | None:
    """Try every standard RSS image location. Returns None if no image or
    if the only candidate looks like garbage (logo/tracker/etc.)."""
    candidates: list[str] = []

    # 1. enclosures
    for enc in getattr(entry, "enclosures", []) or []:
        href = enc.get("href", "")
        if href:
            t = enc.get("type", "").lower()
            if t.startswith("image") or any(href.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp")):
                candidates.append(href)
    # 2. media_content
    for m in getattr(entry, "media_content", []) or []:
        url = m.get("url", "")
        if url:
            candidates.append(url)
    # 3. media_thumbnail
    for m in getattr(entry, "media_thumbnail", []) or []:
        url = m.get("url", "")
        if url:
            candidates.append(url)
    # 4. <img> in summary/content
    for field in ("summary", "description", "content"):
        val = getattr(entry, field, None)
        if not val:
            continue
        if isinstance(val, list) and val:
            val = val[0].get("value", "")
        for m in re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', str(val)):
            candidates.append(m.group(1))

    # Return the first NON-garbage candidate; fall back to first candidate
    # only if all are garbage (caller decides whether to drop the item).
    for c in candidates:
        if not is_garbage_image(c):
            return c
    return candidates[0] if candidates else None


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
# Article-page gallery scraping
#
# For sources flagged scrape_gallery=True, we fetch the article HTML and
# extract additional <img> URLs to populate the `images` array.
# ─────────────────────────────────────────────────────────────────────────────
class _ImgCollector(HTMLParser):
    """Collect <img> src + data-src + srcset URLs from HTML."""
    def __init__(self) -> None:
        super().__init__()
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "img":
            return
        d = {k.lower(): (v or "") for k, v in attrs}
        for key in ("src", "data-src", "data-lazy-src", "data-original", "data-cfsrc"):
            v = d.get(key)
            if v:
                self.urls.append(v)
        srcset = d.get("srcset") or d.get("data-srcset")
        if srcset:
            parts = [p.strip().split(" ")[0] for p in srcset.split(",") if p.strip()]
            self.urls.extend(parts)


def _base_image_url(url: str) -> str:
    """Group key for the same source image: strip query & WP size suffix.

    Used to dedup different crops/sizes of the same source photo. e.g.:
      foo.jpg?resize=980:*
      foo.jpg?resize=640:*
      foo-1024x576.jpg
    all map to the same base 'foo.jpg'.
    """
    p = urlparse(url)
    path = re.sub(r"-\d+x\d+(?=\.\w+$)", "", p.path)
    return f"{p.scheme}://{p.netloc}{path}"


def _image_size_hint(url: str) -> int:
    """Return a 'bigger is better' size hint from URL query / path suffix.

    Used to pick the largest variant among same-base URLs (so we don't keep
    the 90x90 thumbnail when a 1200x675 version exists).
    """
    q = parse_qs(urlparse(url).query)
    for k in ("resize", "fit", "w", "width"):
        if k in q and q[k]:
            m = re.search(r"(\d+)", q[k][0])
            if m:
                return int(m.group(1))
    # WordPress size suffix: -1024x576.jpg → 1024*576
    m = re.search(r"-(\d+)x(\d+)\.\w+$", urlparse(url).path)
    if m:
        return int(m.group(1)) * int(m.group(2))
    return 9999  # No size hint → assume original (best quality)


def extract_gallery_from_html(html_text: str, base_url: str, lead_image: str | None) -> list[str]:
    """Parse article HTML, extract up to (MAX_IMAGES_PER_ITEM - 1) additional
    non-garbage img URLs (excluding the lead image).

    Returns the additional URLs only (caller will prepend the lead image).
    """
    parser = _ImgCollector()
    try:
        parser.feed(html_text)
    except Exception:
        return []

    grouped: dict[str, list[str]] = {}
    for u in parser.urls:
        full = urljoin(base_url, u)
        # Only allow real-looking image URLs
        if not re.search(r"\.(jpg|jpeg|png|webp)(\?|$)", full, re.I):
            continue
        if is_garbage_image(full):
            continue
        b = _base_image_url(full)
        grouped.setdefault(b, []).append(full)

    # Drop the group matching the lead image (we'll add it separately)
    if lead_image:
        lead_b = _base_image_url(lead_image)
        grouped.pop(lead_b, None)

    # In each group, pick the best (largest) variant
    chosen: list[str] = []
    for variants in grouped.values():
        best = max(variants, key=_image_size_hint)
        chosen.append(best)

    # Heuristic: prefer URLs whose path contains /uploads/ or /images/mgl/ (real
    # content photos) over generic paths. Then keep the top N by size hint.
    def rank(u: str) -> tuple[int, int]:
        path = urlparse(u).path.lower()
        premium = 1 if any(s in path for s in ("/uploads/", "/mgl/", "/images/", "/media/", "/hmg-prod/")) else 0
        return (premium, _image_size_hint(u))

    chosen.sort(key=rank, reverse=True)
    return chosen[: MAX_IMAGES_PER_ITEM - 1]


def scrape_article_images(url: str, lead_image: str | None) -> list[str]:
    """Fetch an article page and return a list of additional image URLs
    (excluding the lead). Returns [] on any error or if no gallery found."""
    if not url:
        return []
    status, content, err = fetch_url(url, want_html=True)
    if status != 200 or not content:
        log.debug("  gallery scrape failed for %s: %s", url, err or f"HTTP {status}")
        return []
    try:
        # Decode with fallback — most pages are UTF-8 but some are latin-1
        try:
            text = content.decode("utf-8", errors="replace")
        except Exception:
            text = content.decode("latin-1", errors="replace")
        return extract_gallery_from_html(text, url, lead_image)
    except Exception as e:
        log.debug("  gallery parse error for %s: %s", url, e)
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Fetching
# ─────────────────────────────────────────────────────────────────────────────
def fetch_one(source: dict[str, Any]) -> list[dict[str, Any]]:
    """Fetch and parse a single RSS source into normalized items.

    If source['scrape_gallery'] is True, also fetch up to
    MAX_GALLERY_SCRAPE_PER_SOURCE article pages to extract multi-photo galleries
    for the most recent items.
    """
    name = source["name"]
    url = source["url"]
    category = source["category"]
    scrape_gallery = bool(source.get("scrape_gallery", False))
    log.info("Fetching %s (%s)%s", name, url, " [gallery]" if scrape_gallery else "")
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

        # ── Garbage-photo guard ────────────────────────────────────────────
        # User requirement: "убедись что в json файлы не попадают новости
        # с мусорными фото". If the ONLY image we can find is garbage (logo,
        # tracker, etc.), drop the item entirely. Items with no image at all
        # are kept — some sources publish breaking news without a lead photo
        # and we don't want to lose them.
        if image and is_garbage_image(image):
            log.debug("  ✗ %s: dropping item with only garbage image: %s", name, image)
            continue

        # Determine BMW relevance (used by classifier later, but pre-compute)
        is_bmw = is_bmw_relevant(title, summary)

        items.append({
            "id": item_id(link, title),
            "title": title,
            "summary": summary,
            "url": link,
            "image": image or "",   # backwards-compat single-image field
            "images": [image] if image else [],   # will be enriched below
            "source": name,
            "source_url": base_url,
            "category": category,
            "published": published,
            "is_bmw": is_bmw,
        })

    # ── Gallery scraping (multi-photo) ────────────────────────────────────
    if scrape_gallery and items:
        # Only scrape the top N most recent items to control runtime
        to_scrape = items[:MAX_GALLERY_SCRAPE_PER_SOURCE]
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(scrape_article_images, it["url"], it["image"]): it for it in to_scrape}
            for fut in as_completed(futures):
                it = futures[fut]
                try:
                    extra = fut.result()
                except Exception as e:
                    log.debug("  gallery scrape crashed for %s: %s", it["url"], e)
                    extra = []
                if extra:
                    # Prepend lead image if present, then add unique extras
                    lead = it["image"]
                    gallery: list[str] = []
                    if lead:
                        gallery.append(lead)
                    for u in extra:
                        if u not in gallery:
                            gallery.append(u)
                    it["images"] = gallery[:MAX_IMAGES_PER_ITEM]

    log.info("  ✓ %s: %d items (multi-photo: %d)",
             name, len(items),
             sum(1 for it in items if len(it.get("images", [])) > 1))
    return items


def fetch_all(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
        return True
    try:
        dt = datetime.fromisoformat(published_iso.replace("Z", "+00:00"))
    except Exception:
        return True
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    return dt >= cutoff


def dedup(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate by id (URL+title hash). When collisions occur, prefer the
    item that has more images / a longer summary."""
    by_id: dict[str, dict[str, Any]] = {}
    for it in items:
        existing = by_id.get(it["id"])
        if existing is None:
            by_id[it["id"]] = it
            continue
        # Prefer the one with more images, else longer summary
        if len(it.get("images", [])) > len(existing.get("images", [])):
            by_id[it["id"]] = it
        elif len(it.get("images", [])) == len(existing.get("images", [])) and \
             len(it["summary"]) > len(existing["summary"]):
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
    # Stats on multi-photo coverage
    multi_photo = sum(1 for it in items if len(it.get("images", [])) > 1)
    return {
        "kind": kind,  # "bmw" or "auto"
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_at_human": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "total_items": len(items),
        "sources_used": sources_used,
        "sources_count": len(sources_used),
        "multi_photo_items": multi_photo,
        "items": items,
    }


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    log.info("Wrote %s (%d items, %d multi-photo, %d bytes)",
             path, data["total_items"], data["multi_photo_items"],
             path.stat().st_size)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def main() -> int:
    log.info("=" * 70)
    log.info("Automotive news parser — starting run")
    log.info("Sources: %d total (%d with gallery scraping)",
             len(SOURCES), sum(1 for s in SOURCES if s.get("scrape_gallery")))
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
    auto_items = [it for it in recent if it["category"] != "bmw"]

    # 5. Prefer items with images, then sort by recency
    def image_first_key(it: dict[str, Any]) -> tuple[int, int, str]:
        n_imgs = len(it.get("images", []))
        has_img = 0 if n_imgs > 0 else 1
        return (has_img, -n_imgs, "")

    bmw_items_sorted = sort_newest_first(sorted(bmw_items, key=image_first_key))
    auto_items_sorted = sort_newest_first(sorted(auto_items, key=image_first_key))

    # 6. Trim to reasonable cap (top 200 / 250 — enough for downstream bots
    #    to have choice while keeping the JSON file under ~400 KB)
    bmw_items_sorted = bmw_items_sorted[:200]
    auto_items_sorted = auto_items_sorted[:250]

    # 7. Drop helper fields that were internal-only
    def clean(it: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": it["id"],
            "title": it["title"],
            "summary": it["summary"],
            "url": it["url"],
            "image": it["image"],
            "images": it.get("images", []),
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
