# ATS Job Scraper

A focused job-hunting pipeline that finds **fresh, high-signal job postings** by talking directly to the public APIs of seven major ATS platforms — no fragile HTML scraping, no LinkedIn login, no scraper-blocker arms race.

It works in three steps:

1. **Discover** — Use SerpAPI (Google Search) to find which companies host job boards on each ATS.
2. **Fetch** — Call each ATS's public job-board API for structured listings (title, location, description, posted date).
3. **Rank & filter** — Score each posting by title / location / tech stack / posting age, drop noise, and export a sortable HTML report + CSV.

**Supported ATSs**: Ashby, Greenhouse, Lever, SmartRecruiters, Workable, Rippling, Workday.

> ℹ️ **Why SerpAPI, not Bing?** Microsoft has announced Bing Search API will be retired in August 2026, and new accounts can no longer be created. SerpAPI is the simplest replacement.

---

## Setup

### 1. Get a SerpAPI key (free tier, no credit card)

1. Sign up at [serpapi.com](https://serpapi.com) — Google or GitHub login works.
2. Copy your key from the dashboard.
3. Export it:

```bash
export SERPAPI_KEY="your_key_here"     # macOS / Linux
set SERPAPI_KEY=your_key_here          # Windows CMD
$env:SERPAPI_KEY="your_key_here"       # Windows PowerShell
```

### 2. Install dependencies and bootstrap personal files

```bash
pip install -r requirements.txt

# Bootstrap the gitignored personal files from their .example templates
cp config.example.json                config.json
cp JobApplicationTracker.example.csv  JobApplicationTracker.csv
cp discovered_slugs.example.json      discovered_slugs.json
cp skipped_companies.example.json     skipped_companies.json
cp scripts/run_daily.sh.example       scripts/run_daily.sh
cp scripts/run_flask.sh.example       scripts/run_flask.sh

# Edit config.json with your profile, target titles, locations, etc.
# Edit the two wrapper scripts: replace <PROJECT_DIR> with the absolute project path.
chmod +x scripts/*.sh
```

### 3. Run

```bash
python ats_scraper.py        # full run (~12 minutes with concurrency enabled)
```

Output files:

- **`jobs_results.html`** — visual report, open in a browser.
- **`jobs_results.csv`** — tabular, importable into Excel / Notion / Google Sheets.
- **`discovered_slugs.json`** — discovered company list, reused on subsequent runs.
- **`apply_assistant.html`** — interactive batch-apply dashboard (regenerated automatically at the end of every run).

---

## Customizing `config.json` for different careers / regions

The scraper has zero career- or region-specific hardcoding — it runs whatever filters and weights you put in `config.json`. Three example flavors to show the range:

### Example 1 — US software engineer (default flavor)

```jsonc
{
  "scraper": {
    "search_locations": ["Seattle", "San Francisco", "Remote"],
    "target_titles": { "data engineer": 50, "software engineer": 35 },
    "strict_filter_titles": ["software engineer"],
    "tech_keywords": ["python", "spark", "airflow", "aws"],
    "locations": {
      "preferred_area": { "seattle": 25, "bellevue": 25 },
      "allowed":        { "san francisco": 20, "remote": 15 },
      "exclude_regions":        ["india", "london"],
      "exclude_remote_regions": ["canada", "europe", "apac"]
    },
    "strict_rules": { "require_keywords_in_desc": ["python"], "max_years_experience": 5 }
  }
}
```

### Example 2 — UK-based product designer

```jsonc
{
  "scraper": {
    "search_locations": ["London", "Manchester", "Remote"],
    "target_titles": { "product designer": 50, "ux researcher": 45, "design lead": 40 },
    "strict_filter_titles": [],
    "tech_keywords": ["figma", "sketch", "user research", "a/b testing"],
    "locations": {
      "preferred_area": { "london": 25 },
      "allowed":        { "manchester": 20, "remote": 15 },
      "exclude_regions":        ["india", "san francisco", "seattle"],
      "exclude_remote_regions": ["us", "apac", "latam"]
    },
    "strict_rules": { "require_keywords_in_desc": [], "max_years_experience": 0 }
  }
}
```

### Example 3 — APAC marketing role

```jsonc
{
  "scraper": {
    "search_locations": ["Singapore", "Hong Kong", "Remote"],
    "target_titles": { "growth marketer": 50, "marketing manager": 45 },
    "strict_filter_titles": [],
    "tech_keywords": ["hubspot", "salesforce", "google analytics", "seo"],
    "locations": {
      "preferred_area": { "singapore": 25, "hong kong": 25 },
      "allowed":        { "tokyo": 20, "remote": 15 },
      "exclude_regions":        ["united states", "europe"],
      "exclude_remote_regions": ["us", "uk", "europe"]
    },
    "strict_rules": { "require_keywords_in_desc": [], "max_years_experience": 0 }
  }
}
```

`config.json` is gitignored — only `config.example.json` ships in the repo.

---

## Common commands

| Command | What it does |
|---|---|
| `python ats_scraper.py` | Full run (slug discovery + ATS scraping) |
| `python ats_scraper.py --no-search` | Skip Google discovery, reuse saved slugs (fast, ~12 min, zero SerpAPI cost) |
| `python ats_scraper.py --ats ashby` | Scrape only one ATS |
| `python ats_scraper.py --export html` | Generate HTML report only |
| `python ats_scraper.py --add-slug ashby openai modal-labs` | Manually add company slugs |
| `python ats_scraper.py --max-age-days 7` | Override the posting-age cutoff |
| `python apply_assistant.py --serve` | Start the apply-assistant Flask server at http://localhost:8765/ |

**Recommended cadence:**

- Once monthly: full run (rediscover new companies via SerpAPI).
- Daily: `--no-search` refresh — free, ~12 minutes thanks to per-ATS concurrency.

---

## SerpAPI quota (free tier = 100 searches / month)

A full discovery run costs roughly:

```
(# query templates) × (# search locations) × (# ATS platforms)
       7            ×          3            ×       7           ≈ 147 searches
```

That exceeds the free tier in one run, so monthly discovery typically requires a Starter plan ($25/mo, 1000 searches) or trimming the query templates / locations. Use `--no-search` for daily refreshes — zero searches consumed.

---

## Scoring (0–100)

Score weights and location lists are all defined in `config.json`. The table below shows the *shape* of the signals; actual numbers come from your config.

| Signal | Source | Points |
|---|---|---|
| Title matches a `target_titles` keyword | per-keyword weight | the dict value (e.g. +50) |
| Title contains " ii" / " iii" / " 2" / " 3" | hardcoded | +5 |
| Location matches a `locations.preferred_area` city | per-city weight | the dict value (e.g. +25) |
| Location matches a `locations.allowed` keyword | per-keyword weight | the dict value (e.g. +15) |
| Each `tech_keywords` hit in description | hardcoded | +3 each, capped at +25 |

Hard excludes (drop the job entirely, score → 0):

- Title contains any `exclude_title_words` substring.
- Title matches no `target_titles` entry.
- Location matches any `locations.exclude_regions` substring.
- For remote postings, location matches any `locations.exclude_remote_regions` substring.
- Description contains any `exclude_desc_keywords` substring.
- For titles in `strict_filter_titles`: description missing any `strict_rules.require_keywords_in_desc` entry, or the posting says more than `strict_rules.max_years_experience` years required.

Jobs scoring below `min_score` are also dropped.

---

## Manually adding known companies

```bash
# Add multiple Ashby slugs at once
python ats_scraper.py --add-slug ashby openai anthropic modal-labs replicate

# Then refresh
python ats_scraper.py --no-search
```

A starter pack of well-known AI-startup Ashby slugs:

```
openai, anthropic, scale-ai, cohere, modal-labs, replicate,
together-ai, anyscale, weights-biases, labelbox, snorkel-ai,
cleanlab, comet-ml, activeloop, qdrant
```

---

## Scheduling

### Simple cron

```bash
# macOS / Linux crontab — daily refresh at 08:00 (free, no SerpAPI cost)
0 8 * * * cd /path/to/scraper && python ats_scraper.py --no-search >> daily.log 2>&1

# Monthly full discovery on the 1st at 09:00
0 9 1 * * cd /path/to/scraper && python ats_scraper.py >> monthly.log 2>&1
```

### macOS launchd (production setup with native notifications)

The `scripts/*.example` wrapper scripts plus two `.plist` LaunchAgent templates cover the production setup:

- **Daily scrape** at 08:00, wrapped in `caffeinate -i` so an idle Mac doesn't sleep mid-run, followed by a clickable `terminal-notifier` popup that points at the Flask dashboard.
- **Flask server** (`apply_assistant.py --serve`) kept alive via `RunAtLoad + KeepAlive` so the notification's click target is always reachable.

Steps:

1. `brew install terminal-notifier`
2. `cp scripts/run_daily.sh.example scripts/run_daily.sh && chmod +x scripts/run_daily.sh`
3. Edit `scripts/run_daily.sh`: replace `<PROJECT_DIR>` with the absolute path to your checkout.
4. Repeat (2)–(3) for `scripts/run_flask.sh`.
5. Drop your LaunchAgent plists into `~/Library/LaunchAgents/` and load with `launchctl bootstrap gui/$(id -u) <plist-path>`.

---

## Troubleshooting

### Chrome / Edge / Arc: "Open Selected in Tabs" only opens one tab

Chromium-based browsers treat "multiple tabs from one user gesture" as a popup
flood — by default the first call succeeds and the rest are silently blocked.
**There is no JS workaround**; popups must be allowed at the browser level.

One-time fix:

1. Click "Open Selected in Tabs".
2. A crossed-out window icon 🔲 appears at the right edge of the URL bar (next to the reload button).
3. Click that icon → select **"Always allow pop-ups and redirects from http://localhost:8765"** → Done.
4. Click "Open Selected in Tabs" again — all selected tabs open at once, and the permission is remembered for future sessions.

Safari: Safari menu → Settings → Websites → Pop-up Windows → set `localhost` to **Allow**.
