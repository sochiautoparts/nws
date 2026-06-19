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

Tuning (2026-06):
  - MAX_AGE_DAYS = 14  (was 7) so the BMW file reliably has hundreds of items
  - Output caps: BMW = top 500, Auto = top 500
  - 251 sources total (76 BMW + 175 general auto)
  - All items are guaranteed to have at least one quality photo (photoless
    articles are now dropped — not just garbage-photo ones)
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
HTML_TIMEOUT = 12
MAX_ITEMS_PER_FEED = 25            # cap so one noisy feed can't dominate
MAX_AGE_DAYS = 30                  # only keep items newer than this
BMW_MAX_AGE_DAYS = 90              # BMW-relevant items are rarer; widen to 90 days
AUTO_MAX_AGE_DAYS = 30             # general auto stays at 30 days
MAX_GALLERY_SCRAPE_PER_SOURCE = 3  # how many article pages to fetch per gallery source
MAX_IMAGES_PER_ITEM = 6            # cap on the `images` array (incl. lead image)
BMW_OUTPUT_CAP = 500               # max items in bmw-news.json
AUTO_OUTPUT_CAP = 500              # max items in auto-news.json

# ─────────────────────────────────────────────────────────────────────────────
# Curated source list — hand-tested 2026-06
# ─────────────────────────────────────────────────────────────────────────────
SOURCES: list[dict[str, Any]] = [
    # ── BMW-specific — BMW Blog (broad model + tag coverage) ────────────────
    {"name": "BMW Blog",              "url": "https://bmwblog.com/feed/",                              "category": "bmw"},
    {"name": "BMW Blog M",            "url": "https://bmwblog.com/category/bmw-m/feed/",               "category": "bmw"},
    {"name": "BMW Blog i",            "url": "https://bmwblog.com/category/bmw-i/feed/",               "category": "bmw"},
    {"name": "BMW Blog X",            "url": "https://bmwblog.com/category/bmw-x/feed/",               "category": "bmw"},
    {"name": "BMW Blog 3",            "url": "https://bmwblog.com/category/bmw-3-series/feed/",         "category": "bmw"},
    {"name": "BMW Blog 5",            "url": "https://bmwblog.com/category/bmw-5-series/feed/",         "category": "bmw"},
    {"name": "BMW Blog 1",            "url": "https://bmwblog.com/category/bmw-1-series/feed/",         "category": "bmw"},
    {"name": "BMW Blog 4",            "url": "https://bmwblog.com/category/bmw-4-series/feed/",         "category": "bmw"},
    {"name": "BMW Blog 6",            "url": "https://bmwblog.com/category/bmw-6-series/feed/",         "category": "bmw"},
    {"name": "BMW Blog Z4",           "url": "https://bmwblog.com/category/bmw-z4/feed/",               "category": "bmw"},
    {"name": "BMW Blog M2",           "url": "https://bmwblog.com/category/bmw-m2/feed/",               "category": "bmw"},
    {"name": "BMW Blog M3",           "url": "https://bmwblog.com/category/bmw-m3/feed/",               "category": "bmw"},
    {"name": "BMW Blog M4",           "url": "https://bmwblog.com/category/bmw-m4/feed/",               "category": "bmw"},
    {"name": "BMW Blog M5",           "url": "https://bmwblog.com/category/bmw-m5/feed/",               "category": "bmw"},
    {"name": "BMW Blog M6",           "url": "https://bmwblog.com/category/bmw-m6/feed/",               "category": "bmw"},
    {"name": "BMW Blog M8",           "url": "https://bmwblog.com/category/bmw-m8/feed/",               "category": "bmw"},
    {"name": "BMW Blog i5",           "url": "https://bmwblog.com/category/bmw-i5/feed/",               "category": "bmw"},
    # NOTE: BMW Blog i7 removed — feed returns 0 quality photos
    {"name": "BMW Blog concepts",     "url": "https://bmwblog.com/category/concepts/feed/",             "category": "bmw"},
    {"name": "BMW Blog X1",           "url": "https://bmwblog.com/category/bmw-x1/feed/",               "category": "bmw"},
    {"name": "BMW Blog X2",           "url": "https://bmwblog.com/category/bmw-x2/feed/",               "category": "bmw"},
    {"name": "BMW Blog X3",           "url": "https://bmwblog.com/category/bmw-x3/feed/",               "category": "bmw"},
    {"name": "BMW Blog X5",           "url": "https://bmwblog.com/category/bmw-x5/feed/",               "category": "bmw"},
    {"name": "BMW Blog X6",           "url": "https://bmwblog.com/category/bmw-x6/feed/",               "category": "bmw"},
    {"name": "BMW Blog X7",           "url": "https://bmwblog.com/category/bmw-x7/feed/",               "category": "bmw"},
    {"name": "BMW Blog Motorrad",     "url": "https://bmwblog.com/category/bmw-motorrad/feed/",         "category": "bmw"},

    # ── BMW-specific — BMW Blog tag feeds ──────────────────────────────────
    {"name": "BMW Blog Alpina tag",   "url": "https://bmwblog.com/tag/alpina/feed/",                    "category": "bmw"},
    {"name": "BMW Blog Mini tag",     "url": "https://bmwblog.com/tag/mini/feed/",                      "category": "bmw"},
    {"name": "BMW Blog X1 tag",       "url": "https://bmwblog.com/tag/x1/feed/",                        "category": "bmw"},
    {"name": "BMW Blog X2 tag",       "url": "https://bmwblog.com/tag/x2/feed/",                        "category": "bmw"},
    {"name": "BMW Blog X3 tag",       "url": "https://bmwblog.com/tag/x3/feed/",                        "category": "bmw"},
    {"name": "BMW Blog X4 tag",       "url": "https://bmwblog.com/tag/x4/feed/",                        "category": "bmw"},
    {"name": "BMW Blog X5 tag",       "url": "https://bmwblog.com/tag/x5/feed/",                        "category": "bmw"},
    {"name": "BMW Blog X6 tag",       "url": "https://bmwblog.com/tag/x6/feed/",                        "category": "bmw"},
    {"name": "BMW Blog X7 tag",       "url": "https://bmwblog.com/tag/x7/feed/",                        "category": "bmw"},
    {"name": "BMW Blog XM tag",       "url": "https://bmwblog.com/tag/xm/feed/",                        "category": "bmw"},
    {"name": "BMW Blog iX1 tag",      "url": "https://bmwblog.com/tag/ix1/feed/",                       "category": "bmw"},
    {"name": "BMW Blog iX3 tag",      "url": "https://bmwblog.com/tag/ix3/feed/",                       "category": "bmw"},
    {"name": "BMW Blog iX tag",       "url": "https://bmwblog.com/tag/ix/feed/",                        "category": "bmw"},
    {"name": "BMW Blog i3 tag",       "url": "https://bmwblog.com/tag/i3/feed/",                        "category": "bmw"},
    {"name": "BMW Blog i4 tag",       "url": "https://bmwblog.com/tag/i4/feed/",                        "category": "bmw"},
    {"name": "BMW Blog i5 tag",       "url": "https://bmwblog.com/tag/i5/feed/",                        "category": "bmw"},
    {"name": "BMW Blog i7 tag",       "url": "https://bmwblog.com/tag/i7/feed/",                        "category": "bmw"},
    {"name": "BMW Blog i8 tag",       "url": "https://bmwblog.com/tag/i8/feed/",                        "category": "bmw"},
    {"name": "BMW Blog M tag",        "url": "https://bmwblog.com/tag/bmw-m/feed/",                     "category": "bmw"},
    {"name": "BMW Blog M2 tag",       "url": "https://bmwblog.com/tag/m2/feed/",                        "category": "bmw"},
    {"name": "BMW Blog M3 tag",       "url": "https://bmwblog.com/tag/bmw-m3/feed/",                    "category": "bmw"},
    {"name": "BMW Blog M4 tag",       "url": "https://bmwblog.com/tag/bmw-m4/feed/",                    "category": "bmw"},
    {"name": "BMW Blog M5 tag",       "url": "https://bmwblog.com/tag/bmw-m5/feed/",                    "category": "bmw"},
    {"name": "BMW Blog M6 tag",       "url": "https://bmwblog.com/tag/m6/feed/",                        "category": "bmw"},
    {"name": "BMW Blog M8 tag",       "url": "https://bmwblog.com/tag/bmw-m8/feed/",                    "category": "bmw"},
    {"name": "BMW Blog 1 tag",        "url": "https://bmwblog.com/tag/1-series/feed/",                  "category": "bmw"},
    {"name": "BMW Blog 2 tag",        "url": "https://bmwblog.com/tag/2-series/feed/",                  "category": "bmw"},
    {"name": "BMW Blog 3 tag",        "url": "https://bmwblog.com/tag/3-series/feed/",                  "category": "bmw"},
    {"name": "BMW Blog 5 tag",        "url": "https://bmwblog.com/tag/5-series/feed/",                  "category": "bmw"},
    {"name": "BMW Blog 7 tag",        "url": "https://bmwblog.com/tag/7-series/feed/",                  "category": "bmw"},
    {"name": "BMW Blog 8 tag",        "url": "https://bmwblog.com/tag/8-series/feed/",                  "category": "bmw"},
    {"name": "BMW Blog X tag",        "url": "https://bmwblog.com/tag/bmw-x/feed/",                     "category": "bmw"},
    {"name": "BMW Blog i tag",        "url": "https://bmwblog.com/tag/bmw-i/feed/",                     "category": "bmw"},
    {"name": "BMW Blog Mini Cooper",  "url": "https://bmwblog.com/tag/mini-cooper/feed/",               "category": "bmw"},
    {"name": "BMW Blog Rolls-Royce",  "url": "https://bmwblog.com/tag/rolls-royce/feed/",               "category": "bmw"},
    # ── NEW (r6-r7): BMW Blog tag feeds verified quality-photo ──────────────
    {"name": "BMW Blog M3 tag2",      "url": "https://bmwblog.com/tag/m3/feed/",                        "category": "bmw"},
    {"name": "BMW Blog M4 tag2",      "url": "https://bmwblog.com/tag/m4/feed/",                        "category": "bmw"},
    {"name": "BMW Blog M5 tag2",      "url": "https://bmwblog.com/tag/m5/feed/",                        "category": "bmw"},
    {"name": "BMW Blog M8 tag2",      "url": "https://bmwblog.com/tag/m8/feed/",                        "category": "bmw"},
    {"name": "BMW Blog Motorrad tag", "url": "https://bmwblog.com/tag/bmw-motorrad/feed/",              "category": "bmw"},
    {"name": "BMW Blog Concepts tag", "url": "https://bmwblog.com/tag/concepts/feed/",                  "category": "bmw"},

    # ── BMW-specific — other sites ───────────────────────────────────────────
    {"name": "BimmerFile",            "url": "https://bimmerfile.com/feed/",                            "category": "bmw"},
    {"name": "BimmerToday DE",        "url": "https://www.bimmertoday.de/feed/",                        "category": "bmw"},
    {"name": "Car and Driver BMW",    "url": "https://www.caranddriver.com/rss/bmw.xml",                "category": "bmw", "scrape_gallery": True},
    {"name": "CarScoops BMW",         "url": "https://www.carscoops.com/tag/bmw/feed/",                 "category": "bmw", "scrape_gallery": True},
    {"name": "Electrek BMW",          "url": "https://electrek.co/guides/bmw/feed/",                    "category": "bmw"},
    {"name": "Electrek BMW iX",       "url": "https://electrek.co/guides/bmw-ix/feed/",                 "category": "bmw"},
    {"name": "Autocar BMW",           "url": "https://www.autocar.co.uk/rss/bmw",                       "category": "bmw", "scrape_gallery": True},
    {"name": "Autocar BMW M",         "url": "https://www.autocar.co.uk/rss/bmw-m",                     "category": "bmw", "scrape_gallery": True},
    {"name": "Autocar BMW i",         "url": "https://www.autocar.co.uk/rss/bmw-i",                     "category": "bmw", "scrape_gallery": True},
    {"name": "Motor1 BMW",            "url": "https://www.motor1.com/rss/articles/make/bmw/",           "category": "bmw", "scrape_gallery": True},

    # ── General automotive — broad feeds (premium, photo-rich) ───────────────
    {"name": "CarScoops",             "url": "https://www.carscoops.com/feed/",                          "category": "auto", "scrape_gallery": True},
    {"name": "Car and Driver",        "url": "https://www.caranddriver.com/rss/all.xml",                 "category": "auto", "scrape_gallery": True},
    {"name": "Car and Driver News",   "url": "https://www.caranddriver.com/rss/news.xml",                "category": "auto", "scrape_gallery": True},
    {"name": "Car and Driver Reviews","url": "https://www.caranddriver.com/rss/reviews.xml",             "category": "auto", "scrape_gallery": True},
    {"name": "Autocar",               "url": "https://www.autocar.co.uk/rss",                            "category": "auto", "scrape_gallery": True},
    {"name": "AutoExpress",           "url": "https://www.autoexpress.co.uk/rss",                        "category": "auto", "scrape_gallery": True},
    {"name": "CarExpert",             "url": "https://carexpert.com.au/feed/",                           "category": "auto"},
    {"name": "Jalopnik",              "url": "https://jalopnik.com/rss",                                 "category": "auto", "scrape_gallery": True},
    {"name": "The Drive",             "url": "https://www.thedrive.com/feed",                            "category": "auto"},
    {"name": "Electrek",              "url": "https://electrek.co/feed/",                                "category": "auto"},
    {"name": "InsideEVs",             "url": "https://insideevs.com/feed/",                              "category": "auto"},
    {"name": "Motorious",             "url": "https://motorious.com/feed/",                              "category": "auto"},
    {"name": "GM Authority",          "url": "https://gmauthority.com/blog/feed/",                       "category": "auto"},
    {"name": "CarBuzz",               "url": "https://carbuzz.com/feed/",                                "category": "auto", "scrape_gallery": True},
    {"name": "Motor1",                "url": "https://www.motor1.com/rss/articles/all/",                 "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 News",           "url": "https://www.motor1.com/rss/articles/category/news/",       "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Reviews",        "url": "https://www.motor1.com/rss/articles/category/reviews/",    "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Classics",       "url": "https://www.motor1.com/rss/articles/category/classics/",   "category": "auto", "scrape_gallery": True},
    # NOTE: Road & Track (all/News/Reviews) removed per request — replaced
    # with broader quality sources (Auto.Mail.RU, Motoring Research, etc.)
    {"name": "HotCars",               "url": "https://www.hotcars.com/feed/",                            "category": "auto", "scrape_gallery": True},
    {"name": "TopSpeed",              "url": "https://www.topspeed.com/feed/",                           "category": "auto", "scrape_gallery": True},
    {"name": "AutoWeek News",         "url": "https://www.autoweek.com/rss/news/",                       "category": "auto", "scrape_gallery": True},
    {"name": "Hagerty Media",         "url": "https://www.hagerty.com/media/feed/",                      "category": "auto", "scrape_gallery": True},
    {"name": "BarnFinds",             "url": "https://barnfinds.com/feed/",                              "category": "auto", "scrape_gallery": True},
    {"name": "ClassicCars Journal",   "url": "https://journal.classiccars.com/feed/",                    "category": "auto", "scrape_gallery": True},
    # NOTE: Teslarati removed — feed returns <1 quality photo per 10 entries
    # NOTE: Green Car Reports removed — persistent HTTP 403, never produces output
    # NOTE: AutoWise removed — feed returns 0 quality photos
    {"name": "Nissan News",           "url": "https://global.nissannews.com/rss",                        "category": "auto"},
    {"name": "5koleso RU",            "url": "https://5koleso.ru/feed/",                                 "category": "auto"},

    # ── General automotive — new premium broad feeds (2026-06 expansion) ────
    {"name": "Honda News",            "url": "https://hondanews.com/rss",                               "category": "auto"},
    {"name": "Engadget Auto",         "url": "https://www.engadget.com/rss.xml",                        "category": "auto"},
    {"name": "The Verge Transp",      "url": "https://www.theverge.com/rss/transportation/index.xml",   "category": "auto"},
    {"name": "What Car",              "url": "https://www.whatcar.com/rss",                             "category": "auto"},
    {"name": "CarThrottle",           "url": "https://www.carthrottle.com/rss",                         "category": "auto"},
    {"name": "Bring a Trailer",       "url": "https://bringatrailer.com/feed/",                         "category": "auto"},
    {"name": "Bike EXIF",             "url": "https://www.bikeexif.com/feed",                           "category": "auto"},
    {"name": "Hooniverse",            "url": "https://hooniverse.com/feed/",                            "category": "auto"},
    {"name": "Speed Academy",         "url": "https://www.speedacademy.net/feed/",                      "category": "auto"},
    {"name": "Track Day",             "url": "https://trackdaymag.com/feed/",                           "category": "auto"},
    {"name": "Kolesa RU",             "url": "https://www.kolesa.ru/rss",                               "category": "auto"},
    # ── General automotive — NEW premium broad feeds (2026-06 expansion r5-r7) ─
    {"name": "Auto.Mail.RU",          "url": "https://news.mail.ru/rss/auto/",                          "category": "auto"},
    {"name": "Motoring Research",     "url": "https://www.motoringresearch.com/feed/",                  "category": "auto"},
    {"name": "TopSpeed main",         "url": "https://www.topspeed.com/feed",                           "category": "auto", "scrape_gallery": True},
    {"name": "GM Authority News",     "url": "https://gmauthority.com/blog/category/news/feed/",        "category": "auto"},
    {"name": "Carscoops News",        "url": "https://www.carscoops.com/category/news/feed/",           "category": "auto", "scrape_gallery": True},

    # ── General automotive — CarScoops brand tags (gallery-enabled) ──────────
    {"name": "CarScoops Audi",        "url": "https://www.carscoops.com/tag/audi/feed/",                 "category": "auto", "scrape_gallery": True},
    {"name": "CarScoops Porsche",     "url": "https://www.carscoops.com/tag/porsche/feed/",              "category": "auto", "scrape_gallery": True},
    {"name": "CarScoops Ferrari",     "url": "https://www.carscoops.com/tag/ferrari/feed/",              "category": "auto", "scrape_gallery": True},
    {"name": "CarScoops Tesla",       "url": "https://www.carscoops.com/tag/tesla/feed/",                "category": "auto", "scrape_gallery": True},
    {"name": "CarScoops Mercedes",    "url": "https://www.carscoops.com/tag/mercedes/feed/",             "category": "auto", "scrape_gallery": True},
    {"name": "CarScoops Lamborghini", "url": "https://www.carscoops.com/tag/lamborghini/feed/",          "category": "auto", "scrape_gallery": True},
    {"name": "CarScoops McLaren",     "url": "https://www.carscoops.com/tag/mclaren/feed/",              "category": "auto", "scrape_gallery": True},
    {"name": "CarScoops Bentley",     "url": "https://www.carscoops.com/tag/bentley/feed/",              "category": "auto", "scrape_gallery": True},
    {"name": "CarScoops Rolls",       "url": "https://www.carscoops.com/tag/rolls-royce/feed/",          "category": "auto", "scrape_gallery": True},
    {"name": "CarScoops Bugatti",     "url": "https://www.carscoops.com/tag/bugatti/feed/",              "category": "auto", "scrape_gallery": True},
    {"name": "CarScoops Aston",       "url": "https://www.carscoops.com/tag/aston-martin/feed/",         "category": "auto", "scrape_gallery": True},
    {"name": "CarScoops Corvette",    "url": "https://www.carscoops.com/tag/corvette/feed/",             "category": "auto", "scrape_gallery": True},
    {"name": "CarScoops Toyota",      "url": "https://www.carscoops.com/tag/toyota/feed/",               "category": "auto", "scrape_gallery": True},
    {"name": "CarScoops Honda",       "url": "https://www.carscoops.com/tag/honda/feed/",                "category": "auto", "scrape_gallery": True},
    {"name": "CarScoops Ford",        "url": "https://www.carscoops.com/tag/ford/feed/",                 "category": "auto", "scrape_gallery": True},
    {"name": "CarScoops Chevy",       "url": "https://www.carscoops.com/tag/chevrolet/feed/",            "category": "auto", "scrape_gallery": True},
    {"name": "CarScoops Nissan",      "url": "https://www.carscoops.com/tag/nissan/feed/",               "category": "auto", "scrape_gallery": True},
    {"name": "CarScoops Mazda",       "url": "https://www.carscoops.com/tag/mazda/feed/",                "category": "auto", "scrape_gallery": True},
    {"name": "CarScoops Subaru",      "url": "https://www.carscoops.com/tag/subaru/feed/",               "category": "auto", "scrape_gallery": True},
    {"name": "CarScoops VW",          "url": "https://www.carscoops.com/tag/volkswagen/feed/",           "category": "auto", "scrape_gallery": True},
    {"name": "CarScoops Volvo",       "url": "https://www.carscoops.com/tag/volvo/feed/",                "category": "auto", "scrape_gallery": True},
    {"name": "CarScoops Hyundai",     "url": "https://www.carscoops.com/tag/hyundai/feed/",              "category": "auto", "scrape_gallery": True},
    {"name": "CarScoops Kia",         "url": "https://www.carscoops.com/tag/kia/feed/",                  "category": "auto", "scrape_gallery": True},
    {"name": "CarScoops Lexus",       "url": "https://www.carscoops.com/tag/lexus/feed/",                "category": "auto", "scrape_gallery": True},
    {"name": "CarScoops Mini",        "url": "https://www.carscoops.com/tag/mini/feed/",                 "category": "auto", "scrape_gallery": True},
    {"name": "CarScoops Jaguar",      "url": "https://www.carscoops.com/tag/jaguar/feed/",               "category": "auto", "scrape_gallery": True},
    {"name": "CarScoops Land Rover",  "url": "https://www.carscoops.com/tag/land-rover/feed/",           "category": "auto", "scrape_gallery": True},
    {"name": "CarScoops Maserati",    "url": "https://www.carscoops.com/tag/maserati/feed/",             "category": "auto", "scrape_gallery": True},
    {"name": "CarScoops Alfa",        "url": "https://www.carscoops.com/tag/alfa-romeo/feed/",           "category": "auto", "scrape_gallery": True},
    {"name": "CarScoops Genesis",     "url": "https://www.carscoops.com/tag/genesis/feed/",              "category": "auto", "scrape_gallery": True},
    {"name": "CarScoops Cadillac",    "url": "https://www.carscoops.com/tag/cadillac/feed/",             "category": "auto", "scrape_gallery": True},
    {"name": "CarScoops Dodge",       "url": "https://www.carscoops.com/tag/dodge/feed/",                "category": "auto", "scrape_gallery": True},

    # ── General automotive — Car and Driver brand feeds (only photo-rich ones)
    # NOTE: 26 C&D brand feeds removed (10 malformed XML, 7 no-photos, 9 weak-photos)
    # Only the 9 feeds that consistently return 3+ quality photos per 10 entries kept.
    {"name": "C&D Toyota",            "url": "https://www.caranddriver.com/rss/toyota.xml",              "category": "auto", "scrape_gallery": True},
    {"name": "C&D Audi",              "url": "https://www.caranddriver.com/rss/audi.xml",                "category": "auto", "scrape_gallery": True},
    {"name": "C&D Porsche",           "url": "https://www.caranddriver.com/rss/porsche.xml",             "category": "auto", "scrape_gallery": True},
    {"name": "C&D Lexus",             "url": "https://www.caranddriver.com/rss/lexus.xml",               "category": "auto", "scrape_gallery": True},
    {"name": "C&D Chevrolet",         "url": "https://www.caranddriver.com/rss/chevrolet.xml",           "category": "auto", "scrape_gallery": True},
    {"name": "C&D Hyundai",           "url": "https://www.caranddriver.com/rss/hyundai.xml",             "category": "auto", "scrape_gallery": True},
    {"name": "C&D Mitsubishi",        "url": "https://www.caranddriver.com/rss/mitsubishi.xml",          "category": "auto", "scrape_gallery": True},
    {"name": "C&D Subaru",            "url": "https://www.caranddriver.com/rss/subaru.xml",              "category": "auto", "scrape_gallery": True},
    {"name": "C&D Lotus",             "url": "https://www.caranddriver.com/rss/lotus.xml",               "category": "auto", "scrape_gallery": True},

    # ── General automotive — Autocar brand subfeeds (gallery-enabled) ────────
    {"name": "Autocar Porsche",       "url": "https://www.autocar.co.uk/rss/porsche",                    "category": "auto", "scrape_gallery": True},
    {"name": "Autocar Mercedes",      "url": "https://www.autocar.co.uk/rss/mercedes-benz",              "category": "auto", "scrape_gallery": True},
    {"name": "Autocar Audi",          "url": "https://www.autocar.co.uk/rss/audi",                        "category": "auto", "scrape_gallery": True},
    {"name": "Autocar Tesla",         "url": "https://www.autocar.co.uk/rss/tesla",                       "category": "auto", "scrape_gallery": True},
    {"name": "Autocar Toyota",        "url": "https://www.autocar.co.uk/rss/toyota",                      "category": "auto", "scrape_gallery": True},
    {"name": "Autocar Honda",         "url": "https://www.autocar.co.uk/rss/honda",                       "category": "auto", "scrape_gallery": True},
    {"name": "Autocar Ford",          "url": "https://www.autocar.co.uk/rss/ford",                        "category": "auto", "scrape_gallery": True},
    {"name": "Autocar VW",            "url": "https://www.autocar.co.uk/rss/volkswagen",                  "category": "auto", "scrape_gallery": True},
    {"name": "Autocar Hyundai",       "url": "https://www.autocar.co.uk/rss/hyundai",                     "category": "auto", "scrape_gallery": True},
    {"name": "Autocar Kia",           "url": "https://www.autocar.co.uk/rss/kia",                         "category": "auto", "scrape_gallery": True},
    {"name": "Autocar Mazda",         "url": "https://www.autocar.co.uk/rss/mazda",                       "category": "auto", "scrape_gallery": True},
    {"name": "Autocar Nissan",        "url": "https://www.autocar.co.uk/rss/nissan",                      "category": "auto", "scrape_gallery": True},
    {"name": "Autocar Renault",       "url": "https://www.autocar.co.uk/rss/renault",                     "category": "auto", "scrape_gallery": True},
    {"name": "Autocar Peugeot",       "url": "https://www.autocar.co.uk/rss/peugeot",                     "category": "auto", "scrape_gallery": True},
    {"name": "Autocar Land Rover",    "url": "https://www.autocar.co.uk/rss/land-rover",                  "category": "auto", "scrape_gallery": True},
    {"name": "Autocar Jaguar",        "url": "https://www.autocar.co.uk/rss/jaguar",                      "category": "auto", "scrape_gallery": True},
    {"name": "Autocar Lexus",         "url": "https://www.autocar.co.uk/rss/lexus",                       "category": "auto", "scrape_gallery": True},
    {"name": "Autocar Mini",          "url": "https://www.autocar.co.uk/rss/mini",                        "category": "auto", "scrape_gallery": True},
    {"name": "Autocar Ferrari",       "url": "https://www.autocar.co.uk/rss/ferrari",                     "category": "auto", "scrape_gallery": True},
    {"name": "Autocar Lamborghini",   "url": "https://www.autocar.co.uk/rss/lamborghini",                 "category": "auto", "scrape_gallery": True},
    {"name": "Autocar Bentley",       "url": "https://www.autocar.co.uk/rss/bentley",                     "category": "auto", "scrape_gallery": True},
    {"name": "Autocar Rolls",         "url": "https://www.autocar.co.uk/rss/rolls-royce",                 "category": "auto", "scrape_gallery": True},
    {"name": "Autocar McLaren",       "url": "https://www.autocar.co.uk/rss/mclaren",                     "category": "auto", "scrape_gallery": True},
    {"name": "Autocar Aston Martin",  "url": "https://www.autocar.co.uk/rss/aston-martin",                "category": "auto", "scrape_gallery": True},
    {"name": "Autocar Maserati",      "url": "https://www.autocar.co.uk/rss/maserati",                    "category": "auto", "scrape_gallery": True},
    {"name": "Autocar Alfa Romeo",    "url": "https://www.autocar.co.uk/rss/alfa-romeo",                  "category": "auto", "scrape_gallery": True},
    {"name": "Autocar Citroen",       "url": "https://www.autocar.co.uk/rss/citroen",                     "category": "auto", "scrape_gallery": True},
    {"name": "Autocar Fiat",          "url": "https://www.autocar.co.uk/rss/fiat",                        "category": "auto", "scrape_gallery": True},
    {"name": "Autocar Skoda",         "url": "https://www.autocar.co.uk/rss/skoda",                       "category": "auto", "scrape_gallery": True},
    {"name": "Autocar Suzuki",        "url": "https://www.autocar.co.uk/rss/suzuki",                      "category": "auto", "scrape_gallery": True},

    # ── General automotive — Motor1 brand feeds (gallery-enabled) ────────────
    {"name": "Motor1 Mercedes",       "url": "https://www.motor1.com/rss/articles/make/mercedes-benz/",   "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Audi",           "url": "https://www.motor1.com/rss/articles/make/audi/",            "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Porsche",        "url": "https://www.motor1.com/rss/articles/make/porsche/",         "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Ferrari",        "url": "https://www.motor1.com/rss/articles/make/ferrari/",         "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Tesla",          "url": "https://www.motor1.com/rss/articles/make/tesla/",           "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Lamborghini",    "url": "https://www.motor1.com/rss/articles/make/lamborghini/",    "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 McLaren",        "url": "https://www.motor1.com/rss/articles/make/mclaren/",         "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Bentley",        "url": "https://www.motor1.com/rss/articles/make/bentley/",         "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Rolls-Royce",    "url": "https://www.motor1.com/rss/articles/make/rolls-royce/",     "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Bugatti",        "url": "https://www.motor1.com/rss/articles/make/bugatti/",         "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Aston Martin",   "url": "https://www.motor1.com/rss/articles/make/aston-martin/",    "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Toyota",         "url": "https://www.motor1.com/rss/articles/make/toyota/",          "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Honda",          "url": "https://www.motor1.com/rss/articles/make/honda/",           "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Ford",           "url": "https://www.motor1.com/rss/articles/make/ford/",            "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Chevrolet",      "url": "https://www.motor1.com/rss/articles/make/chevrolet/",       "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Nissan",         "url": "https://www.motor1.com/rss/articles/make/nissan/",          "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Mazda",          "url": "https://www.motor1.com/rss/articles/make/mazda/",           "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Subaru",         "url": "https://www.motor1.com/rss/articles/make/subaru/",          "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 VW",             "url": "https://www.motor1.com/rss/articles/make/volkswagen/",      "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Volvo",          "url": "https://www.motor1.com/rss/articles/make/volvo/",           "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Mini",           "url": "https://www.motor1.com/rss/articles/make/mini/",            "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Hyundai",        "url": "https://www.motor1.com/rss/articles/make/hyundai/",         "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Kia",            "url": "https://www.motor1.com/rss/articles/make/kia/",             "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Lexus",          "url": "https://www.motor1.com/rss/articles/make/lexus/",           "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Acura",          "url": "https://www.motor1.com/rss/articles/make/acura/",           "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Cadillac",       "url": "https://www.motor1.com/rss/articles/make/cadillac/",        "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Genesis",        "url": "https://www.motor1.com/rss/articles/make/genesis/",         "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Maserati",       "url": "https://www.motor1.com/rss/articles/make/maserati/",        "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Alfa Romeo",     "url": "https://www.motor1.com/rss/articles/make/alfa-romeo/",      "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Jaguar",         "url": "https://www.motor1.com/rss/articles/make/jaguar/",          "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Land Rover",     "url": "https://www.motor1.com/rss/articles/make/land-rover/",      "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Ram",            "url": "https://www.motor1.com/rss/articles/make/ram/",             "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Jeep",           "url": "https://www.motor1.com/rss/articles/make/jeep/",            "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Buick",          "url": "https://www.motor1.com/rss/articles/make/buick/",           "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Chrysler",       "url": "https://www.motor1.com/rss/articles/make/chrysler/",        "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Dodge",          "url": "https://www.motor1.com/rss/articles/make/dodge/",           "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 GMC",            "url": "https://www.motor1.com/rss/articles/make/gmc/",             "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Mitsubishi",     "url": "https://www.motor1.com/rss/articles/make/mitsubishi/",      "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Infiniti",       "url": "https://www.motor1.com/rss/articles/make/infiniti/",        "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Suzuki",         "url": "https://www.motor1.com/rss/articles/make/suzuki/",          "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Peugeot",        "url": "https://www.motor1.com/rss/articles/make/peugeot/",         "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Renault",        "url": "https://www.motor1.com/rss/articles/make/renault/",         "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Citroen",        "url": "https://www.motor1.com/rss/articles/make/citroen/",         "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Fiat",           "url": "https://www.motor1.com/rss/articles/make/fiat/",            "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Skoda",          "url": "https://www.motor1.com/rss/articles/make/skoda/",           "category": "auto", "scrape_gallery": True},
    {"name": "Motor1 Seat",           "url": "https://www.motor1.com/rss/articles/make/seat/",            "category": "auto", "scrape_gallery": True},

    # ── General automotive — Electrek brand guides (EV-focused) ──────────────
    {"name": "Electrek Tesla",        "url": "https://electrek.co/guides/tesla/feed/",                    "category": "auto"},
    {"name": "Electrek Mercedes EQ",  "url": "https://electrek.co/guides/mercedes-benz/feed/",            "category": "auto"},
    {"name": "Electrek Audi e-tron",  "url": "https://electrek.co/guides/audi/feed/",                     "category": "auto"},
    {"name": "Electrek Porsche",      "url": "https://electrek.co/guides/porsche/feed/",                  "category": "auto"},
    {"name": "Electrek Ford EV",      "url": "https://electrek.co/guides/ford/feed/",                     "category": "auto"},
    {"name": "Electrek Rivian",       "url": "https://electrek.co/guides/rivian/feed/",                   "category": "auto"},
    {"name": "Electrek Lucid",        "url": "https://electrek.co/guides/lucid/feed/",                    "category": "auto"},
    {"name": "Electrek Hyundai",      "url": "https://electrek.co/guides/hyundai/feed/",                  "category": "auto"},
    {"name": "Electrek Kia EV",       "url": "https://electrek.co/guides/kia/feed/",                      "category": "auto"},
    {"name": "Electrek GM",           "url": "https://electrek.co/guides/gm/feed/",                       "category": "auto"},
    {"name": "Electrek Chevrolet",    "url": "https://electrek.co/guides/chevrolet/feed/",                "category": "auto"},
    {"name": "Electrek Nissan",       "url": "https://electrek.co/guides/nissan/feed/",                   "category": "auto"},
    {"name": "Electrek Fisker",       "url": "https://electrek.co/guides/fisker/feed/",                   "category": "auto"},
    {"name": "Electrek Polestar",     "url": "https://electrek.co/guides/polestar/feed/",                 "category": "auto"},
    {"name": "Electrek Volvo",        "url": "https://electrek.co/guides/volvo/feed/",                    "category": "auto"},
    {"name": "Electrek EV",           "url": "https://electrek.co/guides/ev/feed/",                       "category": "auto"},

]

# ─────────────────────────────────────────────────────────────────────────────
# BMW classification keywords
# ─────────────────────────────────────────────────────────────────────────────
BMW_STRONG_KEYWORDS: list[str] = [
    "bmw", "bimmer", "beemer", "beamer",
    "бмв", "баварски",
    "bmw motorrad", "bmw m", "bmw i",
    "alpina",
    "neue klasse", "neueklasse",
    "ring taxi",
    "bimmercode", "ista",
]

BMW_MODEL_PATTERNS: list[re.Pattern] = [
    re.compile(r"(?<![A-Za-z0-9])M(?:Power|Performance|Division)(?![A-Za-z0-9])", re.I),
    re.compile(r"(?<![A-Za-z0-9])M[2-8](?![A-Za-z0-9])"),
    re.compile(r"(?<![A-Za-z0-9])XM(?![A-Za-z0-9])"),
    re.compile(r"(?<![A-Za-z0-9])X[1-7](?![A-Za-z0-9])"),
    re.compile(r"(?<![A-Za-z0-9])iX[1-3]?(?![A-Za-z0-9])"),
    re.compile(r"(?<![A-Za-z0-9])i[3-8](?![A-Za-z0-9])"),
    re.compile(r"(?<![A-Za-z0-9])(?:G20|G80|G82|G87|F90|G60|G70|G99|G30|G11|F30|F80|F82|F87|E30|E36|E46|E39|E60|F10|F15|G05|F25|G01)(?![A-Za-z0-9])"),
    re.compile(r"(?<![A-Za-z0-9])(?:N55|B58|S58|S63|B48|S68|S55|N52|S65|B38|B46)(?![A-Za-z0-9])"),
    re.compile(r"(?<![A-Za-z0-9])xDrive(?![A-Za-z0-9])", re.I),
    re.compile(r"(?<![A-Za-z0-9])(?:Valvetronic|VANOS)(?![A-Za-z0-9])", re.I),
    re.compile(r"(?<![A-Za-z0-9])(?:Nürburgring|Nurburgring)(?![A-Za-z0-9])"),
    re.compile(r"(?<![A-Za-z0-9])[1-8]\s+series(?![A-Za-z0-9])", re.I),
]


def is_bmw_relevant(title: str, summary: str) -> bool:
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
    "daewoo",
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
#
# IMPORTANT (2026-06 fix): the previous version used `/img/(?!uploads)` which
# FALSE-POSITIVELY matched real content photos at paths like
# `/img/gallery/article-name/l-intro-...jpg` (Jalopnik). That regex has been
# removed and replaced with more targeted chrome-only patterns.
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
        r"-logo[-_]?",            # foo-logo.png, foo-logo-2x.png
        r"_logo\b",

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
        r"-avatar\b",
        r"-author\b",

        # Placeholders, blanks, transparent spacers
        r"/blank\.",
        r"placeholder",
        r"\btransparent\b",
        r"\b16x9-tr\b",
        r"\bdefault[-_]?image\b",
        r"\bno[-_]?image\b",
        r"\bmissing[-_]?image\b",
        r"default-electrek-related-guide",   # Electrek placeholder PNG

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

        # Theme & site chrome (only explicit chrome paths — NOT /img/ in general,
        # because Jalopnik & others host real content photos at /img/gallery/)
        r"/wp-content/themes/",
        r"/wp-content/plugins/",
        r"/wp-includes/",
        r"/wp-content/themes/[^/]+/images/",
        r"/themes?/[^/]+/images/",
        r"/templates?/[^/]+/images/",
        r"/assets/images/",
        r"/assets/img/",
        r"/assets/dist/",
        r"/static/images/",
        r"/static/dist/",
        r"/dist/images/",
        r"/img/icons?/",
        r"/img/social/",
        r"/img/logo",

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
    if url.startswith("data:"):
        return True
    # Tiny dimension hints in query (?w=1&h=1, ?resize=1x1, etc.)
    q = parse_qs(urlparse(url).query)
    for k in ("w", "width", "h", "height"):
        if k in q and q[k]:
            try:
                if int(q[k][0]) <= 32:
                    return True
            except ValueError:
                pass
    # Small WordPress size suffix like "-90x90.jpg", "-32x32.png"
    if re.search(r"-(\d{1,2})x(\d{1,2})\.(?:jpg|jpeg|png|webp|gif)(?:\?|$)", url, re.I):
        return True
    # WordPress author/profile pics
    if re.search(r"wp-content/uploads/.*(?:avatar|profile|author)", url, re.I):
        return True
    for pat in GARBAGE_URL_PATTERNS:
        if pat.search(url):
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# HTTP helpers
# ─────────────────────────────────────────────────────────────────────────────
def fetch_url(url: str, want_html: bool = False) -> tuple[int | None, bytes | None, str | None]:
    headers = HTML_HEADERS if want_html else HTTP_HEADERS
    try:
        r = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT if not want_html else HTML_TIMEOUT)
        return r.status_code, r.content, None
    except Exception as e:
        return None, None, str(e)


def extract_image(entry: Any) -> str | None:
    """Try every standard RSS image location. Returns None if no image found."""
    candidates: list[str] = []

    for enc in getattr(entry, "enclosures", []) or []:
        href = enc.get("href", "")
        if href:
            t = enc.get("type", "").lower()
            if t.startswith("image") or any(href.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp")):
                candidates.append(href)
    for m in getattr(entry, "media_content", []) or []:
        url = m.get("url", "")
        if url:
            candidates.append(url)
    for m in getattr(entry, "media_thumbnail", []) or []:
        url = m.get("url", "")
        if url:
            candidates.append(url)
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
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def parse_date(entry: Any) -> str:
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
# ─────────────────────────────────────────────────────────────────────────────
class _ImgCollector(HTMLParser):
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
    p = urlparse(url)
    path = re.sub(r"-\d+x\d+(?=\.\w+$)", "", p.path)
    return f"{p.scheme}://{p.netloc}{path}"


def _image_size_hint(url: str) -> int:
    q = parse_qs(urlparse(url).query)
    for k in ("resize", "fit", "w", "width"):
        if k in q and q[k]:
            m = re.search(r"(\d+)", q[k][0])
            if m:
                return int(m.group(1))
    m = re.search(r"-(\d+)x(\d+)\.\w+$", urlparse(url).path)
    if m:
        return int(m.group(1)) * int(m.group(2))
    return 9999


def extract_gallery_from_html(html_text: str, base_url: str, lead_image: str | None) -> list[str]:
    parser = _ImgCollector()
    try:
        parser.feed(html_text)
    except Exception:
        return []

    grouped: dict[str, list[str]] = {}
    for u in parser.urls:
        full = urljoin(base_url, u)
        if not re.search(r"\.(jpg|jpeg|png|webp)(\?|$)", full, re.I):
            continue
        if is_garbage_image(full):
            continue
        b = _base_image_url(full)
        grouped.setdefault(b, []).append(full)

    if lead_image:
        lead_b = _base_image_url(lead_image)
        grouped.pop(lead_b, None)

    chosen: list[str] = []
    for variants in grouped.values():
        best = max(variants, key=_image_size_hint)
        chosen.append(best)

    def rank(u: str) -> tuple[int, int]:
        path = urlparse(u).path.lower()
        premium = 1 if any(s in path for s in ("/uploads/", "/mgl/", "/images/", "/media/", "/hmg-prod/")) else 0
        return (premium, _image_size_hint(u))

    chosen.sort(key=rank, reverse=True)
    return chosen[: MAX_IMAGES_PER_ITEM - 1]


def scrape_article_images(url: str, lead_image: str | None) -> list[str]:
    if not url:
        return []
    status, content, err = fetch_url(url, want_html=True)
    if status != 200 or not content:
        return []
    try:
        try:
            text = content.decode("utf-8", errors="replace")
        except Exception:
            text = content.decode("latin-1", errors="replace")
        return extract_gallery_from_html(text, url, lead_image)
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Fetching
# ─────────────────────────────────────────────────────────────────────────────
def fetch_one(source: dict[str, Any]) -> list[dict[str, Any]]:
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
        if len(summary) > 600:
            summary = summary[:597].rsplit(" ", 1)[0] + "…"

        link = getattr(entry, "link", "") or ""
        image = extract_image(entry)
        published = parse_date(entry)

        combined = f"{title} {summary}".lower()
        if any(bl in combined for bl in BLOCKLIST):
            continue

        # ── Photo-quality guard ─────────────────────────────────────────────
        # Requirement: every item in the JSON output MUST have a quality photo.
        # 1. Drop items whose only image is garbage (logo/icon/tracker/etc.)
        # 2. Drop items with NO image at all — no photoless articles allowed
        if not image:
            continue
        if is_garbage_image(image):
            continue

        is_bmw = is_bmw_relevant(title, summary)

        items.append({
            "id": item_id(link, title),
            "title": title,
            "summary": summary,
            "url": link,
            "image": image or "",
            "images": [image] if image else [],
            "source": name,
            "source_url": base_url,
            "category": category,
            "published": published,
            "is_bmw": is_bmw,
        })

    # ── Gallery scraping (multi-photo) ────────────────────────────────────
    if scrape_gallery and items:
        to_scrape = items[:MAX_GALLERY_SCRAPE_PER_SOURCE]
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(scrape_article_images, it["url"], it["image"]): it for it in to_scrape}
            for fut in as_completed(futures):
                it = futures[fut]
                try:
                    extra = fut.result()
                except Exception:
                    extra = []
                if extra:
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
    all_items: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=10) as pool:
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
    if not published_iso:
        return True
    try:
        dt = datetime.fromisoformat(published_iso.replace("Z", "+00:00"))
    except Exception:
        return True
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    return dt >= cutoff


def dedup(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for it in items:
        existing = by_id.get(it["id"])
        if existing is None:
            by_id[it["id"]] = it
            continue
        if len(it.get("images", [])) > len(existing.get("images", [])):
            by_id[it["id"]] = it
        elif len(it.get("images", [])) == len(existing.get("images", [])) and \
             len(it["summary"]) > len(existing["summary"]):
            by_id[it["id"]] = it
    return list(by_id.values())


def sort_newest_first(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(it: dict[str, Any]) -> tuple[int, str]:
        p = it.get("published", "")
        return (0 if p else 1, p or "")
    return sorted(items, key=key, reverse=True)


# ─────────────────────────────────────────────────────────────────────────────
# Output
# ─────────────────────────────────────────────────────────────────────────────
def build_output(items: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    sources_used = sorted({it["source"] for it in items})
    multi_photo = sum(1 for it in items if len(it.get("images", [])) > 1)
    return {
        "kind": kind,
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
    log.info("Sources: %d total (%d BMW + %d auto, %d with gallery scraping)",
             len(SOURCES),
             sum(1 for s in SOURCES if s["category"] == "bmw"),
             sum(1 for s in SOURCES if s["category"] == "auto"),
             sum(1 for s in SOURCES if s.get("scrape_gallery")))
    log.info("=" * 70)

    repo_root = Path(__file__).resolve().parent
    data_dir = repo_root / "data"

    raw = fetch_all(SOURCES)
    log.info("Total raw items fetched: %d", len(raw))
    if not raw:
        log.error("No items fetched from any source — aborting")
        return 1

    deduped = dedup(raw)
    log.info("After dedup: %d items", len(deduped))

    # Recency filter: different windows for BMW vs auto.
    # BMW-relevant items are rarer so we widen the window; auto items are
    # plentiful so we keep the window tight to avoid stale news.
    recent_bmw = [it for it in deduped if is_recent(it["published"], BMW_MAX_AGE_DAYS)]
    recent_auto = [it for it in deduped if it["category"] != "bmw" and is_recent(it["published"], AUTO_MAX_AGE_DAYS)]
    log.info("After recency filter: BMW-window(%dd)=%d, Auto-window(%dd)=%d",
             BMW_MAX_AGE_DAYS, len(recent_bmw), AUTO_MAX_AGE_DAYS, len(recent_auto))

    bmw_items = [it for it in recent_bmw if it["is_bmw"]]
    auto_items = recent_auto
    log.info("Split: BMW=%d, Auto=%d", len(bmw_items), len(auto_items))

    def image_first_key(it: dict[str, Any]) -> tuple[int, int, str]:
        n_imgs = len(it.get("images", []))
        has_img = 0 if n_imgs > 0 else 1
        return (has_img, -n_imgs, "")

    bmw_items_sorted = sort_newest_first(sorted(bmw_items, key=image_first_key))
    auto_items_sorted = sort_newest_first(sorted(auto_items, key=image_first_key))

    bmw_items_sorted = bmw_items_sorted[:BMW_OUTPUT_CAP]
    auto_items_sorted = auto_items_sorted[:AUTO_OUTPUT_CAP]

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

    write_json(data_dir / "bmw-news.json", build_output(bmw_clean, "bmw"))
    write_json(data_dir / "auto-news.json", build_output(auto_clean, "auto"))

    log.info("=" * 70)
    log.info("Run complete. BMW=%d, Auto=%d", len(bmw_clean), len(auto_clean))
    log.info("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
