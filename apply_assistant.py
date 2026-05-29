"""
Job Application Assistant
=========================
Reads jobs_results.csv and generates an interactive HTML page with:
  - Checkboxes to select jobs
  - "Open Selected" to batch-open tabs
  - Personal info panel with one-click copy for fast form filling
  - Application status tracking (applied / skipped / bookmarked) via localStorage
  - Resume file path reminder

Usage:
    python3 apply_assistant.py                    # generate from jobs_results.csv
    python3 apply_assistant.py --top 30           # only top 30 jobs
    python3 apply_assistant.py --open 10          # generate + immediately open top 10 in browser
"""

import csv
import json
import argparse
import webbrowser
import subprocess
import sys
import os
from pathlib import Path
from datetime import datetime

RESULTS_CSV = Path("jobs_results.csv")
OUTPUT_HTML = Path("apply_assistant.html")

# Absolute paths so the server keeps working regardless of the user's CWD
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
MASTER_FILE = os.path.join(PROJECT_DIR, "JobApplicationTracker.csv")
CONFIG_FILE = os.path.join(PROJECT_DIR, "config.json")
CSV_HEADER = ["Company", "Job Title", "Application Method", "Job Link",
              "Application Date", "Status", "匹配度", "备注"]
URL_INDEX = 3  # contract with JS: rows are [company, title, method, URL, date, status, '', '']


def _load_config() -> dict:
    """Load config.json. Strips '//' comment keys so config.example.json can self-document."""
    if not os.path.exists(CONFIG_FILE):
        sys.exit(
            f"Missing {os.path.basename(CONFIG_FILE)}. "
            f"Copy config.example.json → config.json and fill in your values."
        )
    with open(CONFIG_FILE, encoding="utf-8") as f:
        raw = json.load(f)
    def _strip(o):
        if isinstance(o, dict):
            return {k: _strip(v) for k, v in o.items() if not k.startswith("//")}
        if isinstance(o, list):
            return [_strip(v) for v in o]
        return o
    return _strip(raw)


PROFILE = _load_config()["profile"]


def load_jobs(top_n: int = 0) -> list[dict]:
    if not RESULTS_CSV.exists():
        print(f"Error: {RESULTS_CSV} not found. Run ats_scraper.py first.")
        sys.exit(1)
    with open(RESULTS_CSV, encoding="utf-8") as f:
        jobs = list(csv.DictReader(f))
    if top_n > 0:
        jobs = jobs[:top_n]
    return jobs


def generate_html(jobs: list[dict]) -> str:
    # Build profile fields HTML
    profile_fields = ""
    for key, val in PROFILE.items():
        if not val:
            continue
        label = key.replace("_", " ").title()
        profile_fields += f"""
        <div class="pf-row">
          <span class="pf-label">{label}</span>
          <span class="pf-value" id="pf-{key}">{val}</span>
          <button class="copy-btn" onclick="copyField('pf-{key}')">Copy</button>
        </div>"""

    # Build job rows
    ats_colors = {"ashby": "#6c5ce7", "greenhouse": "#00b894", "lever": "#0984e3",
                   "smartrecruiters": "#e84393", "workable": "#fdcb6e",
                   "rippling": "#00d2c4", "workday": "#f97316"}
    rows = ""
    for i, j in enumerate(jobs):
        score = int(j.get("relevance_score", 0))
        score_color = "#27ae60" if score >= 70 else "#e67e22" if score >= 50 else "#95a5a6"
        badge_color = ats_colors.get(j.get("ats", ""), "#888")
        tech = j.get("tech_found", "")
        tech_pills = "".join(
            f'<span class="tech-pill">{t.strip()}</span>'
            for t in tech.split(",") if t.strip()
        )
        url = j.get("url", "")
        company = j.get("company", "")
        title = j.get("title", "")
        location = j.get("location", "")

        rows += f"""
        <tr data-idx="{i}" data-url="{url}" data-company="{company}" data-title="{title.replace('"', '&quot;')}">
          <td><input type="checkbox" class="job-cb" data-idx="{i}"></td>
          <td>
            <span class="status-dot" id="dot-{i}" title="Click to cycle status" onclick="cycleStatus({i})"></span>
          </td>
          <td style="font-weight:600">{company}</td>
          <td><a href="{url}" target="_blank" style="color:#4361ee">{title}</a></td>
          <td style="color:#555">{location}</td>
          <td style="text-align:center">
            <span style="background:{score_color};color:#fff;padding:3px 9px;
                         border-radius:12px;font-size:12px;font-weight:600">{score}</span>
          </td>
          <td>{tech_pills}</td>
          <td style="text-align:center">
            <span style="background:{badge_color};color:#fff;padding:2px 7px;
                         border-radius:8px;font-size:11px">{j.get("ats","")}</span>
          </td>
          <td style="text-align:center">
            <button class="open-btn" onclick="window.open('{url}','_blank')">Open</button>
          </td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Application Assistant — {datetime.now().strftime('%b %d, %Y')}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
          background: #f0f2f5; color: #1a1a2e; }}

  .hero {{ background: linear-gradient(135deg, #e74c3c, #8e44ad);
           color: white; padding: 30px 40px; }}
  .hero h1 {{ font-size: 24px; font-weight: 700; }}
  .hero p  {{ opacity: .8; font-size: 13px; margin-top: 4px; }}

  /* Profile panel */
  .panel {{ background: white; margin: 20px 32px; padding: 20px 24px;
            border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,.06); }}
  .panel h2 {{ font-size: 15px; margin-bottom: 12px; color: #555; }}
  .pf-row {{ display: flex; align-items: center; gap: 10px; padding: 5px 0;
             border-bottom: 1px solid #f5f5f5; }}
  .pf-label {{ width: 120px; font-size: 12px; color: #888; text-transform: uppercase;
               letter-spacing: .3px; }}
  .pf-value {{ flex: 1; font-size: 14px; font-weight: 500; }}
  .copy-btn {{ background: #4361ee; color: white; border: none; padding: 4px 12px;
               border-radius: 6px; font-size: 11px; cursor: pointer; }}
  .copy-btn:hover {{ background: #3451de; }}
  .copy-btn.copied {{ background: #27ae60; }}

  /* Action bar */
  .action-bar {{ display: flex; gap: 10px; align-items: center; flex-wrap: wrap;
                  padding: 16px 32px; }}
  .action-bar button {{ padding: 10px 20px; border: none; border-radius: 8px;
                         font-size: 13px; font-weight: 600; cursor: pointer; }}
  .btn-primary {{ background: #e74c3c; color: white; }}
  .btn-primary:hover {{ background: #c0392b; }}
  .btn-secondary {{ background: #ddd; color: #333; }}
  .btn-secondary:hover {{ background: #ccc; }}
  .action-bar span {{ font-size: 13px; color: #888; }}

  /* Filter bar */
  .filter-bar {{ padding: 0 32px; display: flex; gap: 10px; flex-wrap: wrap;
                  align-items: center; }}
  .filter-bar input, .filter-bar select {{
    padding: 8px 14px; border: 1px solid #dde; border-radius: 8px;
    font-size: 13px; outline: none; background: white; }}
  .filter-bar input {{ width: 240px; }}
  .filter-bar input:focus, .filter-bar select:focus {{ border-color: #4361ee; }}

  /* Stats */
  .stats-row {{ display: flex; gap: 16px; padding: 12px 32px; }}
  .stat-chip {{ background: white; padding: 8px 16px; border-radius: 20px;
                font-size: 13px; box-shadow: 0 1px 4px rgba(0,0,0,.06); }}
  .stat-chip b {{ color: #4361ee; }}

  /* Table */
  .wrap {{ padding: 16px 32px; }}
  table {{ width: 100%; border-collapse: collapse; background: white;
           border-radius: 10px; overflow: hidden;
           box-shadow: 0 2px 12px rgba(0,0,0,.07); }}
  th {{ background: #4361ee; color: white; padding: 12px 14px;
        text-align: left; font-size: 11px; font-weight: 600;
        letter-spacing: .4px; text-transform: uppercase; }}
  td {{ padding: 11px 14px; border-bottom: 1px solid #f0f2f5;
        vertical-align: middle; font-size: 13px; }}
  tr:hover td {{ background: #f7f8ff; }}
  a:hover {{ text-decoration: underline; }}

  .tech-pill {{ background:#edf2ff; color:#4a5568; padding:1px 6px;
                border-radius:9px; font-size:11px; margin:1px; display:inline-block; }}

  .open-btn {{ background: #4361ee; color: white; border: none; padding: 4px 12px;
               border-radius: 6px; font-size: 11px; cursor: pointer; }}
  .open-btn:hover {{ background: #3451de; }}

  /* Status dots */
  .status-dot {{ width: 14px; height: 14px; border-radius: 50%; display: inline-block;
                  cursor: pointer; border: 2px solid #ddd; background: white; }}
  .status-dot.applied    {{ background: #27ae60; border-color: #27ae60; }}
  .status-dot.skipped    {{ background: #e74c3c; border-color: #e74c3c; }}
  .status-dot.bookmarked {{ background: #f39c12; border-color: #f39c12; }}

  /* Legend */
  .legend {{ display: flex; gap: 16px; font-size: 12px; color: #888; align-items: center; }}
  .legend-dot {{ width: 12px; height: 12px; border-radius: 50%; display: inline-block; }}

  input[type="checkbox"] {{ width: 16px; height: 16px; cursor: pointer; }}

  .toast {{ position: fixed; bottom: 24px; right: 24px; background: #333; color: white;
            padding: 12px 20px; border-radius: 10px; font-size: 13px;
            opacity: 0; transition: opacity .3s; z-index: 999; }}
  .toast.show {{ opacity: 1; }}
</style>
</head><body>

<div class="hero">
  <h1>Application Assistant</h1>
  <p>{len(jobs)} jobs ready to apply &nbsp;|&nbsp; {datetime.now().strftime('%B %d, %Y')}
     &nbsp;|&nbsp; Select jobs, batch open, and track your progress</p>
</div>

<!-- Profile quick-copy panel -->
<div class="panel" id="profilePanel">
  <h2>Quick Copy — Personal Info (click Copy to clipboard, then paste into forms)</h2>
  {profile_fields}
  <div style="margin-top:10px; font-size:12px; color:#aaa;">
    Edit these values in <code>apply_assistant.py</code> PROFILE dict.
  </div>
</div>

<!-- Action bar -->
<div class="action-bar">
  <button class="btn-primary" onclick="openSelected()">Open Selected in Tabs</button>
  <button class="btn-secondary" onclick="selectAll()">Select All Visible</button>
  <button class="btn-secondary" onclick="deselectAll()">Deselect All</button>
  <button class="btn-secondary" onclick="selectTop(10)">Select Top 10</button>
  <button class="btn-secondary" onclick="selectUnapplied()">Select Unapplied</button>
  <span id="selectedCount">0 selected</span>
  <button class="btn-secondary" onclick="exportAppliedCSV()" style="margin-left:auto;background:#27ae60;color:#fff;">Export Applied CSV</button>
  <button class="btn-secondary" onclick="exportSkippedCompanies()" style="background:#e74c3c;color:#fff;">Export Skipped Companies</button>
</div>

<!-- Stats -->
<div class="stats-row">
  <div class="stat-chip"><b id="totalCount">{len(jobs)}</b> total</div>
  <div class="stat-chip"><b id="appliedCount">0</b> applied</div>
  <div class="stat-chip"><b id="skippedCount">0</b> skipped</div>
  <div class="stat-chip"><b id="bookmarkedCount">0</b> bookmarked</div>
  <div class="stat-chip"><b id="remainingCount">{len(jobs)}</b> remaining</div>
</div>

<!-- Filter bar -->
<div class="filter-bar">
  <input type="text" id="search" placeholder="Filter by company, title, tech..." oninput="filterTable()">
  <select id="statusFilter" onchange="filterTable()">
    <option value="">All statuses</option>
    <option value="none">Not yet actioned</option>
    <option value="applied">Applied</option>
    <option value="bookmarked">Bookmarked</option>
    <option value="skipped">Skipped</option>
  </select>
  <select id="locFilter" onchange="filterTable()">
    <option value="">All locations</option>
    <option value="seattle">Seattle</option>
    <option value="remote">Remote</option>
    <option value="san francisco">San Francisco</option>
  </select>
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
  <div class="legend" style="margin-left:auto;">
    <span><span class="legend-dot" style="background:#ddd;border:2px solid #ddd;"></span> New</span>
    <span><span class="legend-dot" style="background:#27ae60;"></span> Applied</span>
    <span><span class="legend-dot" style="background:#f39c12;"></span> Bookmarked</span>
    <span><span class="legend-dot" style="background:#e74c3c;"></span> Skipped</span>
  </div>
</div>

<!-- Job table -->
<div class="wrap">
<table id="jobTable">
  <thead><tr>
    <th><input type="checkbox" id="masterCb" onclick="toggleMaster(this)"></th>
    <th>Status</th>
    <th>Company</th><th>Role</th><th>Location</th>
    <th>Score</th><th>Tech Stack</th><th>ATS</th><th>Action</th>
  </tr></thead>
  <tbody id="tableBody">{rows}</tbody>
</table>
</div>

<div id="toast" class="toast"></div>

<script>
// ── Status tracking via localStorage ──────────────────
const STORAGE_KEY = 'job_apply_status';
const DATES_KEY = 'job_apply_dates';
const STATUSES = ['none', 'applied', 'bookmarked', 'skipped'];

function loadStatuses() {{
  try {{ return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {{}}; }}
  catch {{ return {{}}; }}
}}
function saveStatuses(s) {{ localStorage.setItem(STORAGE_KEY, JSON.stringify(s)); }}
function loadDates() {{
  try {{ return JSON.parse(localStorage.getItem(DATES_KEY)) || {{}}; }}
  catch {{ return {{}}; }}
}}
function saveDates(d) {{ localStorage.setItem(DATES_KEY, JSON.stringify(d)); }}
function formatDate(d) {{
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${{mm}}/${{dd}}/${{d.getFullYear()}}`;
}}

function cycleStatus(idx) {{
  const statuses = loadStatuses();
  const dates = loadDates();
  const row = document.querySelector(`tr[data-idx="${{idx}}"]`);
  const url = row.dataset.url;
  const current = statuses[url] || 'none';
  const next = STATUSES[(STATUSES.indexOf(current) + 1) % STATUSES.length];
  if (next === 'none') {{
    delete statuses[url];
    delete dates[url];
  }} else {{
    statuses[url] = next;
    if (next === 'applied') dates[url] = formatDate(new Date());
  }}
  saveStatuses(statuses);
  saveDates(dates);
  applyDotStatus(idx, next);
  updateStats();
}}

function applyDotStatus(idx, status) {{
  const dot = document.getElementById(`dot-${{idx}}`);
  dot.className = 'status-dot' + (status !== 'none' ? ' ' + status : '');
}}

function restoreStatuses() {{
  const statuses = loadStatuses();
  document.querySelectorAll('#tableBody tr').forEach(row => {{
    const url = row.dataset.url;
    const idx = row.dataset.idx;
    const status = statuses[url] || 'none';
    applyDotStatus(idx, status);
  }});
  updateStats();
}}

function updateStats() {{
  const statuses = loadStatuses();
  const urls = Array.from(document.querySelectorAll('#tableBody tr')).map(r => r.dataset.url);
  let applied = 0, skipped = 0, bookmarked = 0;
  urls.forEach(u => {{
    if (statuses[u] === 'applied') applied++;
    else if (statuses[u] === 'skipped') skipped++;
    else if (statuses[u] === 'bookmarked') bookmarked++;
  }});
  document.getElementById('appliedCount').textContent = applied;
  document.getElementById('skippedCount').textContent = skipped;
  document.getElementById('bookmarkedCount').textContent = bookmarked;
  document.getElementById('remainingCount').textContent = urls.length - applied - skipped - bookmarked;
}}

// ── Selection ─────────────────────────────────────────
function getVisibleRows() {{
  return Array.from(document.querySelectorAll('#tableBody tr'))
    .filter(r => r.style.display !== 'none');
}}

function updateSelectedCount() {{
  const n = document.querySelectorAll('.job-cb:checked').length;
  document.getElementById('selectedCount').textContent = n + ' selected';
}}

function selectAll() {{
  getVisibleRows().forEach(r => {{ r.querySelector('.job-cb').checked = true; }});
  updateSelectedCount();
}}
function deselectAll() {{
  document.querySelectorAll('.job-cb').forEach(cb => cb.checked = false);
  updateSelectedCount();
}}
function selectTop(n) {{
  deselectAll();
  getVisibleRows().slice(0, n).forEach(r => {{ r.querySelector('.job-cb').checked = true; }});
  updateSelectedCount();
}}
function selectUnapplied() {{
  deselectAll();
  const statuses = loadStatuses();
  getVisibleRows().forEach(r => {{
    if (!statuses[r.dataset.url]) r.querySelector('.job-cb').checked = true;
  }});
  updateSelectedCount();
}}
function toggleMaster(master) {{
  getVisibleRows().forEach(r => {{ r.querySelector('.job-cb').checked = master.checked; }});
  updateSelectedCount();
}}

document.addEventListener('change', e => {{
  if (e.target.classList.contains('job-cb')) updateSelectedCount();
}});

// ── Batch open ────────────────────────────────────────
// Programmatic anchor-click bypasses Chrome/Safari's "multiple window.open"
// popup blocker: each .click() on a target=_blank link is treated as a regular
// user-initiated navigation, not a popup.
function openInNewTab(url) {{
  const a = document.createElement('a');
  a.href = url;
  a.target = '_blank';
  a.rel = 'noopener noreferrer';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}}

function openSelected() {{
  const checked = document.querySelectorAll('.job-cb:checked');
  if (checked.length === 0) {{ showToast('No jobs selected!'); return; }}
  if (checked.length > 15 && !confirm(`Open ${{checked.length}} tabs? This may be a lot.`)) return;

  const statuses = loadStatuses();
  let opened = 0;
  // Synchronous loop — stay inside the user-gesture stack for popup permission
  checked.forEach(cb => {{
    const row = cb.closest('tr');
    const url = row.dataset.url;
    openInNewTab(url);
    opened++;
    if (!statuses[url]) {{
      statuses[url] = 'bookmarked';
      applyDotStatus(row.dataset.idx, 'bookmarked');
    }}
  }});
  saveStatuses(statuses);
  updateStats();
  showToast(`Opening ${{opened}} job pages…`);
}}

// ── Filter ────────────────────────────────────────────
function filterTable() {{
  const q   = document.getElementById('search').value.toLowerCase();
  const loc = document.getElementById('locFilter').value.toLowerCase();
  const ats = document.getElementById('atsFilter').value.toLowerCase();
  const st  = document.getElementById('statusFilter').value;
  const statuses = loadStatuses();

  document.querySelectorAll('#tableBody tr').forEach(row => {{
    const text = row.textContent.toLowerCase();
    const url  = row.dataset.url;
    const status = statuses[url] || 'none';

    const matchText = !q || text.includes(q);
    const matchLoc  = !loc || text.includes(loc);
    const matchAts  = !ats || text.includes(ats);
    const matchSt   = !st || status === st;

    row.style.display = (matchText && matchLoc && matchAts && matchSt) ? '' : 'none';
  }});
}}

// ── Export skipped companies ─────────────────────────
function exportSkippedCompanies() {{
  const statuses = loadStatuses();
  const companies = new Set();
  document.querySelectorAll('#tableBody tr').forEach(row => {{
    const url = row.dataset.url;
    if (statuses[url] === 'skipped') {{
      companies.add(row.dataset.company.toLowerCase());
    }}
  }});
  if (companies.size === 0) {{
    showToast('No skipped companies found');
    return;
  }}
  const data = JSON.stringify({{skipped_companies: [...companies].sort()}}, null, 2);
  const blob = new Blob([data], {{type: 'application/json'}});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'skipped_companies.json';
  a.click();
  URL.revokeObjectURL(a.href);
  showToast(`Exported ${{companies.size}} skipped companies`);
}}

// ── Export applied jobs as CSV ────────────────────────
function categorizeTitle(raw) {{
  const t = (raw || '').toLowerCase();
  if (t.includes('analytics engineer')) return 'Analytics Engineer';
  // DE before AI: "Data Engineer ... AI" should bucket as DE, not AI Engineer
  if (t.includes('data engineer') || t.includes('data platform')
      || t.includes('data infrastructure') || t.includes('data pipeline')) return 'DE';
  if (/\\bai\\b/.test(t) || t.includes('machine learning')) return 'AI Engineer';
  return 'SDE';
}}

function csvCell(v) {{
  const s = String(v ?? '');
  return /[",\\n]/.test(s) ? `"${{s.replace(/"/g, '""')}}"` : s;
}}

async function exportAppliedCSV() {{
  const statuses = loadStatuses();
  const dates = loadDates();
  const today = formatDate(new Date());
  const rows = [['Company','Job Title','Application Method','Job Link','Application Date','Status','匹配度','备注']];
  document.querySelectorAll('#tableBody tr').forEach(row => {{
    const url = row.dataset.url;
    if (statuses[url] !== 'applied') return;
    rows.push([
      row.dataset.company,
      categorizeTitle(row.dataset.title),
      'Website',
      url,
      dates[url] || today,
      'Applied',
      '',
      '',
    ]);
  }});
  if (rows.length === 1) {{ showToast('No applied jobs to export'); return; }}

  // If served by the Flask backend, POST straight to the master CSV — no download file.
  if (location.protocol.startsWith('http')) {{
    try {{
      const resp = await fetch('/api/applied', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{rows: rows.slice(1)}}),
      }});
      if (resp.ok) {{
        const r = await resp.json();
        showToast(`Synced: +${{r.added}} new, ${{r.skipped}} dupes`);
        return;
      }}
      showToast('Server error ' + resp.status + ' — falling back to download');
    }} catch (e) {{
      showToast('Sync failed: ' + e.message + ' — falling back to download');
    }}
  }}

  // file:// fallback — download CSV the user can merge manually
  const csv = rows.map(r => r.map(csvCell).join(',')).join('\\n');
  const blob = new Blob(['﻿' + csv], {{type: 'text/csv;charset=utf-8'}});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'JobApplicationTracker.csv';
  a.click();
  URL.revokeObjectURL(a.href);
  showToast(`Exported ${{rows.length - 1}} applied jobs`);
}}

// ── Copy to clipboard ─────────────────────────────────
function copyField(id) {{
  const el = document.getElementById(id);
  navigator.clipboard.writeText(el.textContent.trim()).then(() => {{
    const btn = el.nextElementSibling;
    btn.textContent = 'Copied!';
    btn.classList.add('copied');
    setTimeout(() => {{ btn.textContent = 'Copy'; btn.classList.remove('copied'); }}, 1200);
  }});
}}

// ── Toast ─────────────────────────────────────────────
function showToast(msg) {{
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2500);
}}

// ── Init ──────────────────────────────────────────────
restoreStatuses();
</script>
</body></html>"""
    return html


def _append_applied_rows(incoming_rows: list[list[str]]) -> tuple[int, int]:
    """Append rows to MASTER_FILE, deduping by URL. Returns (added, skipped)."""
    # Existing URL set, defensive against empty/malformed master file
    existing_urls: set[str] = set()
    if os.path.exists(MASTER_FILE) and os.path.getsize(MASTER_FILE) > 0:
        with open(MASTER_FILE, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames and "Job Link" in reader.fieldnames:
                existing_urls = {r["Job Link"] for r in reader if r.get("Job Link")}

    # Create with header if missing entirely
    if not os.path.exists(MASTER_FILE) or os.path.getsize(MASTER_FILE) == 0:
        with open(MASTER_FILE, "w", encoding="utf-8", newline="") as f:
            csv.writer(f).writerow(CSV_HEADER)
    else:
        # Make sure file ends in newline so writer doesn't glue onto the last row
        with open(MASTER_FILE, "rb+") as f:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                f.write(b"\n")

    added, skipped = 0, 0
    with open(MASTER_FILE, "a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        for row in incoming_rows:
            if len(row) <= URL_INDEX:
                skipped += 1
                continue
            url = row[URL_INDEX]
            if not url or url in existing_urls:
                skipped += 1
                continue
            writer.writerow(row)
            existing_urls.add(url)
            added += 1
    return added, skipped


def serve(port: int = 8765, open_browser: bool = True):
    """Run a Flask backend so the Export button POSTs applied rows straight
    into JobApplicationTracker.csv (no Downloads file, no manual merge).

    open_browser=False is for LaunchAgent / headless invocations where popping
    a browser tab at every login is intrusive."""
    from flask import Flask, request, jsonify, send_from_directory
    import threading

    # Regenerate HTML so the served copy reflects the latest jobs_results.csv
    jobs = load_jobs()
    OUTPUT_HTML.write_text(generate_html(jobs), encoding="utf-8")

    app = Flask(__name__)

    @app.route("/")
    def index():
        return send_from_directory(PROJECT_DIR, "apply_assistant.html")

    @app.route("/api/applied", methods=["POST"])
    def api_applied():
        payload = request.get_json(silent=True) or {}
        rows = payload.get("rows") or []
        if not isinstance(rows, list):
            return jsonify({"error": "rows must be a list"}), 400
        added, skipped = _append_applied_rows(rows)
        return jsonify({"added": added, "skipped": skipped})

    url = f"http://localhost:{port}/"
    print(f"Serving apply assistant at {url}")
    print(f"Master CSV: {MASTER_FILE}")
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    app.run(port=port, debug=False)


def main():
    parser = argparse.ArgumentParser(description="Job Application Assistant")
    parser.add_argument("--top", type=int, default=0, help="Only include top N jobs")
    parser.add_argument("--open", type=int, default=0,
                        help="After generating, open top N job URLs in browser")
    parser.add_argument("--serve", action="store_true",
                        help="Start a local Flask server so the Export button writes directly to JobApplicationTracker.csv")
    parser.add_argument("--port", type=int, default=8765, help="Port for --serve (default 8765)")
    parser.add_argument("--no-browser", action="store_true",
                        help="Don't auto-open the browser when --serve starts (for LaunchAgent / headless use)")
    args = parser.parse_args()

    if args.serve:
        serve(args.port, open_browser=not args.no_browser)
        return

    jobs = load_jobs(args.top)
    html = generate_html(jobs)
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"Generated: {OUTPUT_HTML}  ({len(jobs)} jobs)")
    print(f"Open in browser: file://{OUTPUT_HTML.resolve()}")

    # Optionally batch-open top N
    if args.open > 0:
        to_open = jobs[:args.open]
        print(f"\nOpening top {len(to_open)} jobs in browser...")
        for j in to_open:
            webbrowser.open(j["url"])

    # Always open the assistant page
    webbrowser.open(f"file://{OUTPUT_HTML.resolve()}")


if __name__ == "__main__":
    main()
