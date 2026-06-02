"""
ATS Job Scraper — SerpAPI (Google) + Direct ATS APIs
=====================================================
Strategy:
  1. Use SerpAPI Google Search (site: queries) to discover ATS company slugs
  2. Call Ashby / Greenhouse / Lever APIs directly for structured job data
  3. Score by title, location, and tech stack — export HTML + CSV

Setup:
  1. Get a FREE SerpAPI key (100 searches/month, no credit card needed):
     https://serpapi.com → Sign Up (use Google/GitHub login)
     Dashboard → copy your API Key

  2. Set your key:
     export SERPAPI_KEY="your_key_here"      # Mac/Linux
     set SERPAPI_KEY=your_key_here           # Windows

  Note: 36 searches used per full run (4 templates × 3 locations × 3 ATS)
        100 free searches/month = ~3 full discovery runs per month
        Use --no-search for weekly refresh (zero API calls needed)

Usage:
    python ats_scraper.py                     # full run
    python ats_scraper.py --ats ashby         # only Ashby
    python ats_scraper.py --no-search         # skip search, reuse saved slugs
    python ats_scraper.py --export html       # HTML only
    python ats_scraper.py --add-slug ashby openai anthropic  # manually add slugs
"""

import os
import sys
import requests
import json
import csv
import time
import re
import random
import threading
import argparse
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from dataclasses import dataclass, asdict, field
from typing import Optional
from pathlib import Path

# ──────────────────────────────────────────────────────────
# CONFIGURATION  — everything personal/tunable comes from config.json.
# Edit that file (gitignored), not this source. See config.example.json.
# ──────────────────────────────────────────────────────────

PROJECT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = PROJECT_DIR / "config.json"


def load_config() -> dict:
    """Load config.json. Strip any sibling keys starting with '//' so the
    example file can self-document inline without needing a JSONC parser."""
    if not CONFIG_FILE.exists():
        sys.exit(
            f"Missing {CONFIG_FILE.name}. "
            f"Copy config.example.json → config.json and fill in your values."
        )

    def _strip_comments(o):
        if isinstance(o, dict):
            return {k: _strip_comments(v) for k, v in o.items() if not k.startswith("//")}
        if isinstance(o, list):
            return [_strip_comments(v) for v in o]
        return o

    return _strip_comments(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))


_CFG = load_config()
_SCRAPER = _CFG["scraper"]

# SerpAPI key — get free key at https://serpapi.com (100 searches/month, no credit card)
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")

SEARCH_LOCATIONS      = _SCRAPER["search_locations"]
TARGET_TITLES         = _SCRAPER["target_titles"]
STRICT_FILTER_TITLES  = set(_SCRAPER.get("strict_filter_titles", []))
EXCLUDE_COMPANIES     = _SCRAPER["exclude_companies"]
EXCLUDE_TITLE_WORDS   = _SCRAPER["exclude_title_words"]
EXCLUDE_DESC_KEYWORDS = _SCRAPER.get("exclude_desc_keywords", [])
TECH_KEYWORDS         = _SCRAPER["tech_keywords"]
MIN_SCORE             = _SCRAPER["min_score"]
MAX_POSTING_AGE_DAYS  = _SCRAPER["max_posting_age_days"]
_LOC                  = _SCRAPER["locations"]
_STRICT               = _SCRAPER.get("strict_rules", {"require_keywords_in_desc": [], "max_years_experience": 0})

# Seconds to wait between ATS API calls (be polite)
API_DELAY = 0.5
# Seconds to wait between Bing search calls
SEARCH_DELAY = 0.5

TIMEOUT = 15

# Output files
SLUGS_FILE   = Path("discovered_slugs.json")
RESULTS_CSV  = Path("jobs_results.csv")
RESULTS_HTML = Path("jobs_results.html")
APPLIED_CSV  = Path("JobApplicationTracker.csv")  # already-applied jobs to exclude

# ──────────────────────────────────────────────────────────
# ATS PLATFORM CONFIGS
# ──────────────────────────────────────────────────────────

ATS_CONFIGS = {
    "ashby": {
        "search_site":    "jobs.ashbyhq.com",
        "url_pattern":    r"jobs\.ashbyhq\.com/([^/?#\s]+)",
        "api_template":   "https://api.ashbyhq.com/posting-api/job-board/{slug}",
    },
    "greenhouse": {
        "search_site":    "boards.greenhouse.io",
        "url_pattern":    r"boards\.greenhouse\.io/([^/?#\s]+)",
        "api_template":   "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
    },
    "lever": {
        "search_site":    "jobs.lever.co",
        "url_pattern":    r"jobs\.lever\.co/([^/?#\s]+)",
        "api_template":   "https://api.lever.co/v0/postings/{slug}?mode=json",
    },
    "smartrecruiters": {
        "search_site":    "jobs.smartrecruiters.com",
        "url_pattern":    r"jobs\.smartrecruiters\.com/([^/?#\s/]+)",
        "api_template":   "https://api.smartrecruiters.com/v1/companies/{slug}/postings",
    },
    "workable": {
        "search_site":    "apply.workable.com",
        "url_pattern":    r"apply\.workable\.com/([^/?#\s/]+)",
        "api_template":   "https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true",
    },
    "rippling": {
        "search_site":    "ats.rippling.com",
        "url_pattern":    r"ats\.rippling\.com/([^/?#\s]+)",
        "api_template":   "https://ats.rippling.com/{slug}/jobs",
    },
    "workday": {
        # Workday slugs are a triple stored as "tenant|wdN|site"
        # (e.g. "remitly|wd5|Remitly_Careers") because all three pieces vary per tenant.
        "search_site":    "myworkdayjobs.com",
        "url_pattern":    r"https?://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2,3}-[A-Z]{2}/)?([A-Za-z0-9_-]+?)(?:/|\?|$)",
        "api_template":   "https://{tenant}.{wdn}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs",
    },
}

# Per-ATS concurrent worker count for the outer slug loop. Default 1 = serial
# (current safe behavior). Bump individual entries after verifying no 429s.
ATS_CONCURRENCY = {
    "ashby":           5,   # verified: 1h23m → 43s
    "greenhouse":      5,   # verified: 38m → 12s
    "lever":           5,   # verified: 27m → 31s
    "smartrecruiters": 5,   # verified: 3h21m → 2.5min
    "workable":        5,   # verified: 3m → 8s
    "rippling":        5,   # verified: 2m → 10s
    "workday":         4,   # Phase 6 — POST endpoint + per-job detail, conservative
}

# Search query templates — {site} and {location} are filled in at runtime
QUERY_TEMPLATES = _SCRAPER["query_templates"]

# ──────────────────────────────────────────────────────────
# LOGGING
# ──────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("scraper.log"), logging.StreamHandler()],
)
log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────
# DATA MODEL
# ──────────────────────────────────────────────────────────

@dataclass
class JobListing:
    company:             str
    title:               str
    location:            str
    url:                 str
    ats:                 str
    description_snippet: str = ""
    tech_found:          str = ""   # comma-separated matched tech keywords
    date_found:          str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))
    relevance_score:     int = 0

# ──────────────────────────────────────────────────────────
# HTTP HELPER
# ──────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
}

# Thread-local requests.Session keeps the HTTPS connection pool per-thread,
# sidestepping cross-thread connection-pool contention under concurrency.
_thread_local = threading.local()

def _session() -> requests.Session:
    sess = getattr(_thread_local, "session", None)
    if sess is None:
        sess = requests.Session()
        _thread_local.session = sess
    return sess


def jitter_sleep(base: float = None, spread: float = 0.3):
    """Sleep ~base seconds with uniform jitter. Constant inter-request delays
    create lockstep request patterns under threading that some ATSs flag;
    randomizing dodges that."""
    if base is None:
        base = API_DELAY
    lo = max(0.05, base - spread)
    hi = base + spread
    time.sleep(random.uniform(lo, hi))


def get(url: str, params: dict = None, headers: dict = None) -> Optional[requests.Response]:
    h = {**HEADERS, **(headers or {})}
    sess = _session()
    for attempt in range(3):
        try:
            r = sess.get(url, headers=h, params=params, timeout=TIMEOUT)
            if r.status_code == 200:
                return r
            elif r.status_code == 429:
                wait = 10 * (attempt + 1)
                log.warning(f"Rate limited — waiting {wait}s...")
                time.sleep(wait)
            elif r.status_code in (401, 403):
                log.error(f"Auth error {r.status_code}: {url}")
                return None
            elif r.status_code == 404:
                return None
            else:
                log.debug(f"HTTP {r.status_code}: {url}")
                time.sleep(2)
        except requests.RequestException as e:
            log.debug(f"Request error ({attempt+1}/3): {e}")
            time.sleep(3)
    return None

# ──────────────────────────────────────────────────────────
# RELEVANCE SCORING
# ──────────────────────────────────────────────────────────

def score_job(title: str, location: str, description: str = "") -> tuple[int, str]:
    """
    Returns (score, matched_tech_keywords).
    Score 0 means the job should be excluded entirely.
    """
    t = title.lower().strip()
    l = location.lower()
    d = description.lower()

    # Hard excludes
    if any(ex in t for ex in EXCLUDE_TITLE_WORDS):
        return 0, ""

    # Title must match at least one target
    title_score = 0
    matched_title = ""
    for kw, pts in TARGET_TITLES.items():
        if kw in t:
            title_score = pts
            matched_title = kw
            break
    if title_score == 0:
        return 0, ""

    # Check if this is a "strict" title (needs additional gates from strict_rules)
    is_strict_title = matched_title in STRICT_FILTER_TITLES
    # If the title also contains "data", treat it as a data role (less strict)
    if is_strict_title and "data" in t:
        is_strict_title = False

    score = title_score

    # Small bump for mid-level indicators
    if any(s in t for s in [" ii", " iii", " 2", " 3"]):
        score += 5

    # Location filter — exclude any region in exclude_regions outright
    if any(x in l for x in _LOC.get("exclude_regions", [])):
        return 0, ""

    preferred_area    = _LOC.get("preferred_area", {})
    allowed_locations = _LOC.get("allowed", {})

    # Strict-filter titles are limited to the preferred_area; other titles can use the broader allowed set
    if is_strict_title:
        loc_score = 0
        for loc_kw, pts in preferred_area.items():
            if loc_kw in l:
                loc_score = pts
                break
        if loc_score == 0:
            return 0, ""
    else:
        loc_score = 0
        for loc_kw, pts in allowed_locations.items():
            if loc_kw in l:
                loc_score = pts
                break
        if loc_score == 0:
            return 0, ""
        # If the matching location keyword was 'remote', verify it isn't a remote we want to exclude
        if "remote" in l:
            if any(x in l for x in _LOC.get("exclude_remote_regions", [])):
                return 0, ""
    score += loc_score

    # Description-keyword hard excludes
    if any(kw in d for kw in EXCLUDE_DESC_KEYWORDS):
        return 0, ""

    # Tech stack / industry keyword signals
    matched_tech = [kw for kw in TECH_KEYWORDS if kw in d]
    score += min(len(matched_tech) * 3, 25)

    # strict_rules apply only to strict_filter_titles
    if is_strict_title:
        required = _STRICT.get("require_keywords_in_desc", [])
        if required and not all(kw in d for kw in required):
            return 0, ""
        max_yoe_cap = _STRICT.get("max_years_experience", 0)
        if max_yoe_cap > 0:
            yoe_matches = re.findall(r'(\d+)\+?\s*(?:years|yrs)', d)
            if yoe_matches:
                max_yoe = max(int(y) for y in yoe_matches)
                if max_yoe > max_yoe_cap:
                    return 0, ""

    return min(score, 100), ", ".join(matched_tech[:8])

# ──────────────────────────────────────────────────────────
# SERPAPI (GOOGLE SEARCH)
# Free tier: 100 searches/month — https://serpapi.com
# ──────────────────────────────────────────────────────────

SERPAPI_ENDPOINT = "https://serpapi.com/search"

def serpapi_search(query: str, num: int = 100) -> list[str]:
    """
    Call SerpAPI Google Search and return a list of result URLs.
    Uses 1 of your 100 free monthly searches per call.
    """
    if not SERPAPI_KEY:
        log.error(
            "\n" + "="*55 +
            "\n  SERPAPI_KEY not set!\n"
            "  1. Sign up free at https://serpapi.com (no credit card)\n"
            "  2. Copy your API key from the dashboard\n"
            "  3. export SERPAPI_KEY='your_key_here'\n"
            "  Or run with --no-search to use saved slugs.\n" +
            "="*55
        )
        return []

    params = {
        "q":       query,
        "num":     min(num, 100),
        "engine":  "google",
        "api_key": SERPAPI_KEY,
        "gl":      "us",
        "hl":      "en",
    }

    r = get(SERPAPI_ENDPOINT, params=params)
    if not r:
        return []

    try:
        data    = r.json()
        results = data.get("organic_results", [])
        urls    = [item["link"] for item in results if "link" in item]
        log.debug(f"  SerpAPI returned {len(urls)} results for: {query[:70]}")
        return urls
    except Exception as e:
        log.error(f"SerpAPI response parse error: {e}")
        return []


def extract_slug(url: str, pattern: str) -> Optional[str]:
    m = re.search(pattern, url)
    if m:
        slug = m.group(1).split("/")[0].split("?")[0].strip()
        return slug if len(slug) > 1 else None
    return None


# Workday subpaths that are not real career sites (auth gateway, static assets, etc.)
_WORKDAY_NON_SITES = {"wday", "static", "assets"}

def _extract_workday_slug(url: str) -> Optional[str]:
    """Extract 'tenant|wdN|site' from a Workday URL. Returns None for invalid/internal paths."""
    m = re.search(ATS_CONFIGS["workday"]["url_pattern"], url)
    if not m:
        return None
    tenant, wdn, site = m.group(1), m.group(2), m.group(3)
    if site.lower() in _WORKDAY_NON_SITES or "." in site or len(site) < 2:
        return None
    return f"{tenant}|{wdn}|{site}"


def discover_slugs_via_serpapi(ats_name: str, config: dict) -> set[str]:
    """
    For each (query_template x location) combo, call SerpAPI and extract slugs.
    API usage: 4 templates x 3 locations = 9 searches per ATS = 36 total
    Free tier = 100/month → ~3 full discovery runs per month.
    Use --no-search for weekly refreshes (zero API calls).
    """
    slugs: set[str] = set()
    site    = config["search_site"]
    pattern = config["url_pattern"]
    total   = len(QUERY_TEMPLATES) * len(SEARCH_LOCATIONS)
    i = 0

    for template in QUERY_TEMPLATES:
        for location in SEARCH_LOCATIONS:
            i += 1
            query = template.format(site=site, location=location)
            log.info(f"  [{i}/{total}] Google (SerpAPI): {query}")
            urls = serpapi_search(query)
            if ats_name == "workday":
                new = {s for u in urls for s in [_extract_workday_slug(u)] if s}
            else:
                new = {s for u in urls for s in [extract_slug(u, pattern)] if s}
            # Ashby API is case-insensitive — collapse "Airwallex" and "airwallex"
            # to one canonical lowercase slug to avoid scraping the same company twice.
            if ats_name == "ashby":
                new = {s.lower() for s in new}
            slugs.update(new)
            log.info(f"    → {len(new)} new slugs (total: {len(slugs)})")
            time.sleep(SEARCH_DELAY)

    return slugs

# ──────────────────────────────────────────────────────────
# DATE FILTERING HELPER
# ──────────────────────────────────────────────────────────

def is_within_max_age(date_str: str) -> bool:
    """Return True if the date string is within MAX_POSTING_AGE_DAYS, or if unparseable."""
    if not date_str:
        return True  # no date info — keep the job
    from datetime import timedelta
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%d"):
        try:
            posted = datetime.strptime(date_str.strip(), fmt).replace(tzinfo=None)
            cutoff = datetime.now() - timedelta(days=MAX_POSTING_AGE_DAYS)
            return posted >= cutoff
        except ValueError:
            continue
    return True  # unparseable — keep the job

# ──────────────────────────────────────────────────────────
# ATS API SCRAPERS
# ──────────────────────────────────────────────────────────

def scrape_ashby(slug: str) -> list[JobListing]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    r   = get(url)
    if not r:
        return []
    jobs = []
    try:
        data    = r.json()
        company = (data.get("organization") or {}).get("name") or slug.replace("-", " ").title()
        for job in data.get("jobs", []):
            title    = job.get("title", "")
            location = job.get("location", "")
            job_url  = job.get("jobUrl", f"https://jobs.ashbyhq.com/{slug}/{job.get('id', '')}")
            desc     = BeautifulSoup(job.get("descriptionHtml", ""), "html.parser").get_text()
            score, tech = score_job(title, location, desc)
            if score >= MIN_SCORE:
                jobs.append(JobListing(
                    company=company, title=title, location=location,
                    url=job_url, ats="ashby",
                    description_snippet=desc[:280].strip(),
                    tech_found=tech, relevance_score=score,
                ))
    except Exception as e:
        log.warning(f"Ashby error [{slug}]: {e}")
    return jobs


def scrape_greenhouse(slug: str) -> list[JobListing]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    r   = get(url)
    if not r:
        return []
    jobs = []
    try:
        data    = r.json()
        company = slug.replace("-", " ").title()
        for job in data.get("jobs", []):
            if not is_within_max_age(job.get("updated_at", "")):
                continue
            title    = job.get("title", "")
            location = job.get("location", {}).get("name", "")
            job_url  = job.get("absolute_url", "")
            desc     = BeautifulSoup(job.get("content", ""), "html.parser").get_text()
            score, tech = score_job(title, location, desc)
            if score >= MIN_SCORE:
                jobs.append(JobListing(
                    company=company, title=title, location=location,
                    url=job_url, ats="greenhouse",
                    description_snippet=desc[:280].strip(),
                    tech_found=tech, relevance_score=score,
                ))
    except Exception as e:
        log.debug(f"Greenhouse error [{slug}]: {e}")
    return jobs


def scrape_lever(slug: str) -> list[JobListing]:
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    r   = get(url)
    if not r:
        return []
    jobs = []
    try:
        for job in r.json():
            # Lever uses millisecond timestamps for createdAt
            created_ms = job.get("createdAt")
            if created_ms:
                from datetime import timedelta
                created_dt = datetime.utcfromtimestamp(created_ms / 1000)
                if created_dt < datetime.now() - timedelta(days=MAX_POSTING_AGE_DAYS):
                    continue
            title    = job.get("text", "")
            cats     = job.get("categories", {})
            location = cats.get("location", "") or (cats.get("allLocations") or [""])[0]
            job_url  = job.get("hostedUrl", "")
            company  = job.get("company", slug.replace("-", " ").title())
            desc     = BeautifulSoup(
                job.get("descriptionPlain", "") or job.get("description", ""),
                "html.parser"
            ).get_text()
            score, tech = score_job(title, location, desc)
            if score >= MIN_SCORE:
                jobs.append(JobListing(
                    company=company, title=title, location=location,
                    url=job_url, ats="lever",
                    description_snippet=desc[:280].strip(),
                    tech_found=tech, relevance_score=score,
                ))
    except Exception as e:
        log.debug(f"Lever error [{slug}]: {e}")
    return jobs


def scrape_smartrecruiters(slug: str) -> list[JobListing]:
    """SmartRecruiters: list endpoint + per-job detail call for descriptions."""
    list_url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
    jobs = []
    offset = 0
    limit = 100

    while True:
        r = get(list_url, params={"limit": limit, "offset": offset})
        if not r:
            break
        try:
            data = r.json()
        except Exception:
            break

        postings = data.get("content", [])
        if not postings:
            break

        company_name = None
        for posting in postings:
            if not is_within_max_age(posting.get("releasedDate", "")):
                continue
            if not company_name:
                company_name = (posting.get("company") or {}).get("name") or slug.replace("-", " ").title()
            title = posting.get("name", "")
            loc_obj = posting.get("location") or {}
            location = loc_obj.get("fullLocation") or loc_obj.get("city", "")
            posting_id = posting.get("id", "")

            # Quick pre-filter by title before fetching detail
            pre_score, _ = score_job(title, location, "")
            if pre_score == 0:
                continue

            # Fetch detail for description and postingUrl
            detail_url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{posting_id}"
            dr = get(detail_url)
            desc = ""
            job_url = f"https://jobs.smartrecruiters.com/{slug}/{posting_id}"
            if dr:
                try:
                    detail = dr.json()
                    job_url = detail.get("postingUrl") or job_url
                    job_ad = detail.get("jobAd", {}).get("sections", {})
                    desc_parts = [
                        job_ad.get("jobDescription", {}).get("text", ""),
                        job_ad.get("qualifications", {}).get("text", ""),
                    ]
                    desc = BeautifulSoup(" ".join(desc_parts), "html.parser").get_text()
                except Exception:
                    pass
                jitter_sleep(0.5)

            score, tech = score_job(title, location, desc)
            if score >= MIN_SCORE:
                jobs.append(JobListing(
                    company=company_name, title=title, location=location,
                    url=job_url, ats="smartrecruiters",
                    description_snippet=desc[:280].strip(),
                    tech_found=tech, relevance_score=score,
                ))

        total_found = data.get("totalFound", 0)
        offset += limit
        if offset >= total_found:
            break
        jitter_sleep()

    return jobs


def scrape_workable(slug: str) -> list[JobListing]:
    """Workable: single call with ?details=true returns all jobs + descriptions."""
    url = f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true"
    r = get(url)
    if not r:
        return []
    jobs = []
    try:
        data = r.json()
        company = data.get("name") or slug.replace("-", " ").title()
        for job in data.get("jobs", []):
            if not is_within_max_age(job.get("published_on", "")):
                continue
            title = job.get("title", "")
            city = job.get("city", "")
            state = job.get("state", "")
            country = job.get("country", "")
            location = ", ".join(p for p in [city, state, country] if p)
            job_url = job.get("url") or job.get("shortlink", "")
            desc = BeautifulSoup(job.get("description", ""), "html.parser").get_text()
            score, tech = score_job(title, location, desc)
            if score >= MIN_SCORE:
                jobs.append(JobListing(
                    company=company, title=title, location=location,
                    url=job_url, ats="workable",
                    description_snippet=desc[:280].strip(),
                    tech_found=tech, relevance_score=score,
                ))
    except Exception as e:
        log.debug(f"Workable error [{slug}]: {e}")
    return jobs


def _rippling_page_items(slug: str, page: int) -> tuple[list[dict], int]:
    """Fetch one page of Rippling job listings via the SSR HTML. Returns (items, total)."""
    r = get(f"https://ats.rippling.com/{slug}/jobs", params={"page": page})
    if not r:
        return [], 0
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
    if not m:
        return [], 0
    try:
        data = json.loads(m.group(1))
        for q in (data.get("props", {}).get("pageProps", {})
                      .get("dehydratedState", {}).get("queries", [])):
            qk = q.get("queryKey") or []
            if len(qk) >= 3 and qk[0] == "board" and qk[2] == "job-posts":
                payload = q.get("state", {}).get("data") or {}
                return payload.get("items", []) or [], payload.get("totalItems", 0) or 0
    except Exception:
        pass
    return [], 0


def scrape_rippling(slug: str) -> list[JobListing]:
    """Rippling ATS: server-rendered Next.js board paginated 20/page. Listing has no
    description or date — fetch each matching job's detail page for createdOn and body."""
    jobs = []
    company = slug.replace("-", " ").title()
    try:
        page = 0
        seen = 0
        while True:
            items, total = _rippling_page_items(slug, page)
            if not items:
                break
            for item in items:
                seen += 1
                title = item.get("name", "")
                locs = item.get("locations") or []
                location = "; ".join(
                    ", ".join(filter(None, [l.get("city"), l.get("state"), l.get("country")]))
                    for l in locs
                )
                job_url = item.get("url") or f"https://ats.rippling.com/{slug}/jobs/{item.get('id')}"

                pre_score, _ = score_job(title, location, "")
                if pre_score == 0:
                    continue

                dr = get(job_url)
                desc = ""
                if dr:
                    dm = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', dr.text, re.S)
                    if dm:
                        try:
                            jp = (json.loads(dm.group(1))
                                  .get("props", {}).get("pageProps", {})
                                  .get("apiData", {}).get("jobPost", {}))
                            if not is_within_max_age(jp.get("createdOn", "")):
                                jitter_sleep()
                                continue
                            company = jp.get("companyName") or company
                            desc_obj = jp.get("description") or {}
                            desc_html = (desc_obj.get("role") or "") + " " + (desc_obj.get("company") or "")
                            desc = BeautifulSoup(desc_html, "html.parser").get_text()
                        except Exception:
                            pass
                    jitter_sleep()

                score, tech = score_job(title, location, desc)
                if score >= MIN_SCORE:
                    jobs.append(JobListing(
                        company=company, title=title, location=location,
                        url=job_url, ats="rippling",
                        description_snippet=desc[:280].strip(),
                        tech_found=tech, relevance_score=score,
                    ))

            page += 1
            if total and seen >= total:
                break
            if page > 50:  # safety cap
                break
            jitter_sleep()
    except Exception as e:
        log.debug(f"Rippling error [{slug}]: {e}")
    return jobs


def _workday_post(url: str, body: dict) -> Optional[dict]:
    """POST with retries — Workday's cxs API is POST-only for listings."""
    headers = {**HEADERS, "Accept": "application/json", "Content-Type": "application/json"}
    sess = _session()
    for attempt in range(3):
        try:
            r = sess.post(url, headers=headers, json=body, timeout=TIMEOUT)
            if r.status_code == 200:
                try:
                    return r.json()
                except Exception:
                    return None
            if r.status_code == 429:
                wait = 10 * (attempt + 1)
                log.warning(f"Workday rate limited — waiting {wait}s...")
                time.sleep(wait)
                continue
            if r.status_code in (403, 404, 405):
                return None
            log.debug(f"Workday HTTP {r.status_code}: {url}")
            time.sleep(2)
        except requests.RequestException as e:
            log.debug(f"Workday request error ({attempt+1}/3): {e}")
            time.sleep(3)
    return None


def _workday_is_recent(posted_on: str) -> bool:
    """Workday returns relative strings: 'Posted Today', 'Posted Yesterday',
    'Posted N Days Ago', 'Posted 30+ Days Ago', 'Posted N Months Ago'."""
    if not posted_on:
        return True
    s = posted_on.lower()
    if "today" in s or "yesterday" in s:
        return True
    if "month" in s or "year" in s:
        return False
    m = re.search(r"(\d+)\+?\s*days?\s*ago", s)
    if m:
        days = int(m.group(1))
        # "30+ days ago" means ≥30 — only keep if our threshold is ≥30
        if "+" in s and days >= MAX_POSTING_AGE_DAYS:
            return False
        return days <= MAX_POSTING_AGE_DAYS
    return True


def scrape_workday(slug: str) -> list[JobListing]:
    """Workday: POST /wday/cxs/{tenant}/{site}/jobs for listings (paginated),
    GET /wday/cxs/{tenant}/{site}{externalPath} for descriptions.
    slug format: 'tenant|wdN|site' (e.g. 'remitly|wd5|Remitly_Careers')."""
    try:
        tenant, wdn, site = slug.split("|")
    except ValueError:
        log.warning(f"Workday: malformed slug '{slug}' — expected 'tenant|wdN|site'")
        return []

    base = f"https://{tenant}.{wdn}.myworkdayjobs.com"
    list_url = f"{base}/wday/cxs/{tenant}/{site}/jobs"
    jobs: list[JobListing] = []
    company_name = tenant.replace("-", " ").title()
    offset = 0
    limit = 20
    total = 0  # only populated by the first response; subsequent pages report total=0

    while True:
        data = _workday_post(list_url, {
            "limit": limit, "offset": offset, "searchText": "", "appliedFacets": {},
        })
        if not data:
            break

        postings = data.get("jobPostings", [])
        if not postings:
            break
        if offset == 0:
            total = data.get("total", 0)

        for posting in postings:
            if not _workday_is_recent(posting.get("postedOn", "")):
                continue
            title = posting.get("title", "")
            location = posting.get("locationsText", "")
            ext_path = posting.get("externalPath", "")
            if not ext_path:
                continue

            pre_score, _ = score_job(title, location, "")
            if pre_score == 0:
                continue

            detail_url = f"{base}/wday/cxs/{tenant}/{site}{ext_path}"
            dr = get(detail_url, headers={"Accept": "application/json"})
            desc = ""
            job_url = f"{base}/en-US/{site}{ext_path}"
            if dr:
                try:
                    info = (dr.json() or {}).get("jobPostingInfo", {})
                    desc = BeautifulSoup(info.get("jobDescription", ""), "html.parser").get_text()
                    job_url = info.get("externalUrl") or job_url
                except Exception:
                    pass
                jitter_sleep()

            score, tech = score_job(title, location, desc)
            if score >= MIN_SCORE:
                jobs.append(JobListing(
                    company=company_name, title=title, location=location,
                    url=job_url, ats="workday",
                    description_snippet=desc[:280].strip(),
                    tech_found=tech, relevance_score=score,
                ))

        offset += limit
        if offset >= total or offset >= 1000:  # safety cap
            break
        jitter_sleep()

    return jobs


SCRAPER_FN = {
    "ashby":           scrape_ashby,
    "greenhouse":      scrape_greenhouse,
    "lever":           scrape_lever,
    "smartrecruiters": scrape_smartrecruiters,
    "workable":        scrape_workable,
    "rippling":        scrape_rippling,
    "workday":         scrape_workday,
}

# ──────────────────────────────────────────────────────────
# SLUG PERSISTENCE
# ──────────────────────────────────────────────────────────

def load_applied_urls() -> set[str]:
    """Load URLs from the applied-jobs tracker CSV to exclude them from results.
    Returns lowercased URLs so the dedup compare in run() is case-insensitive —
    Ashby (and others) sometimes emits the same job with different slug case."""
    urls: set[str] = set()
    if not APPLIED_CSV.exists():
        return urls
    try:
        with open(APPLIED_CSV, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                link = (row.get("Job Link") or "").strip()
                if link:
                    urls.add(link.lower())
        log.info(f"Loaded {len(urls)} already-applied URLs from {APPLIED_CSV}")
    except Exception as e:
        log.warning(f"Could not read {APPLIED_CSV}: {e}")
    return urls


def load_slugs() -> dict[str, set[str]]:
    if SLUGS_FILE.exists():
        raw = json.loads(SLUGS_FILE.read_text())
        result = {k: set(v) for k, v in raw.items()}
        # Ashby's API is case-insensitive; collapse any historical case-dupes
        # (e.g. "Airwallex" + "airwallex" → "airwallex"). Other ATSs' slugs may
        # be case-sensitive (SmartRecruiters: LinkedIn3; Workday: Remitly_Careers).
        if "ashby" in result:
            result["ashby"] = {s.lower() for s in result["ashby"]}
        return result
    return {ats: set() for ats in ATS_CONFIGS}


def save_slugs(slugs: dict[str, set[str]]):
    SLUGS_FILE.write_text(
        json.dumps({k: sorted(v) for k, v in slugs.items()}, indent=2)
    )
    total = sum(len(v) for v in slugs.values())
    log.info(f"Saved {total} slugs → {SLUGS_FILE}")

# ──────────────────────────────────────────────────────────
# EXPORT
# ──────────────────────────────────────────────────────────

def export_csv(jobs: list[JobListing]):
    with open(RESULTS_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=asdict(jobs[0]).keys())
        writer.writeheader()
        writer.writerows(asdict(j) for j in jobs)
    log.info(f"CSV → {RESULTS_CSV}")


def export_html(jobs: list[JobListing]):
    ats_badge = {"ashby": "#6c5ce7", "greenhouse": "#00b894", "lever": "#0984e3",
                  "smartrecruiters": "#e84393", "workable": "#fdcb6e",
                  "rippling": "#00d2c4", "workday": "#f97316"}

    rows = ""
    for j in jobs:
        score_color = (
            "#27ae60" if j.relevance_score >= 70
            else "#e67e22" if j.relevance_score >= 50
            else "#95a5a6"
        )
        badge_color = ats_badge.get(j.ats, "#888")
        tech_pills  = "".join(
            f'<span style="background:#edf2ff;color:#4a5568;padding:1px 6px;'
            f'border-radius:9px;font-size:11px;margin:1px;display:inline-block">{t.strip()}</span>'
            for t in j.tech_found.split(",") if t.strip()
        )
        rows += f"""
        <tr>
          <td style="font-weight:600">{j.company}</td>
          <td><a href="{j.url}" target="_blank" style="color:#4361ee">{j.title}</a></td>
          <td style="color:#555">{j.location}</td>
          <td style="text-align:center">
            <span style="background:{score_color};color:#fff;padding:3px 9px;
                         border-radius:12px;font-size:12px;font-weight:600">
              {j.relevance_score}
            </span>
          </td>
          <td>{tech_pills}</td>
          <td style="font-size:11px;color:#777;max-width:260px">{j.description_snippet[:200]}…</td>
          <td style="text-align:center">
            <span style="background:{badge_color};color:#fff;padding:2px 7px;
                         border-radius:8px;font-size:11px">{j.ats}</span>
          </td>
        </tr>"""

    # Derive location stats / filter options from config so the report stays generic.
    # preferred_kw = top-priority area (e.g. ["seattle","bellevue",...] or ["london"])
    # allowed_only_kw = secondary cities that aren't already preferred and aren't "remote"
    preferred_kw    = list(_LOC.get("preferred_area", {}).keys())
    allowed_kw      = list(_LOC.get("allowed", {}).keys())
    preferred_set   = set(preferred_kw)
    allowed_only_kw = [k for k in allowed_kw if k not in preferred_set and k.strip() != "remote"]

    def _matches_any(loc_str: str, kws: list[str]) -> bool:
        return any(k in loc_str for k in kws)

    def _group_label(kws: list[str]) -> str:
        if not kws:
            return ""
        head = kws[0].strip().lstrip().title() or kws[0]
        return f"{head} Area" if len(kws) > 1 else head

    n_remote    = sum(1 for j in jobs if "remote" in j.location.lower())
    n_preferred = sum(1 for j in jobs if _matches_any(j.location.lower(), preferred_kw))
    n_allowed   = sum(1 for j in jobs if _matches_any(j.location.lower(), allowed_only_kw))
    n_high      = sum(1 for j in jobs if j.relevance_score >= 70)

    preferred_label = _group_label(preferred_kw) or "Preferred"
    allowed_label   = _group_label(allowed_only_kw) or "Allowed"

    # Optional stats blocks — only emit if the corresponding group is non-empty in config
    stat_preferred = (f'  <div class="stat"><div class="n">{n_preferred}</div>'
                      f'<div class="l">{preferred_label}</div></div>') if preferred_kw else ""
    stat_allowed   = (f'  <div class="stat"><div class="n">{n_allowed}</div>'
                      f'<div class="l">{allowed_label}</div></div>') if allowed_only_kw else ""

    # Build the location filter dropdown's options from config
    loc_filter_options = ""
    seen_opts: set[str] = set()
    for value, label in [
        (preferred_kw[0]    if preferred_kw    else None, preferred_label),
        (allowed_only_kw[0] if allowed_only_kw else None, allowed_label),
        ("remote", "Remote"),
    ]:
        if not value: continue
        v = value.strip().lower()
        if v in seen_opts: continue
        seen_opts.add(v)
        loc_filter_options += f'    <option value="{v}">{label}</option>\n'

    html = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Job Results — {datetime.now().strftime('%b %d, %Y')}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
          background: #f0f2f5; color: #1a1a2e; }}
  .hero {{ background: linear-gradient(135deg, #4361ee, #3a0ca3);
           color: white; padding: 36px 48px; }}
  .hero h1 {{ font-size: 26px; font-weight: 700; margin-bottom: 6px; }}
  .hero p  {{ opacity: .75; font-size: 14px; }}
  .stats  {{ display: flex; gap: 0; background: white;
             border-bottom: 2px solid #e8eaf0; }}
  .stat   {{ flex: 1; padding: 18px 24px; text-align: center;
             border-right: 1px solid #eee; }}
  .stat:last-child {{ border-right: none; }}
  .stat .n {{ font-size: 30px; font-weight: 800; color: #4361ee; }}
  .stat .l {{ font-size: 12px; color: #888; margin-top: 2px; }}
  .wrap   {{ padding: 24px 32px; }}
  table   {{ width: 100%; border-collapse: collapse; background: white;
             border-radius: 10px; overflow: hidden;
             box-shadow: 0 2px 12px rgba(0,0,0,.07); }}
  th      {{ background: #4361ee; color: white; padding: 13px 16px;
             text-align: left; font-size: 12px; font-weight: 600;
             letter-spacing: .4px; text-transform: uppercase; }}
  td      {{ padding: 13px 16px; border-bottom: 1px solid #f0f2f5;
             vertical-align: top; font-size: 13px; }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover td {{ background: #f7f8ff; }}
  a:hover {{ text-decoration: underline; }}
  .filter-bar {{ padding: 16px 32px 0; display: flex; gap: 10px; flex-wrap: wrap; }}
  .filter-bar input, .filter-bar select {{
    padding: 9px 14px; border: 1px solid #dde; border-radius: 8px;
    font-size: 13px; outline: none; background: white; }}
  .filter-bar input {{ width: 260px; }}
  .filter-bar input:focus, .filter-bar select:focus {{ border-color: #4361ee; }}
  .count {{ padding: 10px 32px 0; font-size: 13px; color: #888; }}
</style>
</head><body>

<div class="hero">
  <h1>🔍 Job Results</h1>
  <p>Scraped via SerpAPI + direct ATS APIs &nbsp;|&nbsp; {datetime.now().strftime('%B %d, %Y')}</p>
</div>

<div class="stats">
  <div class="stat"><div class="n">{len(jobs)}</div><div class="l">Total Matches</div></div>
  <div class="stat"><div class="n">{n_high}</div><div class="l">High Relevance (70+)</div></div>
  <div class="stat"><div class="n">{len(set(j.company for j in jobs))}</div><div class="l">Companies</div></div>
  <div class="stat"><div class="n">{n_remote}</div><div class="l">Remote</div></div>
{stat_preferred}
{stat_allowed}
</div>

<div class="filter-bar">
  <input type="text" id="search" placeholder="Filter by company, title, tech…" oninput="filterTable()">
  <select id="locFilter" onchange="filterTable()">
    <option value="">All locations</option>
{loc_filter_options}  </select>
  <select id="atsFilter" onchange="filterTable()">
    <option value="">All ATS</option>
    <option value="ashby">Ashby</option>
    <option value="greenhouse">Greenhouse</option>
    <option value="lever">Lever</option>
    <option value="smartrecruiters">SmartRecruiters</option>
    <option value="workable">Workable</option>
    <option value="rippling">Rippling</option>
    <option value="workday">Workday</option>
  </select>
</div>
<div class="count" id="countLabel">{len(jobs)} jobs shown</div>

<div class="wrap">
<table id="jobTable">
  <thead><tr>
    <th>Company</th><th>Role</th><th>Location</th>
    <th>Score</th><th>Tech Stack</th><th>Snippet</th><th>ATS</th>
  </tr></thead>
  <tbody id="tableBody">{rows}</tbody>
</table>
</div>

<script>
function filterTable() {{
  const q   = document.getElementById('search').value.toLowerCase();
  const loc = document.getElementById('locFilter').value.toLowerCase();
  const ats = document.getElementById('atsFilter').value.toLowerCase();
  let shown = 0;
  document.querySelectorAll('#tableBody tr').forEach(row => {{
    const text = row.textContent.toLowerCase();
    const show = (!q || text.includes(q))
              && (!loc || text.includes(loc))
              && (!ats || text.includes(ats));
    row.style.display = show ? '' : 'none';
    if (show) shown++;
  }});
  document.getElementById('countLabel').textContent = shown + ' jobs shown';
}}
</script>
</body></html>"""

    RESULTS_HTML.write_text(html, encoding="utf-8")
    log.info(f"HTML → {RESULTS_HTML}")

# ──────────────────────────────────────────────────────────
# MAIN PIPELINE
# ──────────────────────────────────────────────────────────

def run(ats_filter=None, no_search=False, export_fmt="both"):
    slugs = load_slugs()

    # ── Phase 1: SerpAPI discovery ───────────────────────
    if not no_search:
        if not SERPAPI_KEY:
            log.error(
                "\n" + "="*55 +
                "\n  SERPAPI_KEY is not set. Please:\n"
                "  1. Sign up free at https://serpapi.com (no credit card)\n"
                "  2. Copy your API key from the dashboard\n"
                "  3. export SERPAPI_KEY='your_key_here'\n"
                "  Or run with --no-search to use saved slugs.\n" +
                "="*55
            )
            return []

        log.info("=" * 55)
        log.info("Phase 1 — Google slug discovery via SerpAPI")
        log.info(f"Locations: {SEARCH_LOCATIONS}")
        log.info(f"API usage: ~{len(QUERY_TEMPLATES) * len(SEARCH_LOCATIONS) * len(ATS_CONFIGS)} searches")
        log.info("=" * 55)

        for ats_name, config in ATS_CONFIGS.items():
            if ats_filter and ats_filter != ats_name:
                continue
            log.info(f"\n[{ats_name.upper()}]")
            new_slugs = discover_slugs_via_serpapi(ats_name, config)
            before = len(slugs.get(ats_name, set()))
            slugs.setdefault(ats_name, set()).update(new_slugs)
            after  = len(slugs[ats_name])
            log.info(f"[{ats_name}] {before} → {after} slugs (+{after-before} new)")

        save_slugs(slugs)
    else:
        total = sum(len(v) for v in slugs.values())
        log.info(f"Skipping search. Using {total} saved slugs from {SLUGS_FILE}")

    # ── Phase 2: ATS API scraping ────────────────────────
    log.info("\n" + "=" * 55)
    log.info("Phase 2 — Scraping ATS APIs")
    log.info("=" * 55)

    all_jobs: list[JobListing] = []
    for ats_name, slug_set in slugs.items():
        if ats_filter and ats_filter != ats_name:
            continue
        fn = SCRAPER_FN.get(ats_name)
        if not fn or not slug_set:
            continue

        slug_list = sorted(slug_set)
        workers = ATS_CONCURRENCY.get(ats_name, 1)
        log.info(f"\n[{ats_name.upper()}] {len(slug_list)} companies (concurrency={workers})")

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"{ats_name}-") as pool:
            futures = {pool.submit(fn, slug): (i, slug)
                       for i, slug in enumerate(slug_list, 1)}
            for future in as_completed(futures):
                i, slug = futures[future]
                try:
                    jobs = future.result()
                    if jobs:
                        log.info(f"  [{i:3d}/{len(slug_list)}] {slug} ✓ {len(jobs)} match(es)")
                        all_jobs.extend(jobs)
                    else:
                        log.debug(f"  [{i:3d}/{len(slug_list)}] {slug}")
                except Exception as e:
                    log.error(f"  [{i:3d}/{len(slug_list)}] {slug} ✗ {e}")

    # Deduplicate by URL and exclude already-applied jobs
    applied_urls = load_applied_urls()
    seen:   set[str]        = set()
    unique: list[JobListing] = []
    skipped_applied = 0
    exclude_co = [c.lower() for c in EXCLUDE_COMPANIES]
    skipped_file = Path("skipped_companies.json")
    if skipped_file.exists():
        import json as _json
        skipped = _json.loads(skipped_file.read_text()).get("skipped_companies", [])
        exclude_co.extend(c.lower() for c in skipped)
        log.info(f"Loaded {len(skipped)} skipped companies from {skipped_file}")
    for j in all_jobs:
        url_key = j.url.lower()   # case-insensitive dedup — see load_applied_urls() docstring
        if url_key in applied_urls:
            skipped_applied += 1
            continue
        if any(ex in j.company.lower() for ex in exclude_co):
            continue
        if url_key not in seen:
            seen.add(url_key)
            unique.append(j)
    if skipped_applied:
        log.info(f"Excluded {skipped_applied} already-applied job(s)")
    unique.sort(key=lambda j: j.relevance_score, reverse=True)

    # ── Phase 3: Export ──────────────────────────────────
    n_co = len(set(j.company for j in unique))
    log.info(f"\n{'='*55}")
    log.info(f"Results: {len(unique)} unique jobs from {n_co} companies")
    log.info("=" * 55)

    if unique:
        if export_fmt in ("csv",  "both"): export_csv(unique)
        if export_fmt in ("html", "both"): export_html(unique)
        # Regenerate apply_assistant.html (needs jobs_results.csv, so gate on csv export)
        if export_fmt in ("csv", "both"):
            try:
                import apply_assistant
                jobs_dict = apply_assistant.load_jobs()
                apply_assistant.OUTPUT_HTML.write_text(
                    apply_assistant.generate_html(jobs_dict), encoding="utf-8"
                )
                log.info(f"Apply assistant → {apply_assistant.OUTPUT_HTML}")
            except Exception as e:
                log.warning(f"apply_assistant regeneration failed: {e}")

    # Console preview
    print(f"\n{'─'*62}")
    print(f"  TOP MATCHES  ({min(15, len(unique))} of {len(unique)} total)")
    print(f"{'─'*62}")
    for j in unique[:15]:
        print(f"\n  [{j.relevance_score:3d}] {j.title}")
        print(f"        Company:  {j.company}")
        print(f"        Location: {j.location}")
        print(f"        Tech:     {j.tech_found or '—'}")
        print(f"        URL:      {j.url}")
    if len(unique) > 15:
        print(f"\n  … and {len(unique)-15} more.")
    print(f"\n  Open {RESULTS_HTML} in your browser for the full report.\n")

    return unique

# ──────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="ATS Job Scraper — Bing Search API + Ashby/Greenhouse/Lever"
    )
    parser.add_argument("--ats", choices=["ashby", "greenhouse", "lever", "smartrecruiters", "workable", "rippling", "workday"],
                        help="Only scrape one ATS platform")
    parser.add_argument("--no-search", action="store_true",
                        help="Skip Bing search, reuse slugs from discovered_slugs.json")
    parser.add_argument("--export", choices=["csv", "html", "both"], default="both",
                        help="Export format (default: both)")
    parser.add_argument("--add-slug", nargs="+", metavar="SLUG",
                        help="Manually add slugs to an ATS. First arg = ATS name.\n"
                             "e.g.: --add-slug ashby openai anthropic scale-ai")
    parser.add_argument("--max-age-days", type=int, default=MAX_POSTING_AGE_DAYS,
                        help=f"Max posting age in days (default: {MAX_POSTING_AGE_DAYS}). "
                             "Use 7 for last week, 30 for last month.")
    args = parser.parse_args()

    MAX_POSTING_AGE_DAYS = args.max_age_days

    if args.add_slug:
        slugs    = load_slugs()
        ats_name = args.add_slug[0]
        new      = args.add_slug[1:]
        if ats_name not in ATS_CONFIGS:
            print(f"Unknown ATS '{ats_name}'. Choose from: {list(ATS_CONFIGS.keys())}")
        else:
            if ats_name == "ashby":
                new = [s.lower() for s in new]   # Ashby API is case-insensitive
            slugs.setdefault(ats_name, set()).update(new)
            save_slugs(slugs)
            print(f"Added to {ats_name}: {new}")
        exit(0)

    run(ats_filter=args.ats, no_search=args.no_search, export_fmt=args.export)
