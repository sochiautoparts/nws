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
RSS sources (338 feeds — 82 BMW + 256 general auto)
        │
        ▼
   fetch_news.py        ← feedparser + requests, parallel fetch
        │
        ├─► normalize items (title, summary, url, image, published)
        ├─► extract lead image (enclosure / media:content / media:thumbnail / <img>)
        ├─► ★ garbage-photo guard — drop items whose only image is a logo/icon/tracker
        ├─► ★ multi-photo scrape — for 206 gallery-enabled sources, fetch article page
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
| `bmw-news.json` | ~347 | ~88 | ~27 |
| `auto-news.json` | ~500 (cap) | ~66 | ~99 |

Each multi-photo item carries up to 6 distinct image URLs (lead first).

---

## Sources (338 hand-tested feeds — 2026-06)

All sources return RSS feeds with quality photos embedded (`media:content`,
enclosures, or `<img>` in summary). BMW-file output uses 82 BMW-specific feeds;
auto-file output uses 256 general automotive feeds. 206 feeds are
**gallery-enabled** (`scrape_gallery: true`) so the parser fetches each article
page and extracts up to 5 additional photos.

Every source was individually tested for: (1) working RSS endpoint (HTTP 200),
(2) valid feed XML, (3) ≥ 3 quality photos per 10 entries. Sources that
returned malformed XML, 0 quality photos, or only 1–2 photos were removed.
Testing is reproducible via `feed_tester.py` (see [Adding a source](#adding-a-source)).

### BMW-specific (82 feeds)

- **BMW Blog (61 sub-feeds)** — main + categories (1/3/4/5/6/Z4/M2-M8/i5/X1-X7/X/Motorrad/concepts) + tags (1-8 series, M/M2-M8, X/X1-X7/XM, i/i3-i8/iX/iX1/iX3, Motorrad, concepts, Alpina, Mini, Mini Cooper, Rolls-Royce, 7-series)
- **Other BMW sites (15)** — BimmerFile, BimmerToday DE, Car and Driver BMW, CarScoops BMW, Electrek BMW, Electrek BMW iX, Autocar BMW, Autocar BMW M, Autocar BMW i, Motor1 BMW

### General automotive (175 feeds)

- **Broad feeds (40)** — CarScoops, Car and Driver (all/News/Reviews), Autocar, AutoExpress, CarExpert, Jalopnik, The Drive, Electrek, InsideEVs, Motorious, GM Authority, CarBuzz, Motor1 (all/News/Reviews/Classics), HotCars, TopSpeed, AutoWeek News, Hagerty Media, BarnFinds, ClassicCars Journal, Nissan News, 5koleso RU, Honda News, Engadget Auto, The Verge Transportation, What Car, CarThrottle, Bring a Trailer, Bike EXIF, Hooniverse, Speed Academy, Track Day, Kolesa RU, Auto.Mail.RU, Motoring Research, TopSpeed main, GM Authority News, Carscoops News
- **CarScoops brand tags (32)** — Audi, Porsche, Ferrari, Tesla, Mercedes, Lamborghini, McLaren, Bentley, Rolls, Bugatti, Aston, Corvette, Toyota, Honda, Ford, Chevy, Nissan, Mazda, Subaru, VW, Volvo, Hyundai, Kia, Lexus, Mini, Jaguar, Land Rover, Maserati, Alfa Romeo, Genesis, Cadillac, Dodge
- **Car and Driver brand feeds (9)** — Toyota, Audi, Porsche, Lexus, Chevrolet, Hyundai, Mitsubishi, Subaru, Lotus *(26 broken/no-photo/weak feeds removed)*
- **Autocar brand subfeeds (30)** — Porsche, Mercedes, Audi, Tesla, Toyota, Honda, Ford, VW, Hyundai, Kia, Mazda, Nissan, Renault, Peugeot, Land Rover, Jaguar, Lexus, Mini, Ferrari, Lamborghini, Bentley, Rolls, McLaren, Aston Martin, Maserati, Alfa Romeo, Citroen, Fiat, Skoda, Suzuki
- **Motor1 brand feeds (46)** — Mercedes, Audi, Porsche, Ferrari, Tesla, Lamborghini, McLaren, Bentley, Rolls-Royce, Bugatti, Aston Martin, Toyota, Honda, Ford, Chevrolet, Nissan, Mazda, Subaru, VW, Volvo, Mini, Hyundai, Kia, Lexus, Acura, Cadillac, Genesis, Maserati, Alfa Romeo, Jaguar, Land Rover, Ram, Jeep, Buick, Chrysler, Dodge, GMC, Mitsubishi, Infiniti, Suzuki, Peugeot, Renault, Citroen, Fiat, Skoda, Seat
- **Electrek brand guides (16)** — Tesla, Mercedes EQ, Audi e-tron, Porsche, Ford EV, Rivian, Lucid, Hyundai, Kia EV, GM, Chevrolet, Nissan, Fisker, Polestar, Volvo, EV

Sources marked **gallery-enabled** (206 feeds) have `scrape_gallery: true` —
the parser fetches the article page for the top 3 most recent items and
extracts up to 5 additional gallery photos (so each item ends up with up to
6 images total).

### Sources removed (quality control — 2026-06)

| Source | Reason |
|---|---|
| Road & Track (all/News/Reviews) | Removed per request — replaced with broader quality sources |
| Teslarati | < 1 quality photo per 10 entries |
| Green Car Reports | Persistent HTTP 403, never produces output |
| AutoWise | 0 quality photos |
| BMW Blog i7 (category) | 0 quality photos |
| 10 Car and Driver brand feeds (Nissan, VW, Genesis, Buick, Ram, Cadillac, Chrysler, GMC, Dodge, Kia) | Malformed XML feed |
| 7 Car and Driver brand feeds (Acura, Bugatti, Honda, Corvette, Land Rover, Mazda, Mini) | 0 quality photos |
| 9 Car and Driver brand feeds (Mercedes, Ferrari, Bentley, Lamborghini, Ford, McLaren, Lincoln, Infiniti, Volvo, Jeep) | Only 1–2 quality photos per 10 entries |

### Sources added (2026-06 expansion r9 — +22 feeds)

A further 22 hand-tested quality-photo feeds were added, broadening
global/regional coverage and classic-American depth. All verified with
`feed_tester.py` (most scored 9–10/10 quality photos).

| Group | Feeds | Category | Quality |
|---|---|---|---|
| Asia — Paul Tan (MY/SG), Gaadiwaadi, RushLane, MotorBeam, Motoring World, Team-BHP Forum (IN), Response.jp, Clicccar (JP) | 8 | auto | 7–10/10 photos |
| Latin America — Auto Bild ES, Quatro Rodas, Auto Esporte, Auto Esporte Carros (BR) | 4 | auto | 10/10 photos each |
| Classic & vintage — Sports Car Digest, Vintage Motorsport | 2 | auto | 9–10/10 photos |
| EV — Electric Cars Report | 1 | auto | 10/10 photos |
| Motorious classic-American brand tags — Muscle, Buick, Oldsmobile, Pontiac | 4 | auto | 9–10/10 photos |
| Motor1 / Autocar categories — Design, Long-term Tests, Used Cars | 3 | auto | 10/10 photos each |

**Result (typical run after r9):** `bmw-news.json` ≈ 347 items from 88 sources,
`auto-news.json` = 500 items (cap) from 66 sources with **99 multi-photo items**
(up from 77 after r8). 0 photoless articles in either file.

### Sources added (2026-06 expansion r8 — +65 feeds)

A further 65 hand-tested quality-photo feeds were added, grouped by category.
Every feed below was verified with `feed_tester.py`: HTTP 200, valid XML,
≥3 entries, and ≥3 of the first-10 entries carry a quality photo (most scored
10/10). This expands coverage into motorsport, classic/muscle cars, international
outlets, and additional brand/model tags on existing photo-rich platforms.

| Group | Feeds | Category | Quality |
|---|---|---|---|
| CarScoops BMW tags (Alpina, BMW M, M3, M4, M5, iX) | 6 | bmw | 10/10 photos each |
| CarScoops specialty tags (AMG, Brabus, Hennessey, Koenigsegg, Lucid, Pagani, Polestar, Range Rover, Rimac, Rivian, Supercar) | 11 | auto | 10/10 photos each |
| Motorious tags (Classic, Porsche, Corvette, Ford, Chevrolet, Mustang, Camaro, Dodge, Charger, Challenger, Auction) | 11 | auto | 7–10/10 photos |
| Hagerty categories (News, Driving, People, Video) | 4 | auto | 10/10 photos each |
| Motor1 categories (Industry, Technology) | 2 | auto | 9–10/10 photos |
| Autocar categories (News, Reviews, First Drives, Group Tests) | 4 | auto | 10/10 photos each |
| AutoExpress categories (News, Reviews) | 2 | auto | 10/10 photos each |
| Autosport series (All, F1, WEC, WRC, MotoGP, IndyCar, NASCAR) | 7 | auto | 10/10 photos each |
| Motorsport.com series (All, F1, WEC, WRC, MotoGP, IndyCar, NASCAR, Formula E) | 8 | auto | 9–10/10 photos |
| Other motorsport (RACER, Crash.net, PlanetF1, Racecar Engineering) | 4 | auto | 10/10 photos each |
| International (Motor.es ES, Diariomotor ES, CarWale IN, AutoWeek NL, Practical Motoring AU) | 5 | auto | 10/10 photos each |
| Manufacturer press (Acura News) | 1 | auto | 10/10 photos |

**Result (typical run after r8):** `bmw-news.json` ≈ 341 items from 84 sources
(was ~250/45); `auto-news.json` = 500 items (cap) from 59 sources with 77
multi-photo items. 0 photoless articles in either file.

### Sources added (2026-06 expansion r5–r7)

| Source | Category | Quality |
|---|---|---|
| Auto.Mail.RU | auto (Russian-language) | 10/10 quality photos |
| Motoring Research | auto (UK) | 10/10 quality photos |
| TopSpeed main | auto | 10/10 quality photos |
| GM Authority News | auto | 9/10 quality photos |
| Carscoops News | auto | 10/10 quality photos |
| BMW Blog M3 tag | bmw | 10/10 quality photos |
| BMW Blog M4 tag | bmw | 10/10 quality photos |
| BMW Blog M5 tag | bmw | 10/10 quality photos |
| BMW Blog M8 tag | bmw | 10/10 quality photos |
| BMW Blog Motorrad tag | bmw | 10/10 quality photos |
| BMW Blog Concepts tag | bmw | 8/8 quality photos |

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

The fastest way to test a candidate feed is the included tester:

```bash
pip install feedparser requests
python feed_tester.py --urls https://example.com/feed/ https://other.com/rss.xml
# → prints a TSV table: URL, HTTP status, entries, quality-photo count, sample image, PASS/NO
# A feed PASSES if: HTTP 200, valid feed, ≥3 entries, ≥3 of first-10 have a quality photo.
```

Or test manually:

1. Test the feed URL with `curl -A "Mozilla/5.0 ..." <url> | head -100`
2. Verify it returns HTTP 200 and includes images in entries (enclosure /
   `media:content` / `media:thumbnail` / `<img>` in summary)
3. Add a row to `SOURCES` in `fetch_news.py` with `category` = `"bmw"` or `"auto"`
4. If the source has multi-photo galleries on article pages, add
   `"scrape_gallery": True` to the source dict
5. Run `python fetch_news.py` locally to verify

If a source starts 404'ing, the parser logs a warning and continues with the
rest — no manual intervention needed.
