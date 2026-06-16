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
RSS sources (15 feeds)
        │
        ▼
   fetch_news.py        ← feedparser + requests, parallel fetch
        │
        ├─► normalize items (title, summary, url, image, published)
        ├─► dedup by id = sha256(url + title)
        ├─► recency filter (≤ 7 days old)
        ├─► classify: is_bmw_relevant() → STRONG kw OR ≥2 model-pattern matches
        │
        ▼
   ┌──────────────┐    ┌──────────────┐
   │ bmw-news.json│    │auto-news.json│
   └──────────────┘    └──────────────┘
```

### Sources (36 hand-tested feeds — 2025-06)

All sources return RSS feeds with quality photos embedded (`media:content`,
enclosures, or `<img>` in summary). BMW-file output uses 22 BMW-specific feeds;
auto-file output uses 14 general automotive feeds.

**BMW-specific (22 feeds)**
| Source | URL |
|---|---|
| BMW Blog (main) | `https://bmwblog.com/feed/` |
| BMW Blog M | `https://bmwblog.com/category/bmw-m/feed/` |
| BMW Blog i | `https://bmwblog.com/category/bmw-i/feed/` |
| BMW Blog X | `https://bmwblog.com/category/bmw-x/feed/` |
| BMW Blog 3 | `https://bmwblog.com/category/bmw-3-series/feed/` |
| BMW Blog 5 | `https://bmwblog.com/category/bmw-5-series/feed/` |
| BMW Blog M2 | `https://bmwblog.com/category/bmw-m2/feed/` |
| BMW Blog M3 | `https://bmwblog.com/category/bmw-m3/feed/` |
| BMW Blog M4 | `https://bmwblog.com/category/bmw-m4/feed/` |
| BMW Blog M5 | `https://bmwblog.com/category/bmw-m5/feed/` |
| BMW Blog M8 | `https://bmwblog.com/category/bmw-m8/feed/` |
| BMW Blog Concepts | `https://bmwblog.com/category/concepts/feed/` |
| BMW Blog Alpina tag | `https://bmwblog.com/tag/alpina/feed/` |
| BMW Blog Mini tag | `https://bmwblog.com/tag/mini/feed/` |
| BMW Blog X5 tag | `https://bmwblog.com/tag/x5/feed/` |
| BMW Blog X7 tag | `https://bmwblog.com/tag/x7/feed/` |
| BMW Blog XM tag | `https://bmwblog.com/tag/xm/feed/` |
| BimmerFile | `https://bimmerfile.com/feed/` |
| BimmerToday DE | `https://www.bimmertoday.de/feed/` |
| Car and Driver BMW | `https://www.caranddriver.com/rss/bmw.xml` |
| CarScoops BMW | `https://www.carscoops.com/tag/bmw/feed/` |
| Electrek BMW | `https://electrek.co/guides/bmw/feed/` |

**General automotive (14 feeds)**
| Source | URL |
|---|---|
| CarScoops | `https://www.carscoops.com/feed/` |
| Car and Driver (all) | `https://www.caranddriver.com/rss/all.xml` |
| Car and Driver News | `https://www.caranddriver.com/rss/news.xml` |
| Car and Driver Reviews | `https://www.caranddriver.com/rss/reviews.xml` |
| Autocar | `https://www.autocar.co.uk/rss` |
| AutoExpress | `https://www.autoexpress.co.uk/rss` |
| CarExpert | `https://carexpert.com.au/feed/` |
| Jalopnik | `https://jalopnik.com/rss` |
| The Drive | `https://www.thedrive.com/feed` |
| Electrek | `https://electrek.co/feed/` |
| InsideEVs | `https://insideevs.com/feed/` |
| Motorious | `https://motorious.com/feed/` |
| GM Authority | `https://gmauthority.com/blog/feed/` |
| CarBuzz | `https://carbuzz.com/feed/` |

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

### Output JSON schema

```jsonc
{
  "kind": "bmw",                       // or "auto"
  "generated_at": "2025-06-16T15:53:00+00:00",
  "generated_at_human": "2025-06-16 15:53 UTC",
  "total_items": 42,
  "sources_used": ["BMW Blog", "BimmerFile", ...],
  "sources_count": 8,
  "items": [
    {
      "id": "a3f8c1d9...",             // sha256(url+title)[:16]
      "title": "Inside The Radically New 2027 BMW X5",
      "summary": "Short excerpt (≤600 chars)...",
      "url": "https://bmwblog.com/...",
      "image": "https://bmwblog.com/wp-content/uploads/...",
      "source": "BMW Blog",
      "source_url": "https://bmwblog.com",
      "published": "2025-06-16T13:04:00+00:00"
    }
  ]
}
```

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
| `HTTP_TIMEOUT` | `20` | Per-request timeout (seconds) |
| `MAX_ITEMS_PER_FEED` | `30` | Cap per source so one noisy feed can't dominate |
| `MAX_AGE_DAYS` | `7` | Items older than this are dropped (items with no date are kept) |

Cap on output size: BMW file = top 200, Auto file = top 250.

---

## Adding a source

1. Test the feed URL with `curl -A "Mozilla/5.0 ..." <url> | head -100`
2. Verify it returns HTTP 200 and includes images in entries
3. Add a row to `SOURCES` in `fetch_news.py` with `category` = `"bmw"` or `"auto"`
4. Run `python fetch_news.py` locally to verify

If a source starts 404'ing, the parser logs a warning and continues with the
rest — no manual intervention needed.
