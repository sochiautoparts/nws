#!/usr/bin/env python3
"""
Feed tester — given candidate RSS feed URLs (one per line on stdin, or via
--urls), report for each: HTTP status, feed validity, # entries, # entries
with a quality (non-garbage) photo, and a sample image URL.

Usage:
    python feed_tester.py --urls url1 url2 ...
    cat urls.txt | python feed_tester.py

Prints a compact TSV summary to stdout and a detailed JSON report to stderr.
A feed PASSES if: HTTP 200, valid feed, >=3 entries, and >=3 of those have a
quality photo. Pass/fail is printed in the SUMMARY line.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, parse_qs

import feedparser
import requests

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = 20

GARBAGE = [
    re.compile(p, re.IGNORECASE) for p in [
        r"/logo", r"/icons?/", r"/favicon", r"/sprite", r"\blogo\b",
        r"-logo", r"_logo", r"/pixel", r"/tracker", r"/beacon",
        r"doubleclick", r"google-analytics", r"facebook\.com/tr",
        r"googletagmanager", r"scorecardresearch", r"/ads?/", r"\bad[-_]?server",
        r"/avatar", r"/authors?/", r"/profile", r"gravatar", r"-avatar", r"-author",
        r"/blank\.", r"placeholder", r"\btransparent\b", r"\bdefault[-_]?image\b",
        r"\bno[-_]?image\b", r"\b1x1\b", r"width[=:]1\b", r"height[=:]1\b",
        r"/social/", r"twitter\.com/", r"instagram\.com/", r"youtube\.com/",
        r"tiktok\.com/", r"facebook\.com/", r"linkedin\.com/", r"pinterest\.com/",
        r"reddit\.com/", r"/newsletter/", r"/subscribe/", r"/comment",
        r"/wp-content/themes/", r"/wp-content/plugins/", r"/wp-includes/",
        r"/assets/images/", r"/assets/img/", r"/assets/dist/", r"/static/images/",
        r"/dist/images/", r"/img/icons?/", r"/img/social/", r"/img/logo",
        r"emoji", r"amazon\.com/", r"shopify", r"/shop/", r"/store/", r"\.gif($|\?)",
    ]
]


def is_garbage(url: str) -> bool:
    if not url or url.startswith("data:"):
        return True
    q = parse_qs(urlparse(url).query)
    for k in ("w", "width", "h", "height"):
        if k in q and q[k]:
            try:
                if int(q[k][0]) <= 32:
                    return True
            except ValueError:
                pass
    if re.search(r"-(\d{1,2})x(\d{1,2})\.(?:jpg|jpeg|png|webp|gif)(?:\?|$)", url, re.I):
        return True
    for p in GARBAGE:
        if p.search(url):
            return True
    return False


class _ImgFinder(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "img":
            return
        d = {k.lower(): (v or "") for k, v in attrs}
        for key in ("src", "data-src", "data-lazy-src", "data-original", "data-cfsrc"):
            v = d.get(key)
            if v:
                self.urls.append(v)
        ss = d.get("srcset") or d.get("data-srcset")
        if ss:
            self.urls.extend(p.strip().split(" ")[0] for p in ss.split(",") if p.strip())


def extract_image(entry, base_url: str) -> str | None:
    cands = []
    for enc in getattr(entry, "enclosures", []) or []:
        href = enc.get("href", "")
        if href:
            t = enc.get("type", "").lower()
            if t.startswith("image") or any(href.lower().endswith(e) for e in (".jpg", ".jpeg", ".png", ".webp")):
                cands.append(href)
    for m in getattr(entry, "media_content", []) or []:
        u = m.get("url", "")
        if u:
            cands.append(u)
    for m in getattr(entry, "media_thumbnail", []) or []:
        u = m.get("url", "")
        if u:
            cands.append(u)
    for field in ("summary", "description", "content"):
        val = getattr(entry, field, None)
        if not val:
            continue
        if isinstance(val, list) and val:
            val = val[0].get("value", "")
        for m in re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', str(val)):
            cands.append(urljoin(base_url, m.group(1)))
    for c in cands:
        if not is_garbage(c):
            return c
    return cands[0] if cands else None


def test_feed(url: str) -> dict:
    result = {"url": url, "status": None, "error": None, "entries": 0,
              "quality_photos": 0, "sample_image": None, "pass": False,
              "title": None}
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        result["status"] = r.status_code
        if r.status_code != 200:
            result["error"] = f"HTTP {r.status_code}"
            return result
        feed = feedparser.parse(r.content)
        if feed.bozo and not feed.entries:
            result["error"] = f"malformed: {getattr(feed, 'bozo_exception', '?')}"
            return result
        if not feed.entries:
            result["error"] = "no entries"
            return result
        base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
        result["title"] = getattr(feed.feed, "title", "")
        result["entries"] = len(feed.entries)
        qcount = 0
        sample = None
        for e in feed.entries[:10]:
            img = extract_image(e, base)
            if img and not is_garbage(img):
                qcount += 1
                if not sample:
                    sample = img
        result["quality_photos"] = qcount
        result["sample_image"] = sample
        result["pass"] = (result["entries"] >= 3 and qcount >= 3)
        return result
    except Exception as e:
        result["error"] = str(e)[:200]
        return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--urls", nargs="*", default=[])
    ap.add_argument("--workers", type=int, default=12)
    args = ap.parse_args()
    urls = list(args.urls)
    if not urls:
        urls = [ln.strip() for ln in sys.stdin if ln.strip() and not ln.startswith("#")]
    print(f"URL\tSTATUS\tENTRIES\tQUALITY\tSAMPLE\tPASS\tERROR")
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        future_map = {pool.submit(test_feed, u): u for u in urls}
        for fut in as_completed(future_map):
            r = fut.result()
            results.append(r)
            print(f"{r['url']}\t{r['status']}\t{r['entries']}\t{r['quality_photos']}"
                  f"\t{r['sample_image'] or ''}\t{'YES' if r['pass'] else 'NO'}"
                  f"\t{r['error'] or ''}")
            sys.stdout.flush()
    # Sort results to match input order for readability in stderr summary
    order = {u: i for i, u in enumerate(urls)}
    results.sort(key=lambda r: order.get(r["url"], 9999))
    passed = [r for r in results if r["pass"]]
    marginal = [r for r in results if not r["pass"] and r["quality_photos"] >= 1 and r["entries"] >= 3]
    print(f"\n=== SUMMARY: {len(passed)}/{len(results)} PASSED, {len(marginal)} MARGINAL ===", file=sys.stderr)
    for r in passed:
        print(f"PASS\t{r['title']}\t{r['url']}\t{r['quality_photos']}/10 photos\t{r['sample_image']}", file=sys.stderr)
    for r in marginal:
        print(f"MARGINAL\t{r['title']}\t{r['url']}\t{r['quality_photos']}/10 photos", file=sys.stderr)
    json.dump(results, sys.stderr, indent=2, default=str)


if __name__ == "__main__":
    main()
