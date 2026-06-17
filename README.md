# nws — Automotive News Parser

Hourly automotive news aggregator that fetches BMW-specific and general automotive
news from curated RSS sources and publishes them as two JSON files:

- **`data/bmw-news.json`** — BMW-themed news (models, M-division, Neue Klasse, i-series, Motorrad, Alpina, …)
- **`data/auto-news.json`** — General world automotive news (industry, EVs, classics, reviews, …)

A GitHub Actions workflow runs `fetch_news.py` **every hour** (at `HH:05`) and
commits the refreshed JSON files back to the repo.

---

## How it works

```
RSS sources (61 feeds — 24 BMW + 37 general auto)
        │
        ▼
   fetch_news.py        ← feedparser + requests, parallel fetch
        │
        ├─► normalize items (title, summary, url, image, published)
        ├─► extract lead image (enclosure / media:content / media:thumbnail / <img>)
        ├─► ★ garbage-photo guard — drop items whose only image is a logo/icon/tracker
        ├─► ★ multi-photo scrape — for top-tier sources, fetch article page
        │                       and extract up to 5 additional gallery images
        ├─► dedup by id = sha256(url + title)
        ├─► recency filter (≤ 7 days old)
        ├─► classify: is_bmw_relevant() → STRONG kw OR ≥2 model-pattern matches
        │
        ▼
   ┌──────────────┐    ┌──────────────┐
   │ bmw-news.json│    │auto-news.json│
   └──────────────┘    └──────────────┘
```

### Sources (61 hand-tested feeds — 2026-06)

All sources return RSS feeds with quality photos embedded (`media:content`,
enclosures, or `<img>` in summary). BMW-file output uses 24 BMW-specific feeds;
auto-file output uses 37 general automotive feeds.

**BMW-specific (24 feeds)**
| Source | URL | Gallery? |
|---|---|:---:|
| BMW Blog (main) | `https://bmwblog.com/feed/` | |
| BMW Blog M | `https://bmwblog.com/category/bmw-m/feed/` | |
| BMW Blog i | `https://bmwblog.com/category/bmw-i/feed/` | |
| BMW Blog X | `https://bmwblog.com/category/bmw-x/feed/` | |
| BMW Blog 3 | `https://bmwblog.com/category/bmw-3-series/feed/` | |
| BMW Blog 5 | `https://bmwblog.com/category/bmw-5-series/feed/` | |
| BMW Blog M2 | `https://bmwblog.com/category/bmw-m2/feed/` | |
| BMW Blog M3 | `https://bmwblog.com/category/bmw-m3/feed/` | |
| BMW Blog M4 | `https://bmwblog.com/category/bmw-m4/feed/` | |
| BMW Blog M5 | `https://bmwblog.com/category/bmw-m5/feed/` | |
| BMW Blog M8 | `https://bmwblog.com/category/bmw-m8/feed/` | |
| BMW Blog Concepts | `https://bmwblog.com/category/concepts/feed/` | |
| BMW Blog Alpina tag | `https://bmwblog.com/tag/alpina/feed/` | |
| BMW Blog Mini tag | `https://bmwblog.com/tag/mini/feed/` | |
| BMW Blog X5 tag | `https://bmwblog.com/tag/x5/feed/` | |
| BMW Blog X7 tag | `https://bmwblog.com/tag/x7/feed/` | |
| BMW Blog XM tag | `https://bmwblog.com/tag/xm/feed/` | |
| BimmerFile | `https://bimmerfile.com/feed/` | |
| BimmerToday DE | `https://www.bimmertoday.de/feed/` | |
| Car and Driver BMW | `https://www.caranddriver.com/rss/bmw.xml` | ✓ |
| CarScoops BMW | `https://www.carscoops.com/tag/bmw/feed/` | ✓ |
| Electrek BMW | `https://electrek.co/guides/bmw/feed/` | |
| Autocar BMW | `https://www.autocar.co.uk/rss/bmw` | ✓ |
| Motor1 BMW | `https://www.motor1.com/rss/articles/make/bmw/` | ✓ |

**General automotive (37 feeds)**
| Source | URL | Gallery? |
|---|---|:---:|
| CarScoops | `https://www.carscoops.com/feed/` | ✓ |
| Car and Driver (all) | `https://www.caranddriver.com/rss/all.xml` | ✓ |
| Car and Driver News | `https://www.caranddriver.com/rss/news.xml` | ✓ |
| Car and Driver Reviews | `https://www.caranddriver.com/rss/reviews.xml` | ✓ |
| Autocar | `https://www.autocar.co.uk/rss` | ✓ |
| AutoExpress | `https://www.autoexpress.co.uk/rss` | ✓ |
| CarExpert | `https://carexpert.com.au/feed/` | |
| Jalopnik | `https://jalopnik.com/rss` | |
| The Drive | `https://www.thedrive.com/feed` | |
| Electrek | `https://electrek.co/feed/` | |
| InsideEVs | `https://insideevs.com/feed/` | |
| Motorious | `https://motorious.com/feed/` | |
| GM Authority | `https://gmauthority.com/blog/feed/` | |
| CarBuzz | `https://carbuzz.com/feed/` | ✓ |
| **Motor1** | `https://www.motor1.com/rss/articles/all/` | ✓ |
| **Motor1 News** | `https://www.motor1.com/rss/articles/category/news/` | ✓ |
| **Motor1 Reviews** | `https://www.motor1.com/rss/articles/category/reviews/` | ✓ |
| **Road & Track** | `https://www.roadandtrack.com/rss/all.xml` | ✓ |
| **Road & Track News** | `https://www.roadandtrack.com/rss/news.xml` | ✓ |
| **Road & Track Reviews** | `https://www.roadandtrack.com/rss/reviews.xml` | ✓ |
| **HotCars** | `https://www.hotcars.com/feed/` | ✓ |
| **TopSpeed** | `https://www.topspeed.com/feed/` | ✓ |
| **AutoWeek News** | `https://www.autoweek.com/rss/news/` | ✓ |
| **Hagerty Media** | `https://www.hagerty.com/media/feed/` | ✓ |
| **BarnFinds** | `https://barnfinds.com/feed/` | ✓ |
| **ClassicCars Journal** | `https://journal.classiccars.com/feed/` | ✓ |
| **CarScoops Audi** | `https://www.carscoops.com/tag/audi/feed/` | ✓ |
| **CarScoops Porsche** | `https://www.carscoops.com/tag/porsche/feed/` | ✓ |
| **CarScoops Ferrari** | `https://www.carscoops.com/tag/ferrari/feed/` | ✓ |
| **CarScoops Tesla** | `https://www.carscoops.com/tag/tesla/feed/` | ✓ |
| **Car and Driver Toyota** | `https://www.caranddriver.com/rss/toyota.xml` | ✓ |
| **Car and Driver Mercedes** | `https://www.caranddriver.com/rss/mercedes-benz.xml` | ✓ |
| **Car and Driver Audi** | `https://www.caranddriver.com/rss/audi.xml` | ✓ |
| **Car and Driver Porsche** | `https://www.caranddriver.com/rss/porsche.xml` | ✓ |
| **Car and Driver Ferrari** | `https://www.caranddriver.com/rss/ferrari.xml` | ✓ |
| **Car and Driver Lexus** | `https://www.caranddriver.com/rss/lexus.xml` | ✓ |
| **Autocar Porsche** | `https://www.autocar.co.uk/rss/porsche` | ✓ |

Sources marked **★ Gallery** have `scrape_gallery: true` — the parser fetches
the article page for the top 3 most recent items and extracts up to 5
additional gallery photos (so each item ends up with up to 6 images total).

### BMW classification

Two-tier matcher to avoid false positives like matching `"ix"` in `"six"`,
`"x5"` in `"EX5"`, or `"ista"` in `"assistant"`:

1. **Tier 1 (strong)** — `bmw`, `bimmer`, `beemer`, `бмв`, `alpina`,
   `neue klasse`, `bmw motorrad`, etc. — matched with `\b` word boundaries.
2. **Tier 2 (model)** — `M3`, `X5`, `i7`, `G80`, `B58`, `xDrive`,
   `Nürburgring`, `3 series`, etc. — matched with strict lookaround regexes
   so they must be standalone tokens, NOT substrings of other words. Requires
   **≥2 distinct** matches so a single coincidental hit isn't enough.

An item is BMW-relevant if Tier 1 matches OR Tier 2 has ≥2 distinct hits.

### Garbage-photo guard

Every extracted image URL is filtered through `is_garbage_image()` which rejects:

| Category | Examples |
|---|---|
| Logos / icons | `/logo`, `/icons/`, `/favicon`, `/sprite`, `logo` in URL |
| Trackers / ad pixels | `doubleclick`, `google-analytics`, `facebook.com/tr`, `/pixel`, `/beacon` |
| Avatars / author bylines | `/avatar`, `/authors/`, `gravatar`, `/profile-pic`, `/byline` |
| Placeholders / spacers | `placeholder`, `transparent`, `16x9-tr.png`, `default-image`, `no-image`, `/blank.` |
| Tiny dimensions | `1x1`, `?w=1&h=1`, `-90x90.jpg`, `-32x32.png`, any `w`/`h` ≤ 32 |
| Social media buttons | `/social/`, `twitter.com`, `instagram.com`, `youtube.com`, `facebook.com`, `tiktok.com` |
| Theme / site chrome | `/themes/`, `/wp-content/themes/`, `/wp-content/plugins/`, `/wp-includes/` |
| Shopping / affiliate | `amazon.com`, `shopify`, `/shop/`, `/store/` |
| GIFs (almost always animated icons in feeds) | `.gif` |
| Emoji | `emoji`, `/emoticons/` |

If an item's only image is garbage, **the item is dropped entirely** —
guaranteeing the JSON files only contain real content photos.

### Multi-photo scraping

For sources flagged `scrape_gallery: true`, the parser:

1. Takes the top 3 most recent items from the RSS feed
2. Fetches each article's HTML page
3. Extracts `<img>` URLs (including `srcset`, `data-src`, `data-lazy-src`)
4. Groups them by base URL (stripping query strings & WordPress size suffixes
   like `-1024x576.jpg`), and picks the **largest** variant per group
5. Filters out garbage URLs (same filter as above)
6. Caps at 6 total images per item (lead image first, then 5 extras)

This means top-tier sources (Motor1, Road & Track, CarScoops, Car and Driver,
HotCars, TopSpeed, Hagerty, etc.) provide rich multi-photo news items.

### Output JSON schema

```jsonc
{
  "kind": "bmw",                       // or "auto"
  "generated_at": "2026-06-17T08:03:00+00:00",
  "generated_at_human": "2026-06-17 08:03 UTC",
  "total_items": 88,
  "sources_used": ["BMW Blog", "BimmerFile", ...],
  "sources_count": 21,
  "multi_photo_items": 9,              // ★ NEW: count of items with >1 image
  "items": [
    {
      "id": "a3f8c1d9...",             // sha256(url+title)[:16]
      "title": "BMW's $580,000 V8 Shooting Brake Spied Undisguised",
      "summary": "Short excerpt (≤600 chars)...",
      "url": "https://www.carscoops.com/2026/06/bmw-speedtop-production-spied/",
      "image": "https://www.carscoops.com/wp-content/uploads/.../BMW-Speedtop-17-copy-1024x576.jpg",  // lead image (backwards-compat)
      "images": [                      // ★ NEW: array of image URLs (lead first)
        "https://www.carscoops.com/wp-content/uploads/.../BMW-Speedtop-17-copy-1024x576.jpg",
        "https://www.carscoops.com/wp-content/uploads/.../BMW-Speedtop-616-4-2048x1366.jpg",
        "https://www.carscoops.com/wp-content/uploads/.../BMW-Speedtop-616-5-2048x1366.jpg",
        "https://www.carscoops.com/wp-content/uploads/.../BMW-Speedtop-616-6-2048x1366.jpg",
        "https://www.carscoops.com/wp-content/uploads/.../BMW-Speedtop-616-7-2048x1366.jpg",
        "https://www.carscoops.com/wp-content/uploads/.../BMW-Speedtop-616-8-2048x1366.jpg"
      ],
      "source": "CarScoops BMW",
      "source_url": "https://www.carscoops.com",
      "published": "2026-06-17T07:30:00+00:00"
    }
  ]
}
```

**Field notes:**
- `image` (string) — the lead image; kept for backwards compatibility with
  consumers that expect a single image.
- `images` (array of strings) — list of all quality image URLs for this item,
  lead image first. Always has ≥1 entry (items with no quality image are
  dropped). Capped at 6 entries. Use this field for multi-photo UIs.

---

## Usage

### Local run

```bash
pip install -r requirements.txt
python fetch_news.py
# → writes data/bmw-news.json and data/auto-news.json
```

### GitHub Actions

The workflow at `.github/workflows/fetch-news.yml`:

- Runs automatically every hour at `HH:05 UTC`
- Can be triggered manually from the **Actions** tab ("Fetch Automotive News" → "Run workflow")
- Runs on every push that touches `fetch_news.py` / `requirements.txt` / the workflow itself
- Commits the refreshed JSON files back to `main` with message `chore(news): hourly refresh @ <timestamp>`
- Uses the built-in `GITHUB_TOKEN` — no extra secrets required

---

## Config knobs

Defined at the top of `fetch_news.py`:

| Constant | Default | Meaning |
|---|---|---|
| `HTTP_TIMEOUT` | `20` | Per-RSS-request timeout (seconds) |
| `HTML_TIMEOUT` | `15` | Per-article-page timeout for gallery scraping |
| `MAX_ITEMS_PER_FEED` | `30` | Cap per source so one noisy feed can't dominate |
| `MAX_AGE_DAYS` | `7` | Items older than this are dropped (items with no date are kept) |
| `MAX_GALLERY_SCRAPE_PER_SOURCE` | `3` | How many article pages to fetch per gallery source |
| `MAX_IMAGES_PER_ITEM` | `6` | Cap on the `images` array (incl. lead image) |

Cap on output size: BMW file = top 200, Auto file = top 250.

---

## Adding a source

1. Test the feed URL with `curl -A "Mozilla/5.0 ..." <url> | head -100`
2. Verify it returns HTTP 200 and includes images in entries (enclosure /
   `media:content` / `media:thumbnail` / `<img>` in summary)
3. Add a row to `SOURCES` in `fetch_news.py` with `category` = `"bmw"` or `"auto"`
4. If the source has multi-photo galleries on article pages, add
   `"scrape_gallery": True` to the source dict
5. Run `python fetch_news.py` locally to verify

If a source starts 404'ing, the parser logs a warning and continues with the
rest — no manual intervention needed.
