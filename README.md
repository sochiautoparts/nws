# nws — Automotive News Parser

Hourly automotive news aggregator that fetches BMW-specific and general automotive
news from curated RSS sources and publishes them as two JSON files:

- **[`data/auto-news.json`](data/auto-news.json)** — General world automotive news (industry, EVs, classics, reviews, brand-specific tags)
- **[`data/bmw-news.json`](data/bmw-news.json)** — BMW-themed news (models, M-division, Neue Klasse, i-series, Motorrad, Alpina, …)

A GitHub Actions workflow runs `fetch_news.py` **every hour** (at `HH:05`) and
commits the refreshed JSON files back to the repo.

---

## How it works

```
RSS sources (192 feeds — 51 BMW + 141 general auto)
        │
        ▼
   fetch_news.py        ← feedparser + requests, parallel fetch
        │
        ├─► normalize items (title, summary, url, image, published)
        ├─► extract lead image (enclosure / media:content / media:thumbnail / <img>)
        ├─► ★ garbage-photo guard — drop items whose only image is a logo/icon/tracker
        ├─► ★ multi-photo scrape — for 70+ gallery-enabled sources, fetch article page
        │                       and extract up to 5 additional gallery images
        ├─► dedup by id = sha256(url + title)
        ├─► recency filter:
        │     • BMW items  → ≤ 90 days
        │     • Auto items → ≤ 30 days
        ├─► classify: is_bmw_relevant() → STRONG kw OR ≥2 model-pattern matches
        │
        ▼
   ┌──────────────┐    ┌──────────────┐
   │ bmw-news.json│    │auto-news.json│
   │  top 500     │    │  top 500     │
   └──────────────┘    └──────────────┘
```

### Output size (typical run)

| File | Items | Sources | Multi-photo items |
|---|---:|---:|---:|
| `bmw-news.json` | ~250 | ~45 | ~10 |
| `auto-news.json` | ~500 (cap) | ~60 | ~90 |

Each multi-photo item carries up to 6 distinct image URLs (lead first).

---

## Sources (243 hand-tested feeds — 2026-06)

All sources return RSS feeds with quality photos embedded (`media:content`,
enclosures, or `<img>` in summary). BMW-file output uses 70 BMW-specific feeds;
auto-file output uses 173 general automotive feeds.

Every source was individually tested for: (1) working RSS endpoint (HTTP 200),
(2) valid feed XML, (3) ≥ 3 quality photos per 10 entries. Sources that
returned malformed XML, 0 quality photos, or only 1–2 photos were removed.

### BMW-specific (70 feeds)

- **BMW Blog (55 sub-feeds)** — main + categories (1/3/4/5/6/Z4/M2-M8/i5/X1-X7/X/Motorrad/concepts) + tags (1-8 series, M/M2-M8, X/X1-X7/XM, i/i3-i8/iX/iX1/iX3, Alpina, Mini, Mini Cooper, Rolls-Royce, 7-series)
- **Other BMW sites (15)** — BimmerFile, BimmerToday DE, Car and Driver BMW, CarScoops BMW, Electrek BMW, Electrek BMW iX, Autocar BMW, Autocar BMW M, Autocar BMW i, Motor1 BMW

### General automotive (173 feeds)

- **Broad feeds (35)** — CarScoops, Car and Driver (all/News/Reviews), Autocar, AutoExpress, CarExpert, Jalopnik, The Drive, Electrek, InsideEVs, Motorious, GM Authority, CarBuzz, Motor1 (all/News/Reviews/Classics), Road & Track (all/News/Reviews), HotCars, TopSpeed, AutoWeek News, Hagerty Media, BarnFinds, ClassicCars Journal, Nissan News, 5koleso RU, Honda News, Engadget Auto, The Verge Transportation, What Car, CarThrottle, Bring a Trailer, Bike EXIF, Hooniverse, Speed Academy, Track Day, Kolesa RU
- **CarScoops brand tags (32)** — Audi, Porsche, Ferrari, Tesla, Mercedes, Lamborghini, McLaren, Bentley, Rolls, Bugatti, Aston, Corvette, Toyota, Honda, Ford, Chevy, Nissan, Mazda, Subaru, VW, Volvo, Hyundai, Kia, Lexus, Mini, Jaguar, Land Rover, Maserati, Alfa Romeo, Genesis, Cadillac, Dodge
- **Car and Driver brand feeds (9)** — Toyota, Audi, Porsche, Lexus, Chevrolet, Hyundai, Mitsubishi, Subaru, Lotus *(26 broken/no-photo/weak feeds removed)*
- **Autocar brand subfeeds (30)** — Porsche, Mercedes, Audi, Tesla, Toyota, Honda, Ford, VW, Hyundai, Kia, Mazda, Nissan, Renault, Peugeot, Land Rover, Jaguar, Lexus, Mini, Ferrari, Lamborghini, Bentley, Rolls, McLaren, Aston Martin, Maserati, Alfa Romeo, Citroen, Fiat, Skoda, Suzuki
- **Motor1 brand feeds (46)** — Mercedes, Audi, Porsche, Ferrari, Tesla, Lamborghini, McLaren, Bentley, Rolls-Royce, Bugatti, Aston Martin, Toyota, Honda, Ford, Chevrolet, Nissan, Mazda, Subaru, VW, Volvo, Mini, Hyundai, Kia, Lexus, Acura, Cadillac, Genesis, Maserati, Alfa Romeo, Jaguar, Land Rover, Ram, Jeep, Buick, Chrysler, Dodge, GMC, Mitsubishi, Infiniti, Suzuki, Peugeot, Renault, Citroen, Fiat, Skoda, Seat
- **Electrek brand guides (16)** — Tesla, Mercedes EQ, Audi e-tron, Porsche, Ford EV, Rivian, Lucid, Hyundai, Kia EV, GM, Chevrolet, Nissan, Fisker, Polestar, Volvo, EV

Sources marked **gallery-enabled** (144 feeds) have `scrape_gallery: true` —
the parser fetches the article page for the top 3 most recent items and
extracts up to 5 additional gallery photos (so each item ends up with up to
6 images total).

### Sources removed (quality control — 2026-06)

| Source | Reason |
|---|---|
| Teslarati | < 1 quality photo per 10 entries |
| Green Car Reports | Persistent HTTP 403, never produces output |
| AutoWise | 0 quality photos |
| BMW Blog i7 (category) | 0 quality photos |
| 10 Car and Driver brand feeds (Nissan, VW, Genesis, Buick, Ram, Cadillac, Chrysler, GMC, Dodge, Kia) | Malformed XML feed |
| 7 Car and Driver brand feeds (Acura, Bugatti, Honda, Corvette, Land Rover, Mazda, Mini) | 0 quality photos |
| 9 Car and Driver brand feeds (Mercedes, Ferrari, Bentley, Lamborghini, Ford, McLaren, Lincoln, Infiniti, Volvo, Jeep) | Only 1–2 quality photos per 10 entries |

---

## Garbage-photo guard

Every extracted image URL is filtered through `is_garbage_image()` which rejects:

| Category | Examples |
|---|---|
| Logos / icons | `/logo`, `/icons/`, `/favicon`, `/sprite`, `foo-logo.png`, `_logo` |
| Trackers / ad pixels | `doubleclick`, `google-analytics`, `facebook.com/tr`, `/pixel`, `/beacon` |
| Avatars / author bylines | `/avatar`, `/authors/`, `gravatar`, `/profile-pic`, `-avatar`, `-author` |
| Placeholders / spacers | `placeholder`, `transparent`, `16x9-tr.png`, `default-image`, `no-image`, `/blank.` |
| Tiny dimensions | `1x1`, `?w=1&h=1`, `-90x90.jpg`, `-32x32.png`, any `w`/`h` ≤ 32 |
| Social media buttons | `/social/`, `twitter.com`, `instagram.com`, `youtube.com`, `facebook.com`, `tiktok.com` |
| Theme / site chrome | `/wp-content/themes/`, `/wp-content/plugins/`, `/wp-includes/`, `/assets/images/`, `/assets/dist/`, `/dist/images/`, `/img/icons/`, `/img/social/`, `/img/logo` |
| Shopping / affiliate | `amazon.com`, `shopify`, `/shop/`, `/store/` |
| GIFs (almost always animated icons in feeds) | `.gif` |
| Emoji | `emoji`, `/emoticons/` |
| Site-specific placeholders | `default-electrek-related-guide` |

If an item's only image is garbage, **the item is dropped entirely** —
guaranteeing the JSON files only contain real content photos.

**2026-06 quality guarantee:** items with **no image at all** are now also
dropped. Every single item in the JSON output is guaranteed to have at least
one quality photo. Zero photoless articles.

**2026-06 fix:** the previous version used `/img/(?!uploads)` regex which
false-positively matched real content photos at paths like
`/img/gallery/article-name/l-intro-...jpg` (Jalopnik). That regex has been
removed and replaced with more targeted chrome-only patterns.

---

## Multi-photo scraping

For sources flagged `scrape_gallery: true`, the parser:

1. Takes the top 3 most recent items from the RSS feed
2. Fetches each article's HTML page
3. Extracts `<img>` URLs (including `srcset`, `data-src`, `data-lazy-src`)
4. Groups them by base URL (stripping query strings & WordPress size suffixes
   like `-1024x576.jpg`), and picks the **largest** variant per group
5. Filters out garbage URLs (same filter as above)
6. Caps at 6 total images per item (lead image first, then 5 extras)

This means top-tier sources (Motor1, Road & Track, CarScoops, Car and Driver,
HotCars, TopSpeed, Hagerty, CarBuzz, Autocar, AutoExpress, etc.) provide rich
multi-photo news items.

---

## Output JSON schema

```jsonc
{
  "kind": "bmw",                       // or "auto"
  "generated_at": "2026-06-17T17:38:00+00:00",
  "generated_at_human": "2026-06-17 17:38 UTC",
  "total_items": 248,
  "sources_used": ["BMW Blog", "BimmerFile", ...],
  "sources_count": 45,
  "multi_photo_items": 9,              // count of items with >1 image
  "items": [
    {
      "id": "a3f8c1d9...",             // sha256(url+title)[:16]
      "title": "Bovensiepen 05 GT driven: 790bhp successor to the Alpina B5",
      "summary": "Short excerpt (≤600 chars)...",
      "url": "https://www.autocar.co.uk/...",
      "image": "https://images.cdn.autocar.co.uk/.../bovensiepen-05-gt-29.jpg",  // lead image (backwards-compat)
      "images": [                      // array of image URLs (lead first)
        "https://images.cdn.autocar.co.uk/.../bovensiepen-05-gt-29.jpg",
        "https://images.cdn.autocar.co.uk/.../bovensiepen-05-gt-28.jpg",
        "https://images.cdn.autocar.co.uk/.../bovensiepen-05-gt-review-12.jpg",
        ...                           // up to 6 entries
      ],
      "source": "Autocar BMW",
      "source_url": "https://www.autocar.co.uk",
      "published": "2026-06-15T10:30:00+00:00"
    }
  ]
}
```

**Field notes:**
- `image` (string) — the lead image; kept for backwards compatibility with
  consumers that expect a single image.
- `images` (array of strings) — list of all quality image URLs for this item,
  lead image first. Always has ≥1 entry for items with photos. Capped at 6
  entries. Use this field for multi-photo UIs.

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
| `HTML_TIMEOUT` | `12` | Per-article-page timeout for gallery scraping |
| `MAX_ITEMS_PER_FEED` | `25` | Cap per source so one noisy feed can't dominate |
| `BMW_MAX_AGE_DAYS` | `90` | BMW items older than this are dropped |
| `AUTO_MAX_AGE_DAYS` | `30` | Auto items older than this are dropped |
| `MAX_GALLERY_SCRAPE_PER_SOURCE` | `3` | How many article pages to fetch per gallery source |
| `MAX_IMAGES_PER_ITEM` | `6` | Cap on the `images` array (incl. lead image) |
| `BMW_OUTPUT_CAP` | `500` | Max items in bmw-news.json |
| `AUTO_OUTPUT_CAP` | `500` | Max items in auto-news.json |

BMW file uses a wider 90-day recency window because BMW-relevant news is rarer
than general auto news; Auto file uses a tighter 30-day window to keep content
fresh.

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
