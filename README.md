# ATS Job Scraper — Data Engineer Roles (Seattle · SF · Remote)

通过 **SerpAPI（Google 搜索）** 的 `site:` 搜索发现 ATS 公司，再直接调用 Ashby / Greenhouse / Lever 的公开 API 拉取职位数据。

> ℹ️ **为什么不用 Bing？** 微软已宣布 Bing Search API 将于 2026 年 8 月正式退役，新账号无法创建。SerpAPI 是最简单的替代方案。

---

## 第一步：获取 SerpAPI Key（免费，无需信用卡）

1. 打开 [serpapi.com](https://serpapi.com) → **Sign Up**（支持 Google/GitHub 一键登录）
2. 登录后进入 **Dashboard** → 复制页面上的 **API Key**
3. 设置环境变量：

```bash
# Mac/Linux
export SERPAPI_KEY="your_key_here"

# Windows CMD
set SERPAPI_KEY=your_key_here

# Windows PowerShell
$env:SERPAPI_KEY="your_key_here"
```

---

## 第二步：安装依赖 & 配置

```bash
pip install -r requirements.txt

# Bootstrap the gitignored personal files from their .example templates
cp config.example.json            config.json
cp JobApplicationTracker.example.csv JobApplicationTracker.csv
cp discovered_slugs.example.json     discovered_slugs.json
cp skipped_companies.example.json    skipped_companies.json
cp scripts/run_daily.sh.example      scripts/run_daily.sh
cp scripts/run_flask.sh.example      scripts/run_flask.sh

# Edit config.json with your profile, target titles, locations, etc.
# Edit the two wrapper scripts: replace <PROJECT_DIR> with the absolute project path.
chmod +x scripts/*.sh
```

Then run:

```bash
python ats_scraper.py        # 完整运行（约 5–15 分钟）
```

生成文件：
- **`jobs_results.html`** — 可视化报告，用浏览器打开（推荐）
- **`jobs_results.csv`**  — 表格，可导入 Excel / Notion / Google Sheets
- **`discovered_slugs.json`** — 已发现的公司列表，下次可复用

---

## Customizing `config.json` for different careers / regions

`config.example.json` is generic on purpose. The scraper has zero career/region hardcoding — it just runs whatever filters & weights you put in `config.json`. Three example flavors to show the range:

### Example 1 — US software engineer (the default flavor)

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

`config.json` is gitignored — `config.example.json` is the only thing in the repo.

---

## 常用命令

| 命令 | 说明 |
|---|---|
| `python ats_scraper.py` | 完整运行（搜索发现 + API 爬取）|
| `python ats_scraper.py --no-search` | 跳过搜索，复用上次的公司列表（快，几分钟，不消耗 API 额度）|
| `python ats_scraper.py --ats ashby` | 只爬 Ashby（AI 初创公司最多用这个）|
| `python ats_scraper.py --export html` | 只生成 HTML 报告 |
| `python ats_scraper.py --add-slug ashby openai modal-labs` | 手动添加公司 slug |

**推荐节奏：**
- 每月 1 次完整运行（发现新公司，消耗约 27 次搜索额度）
- 每周用 `--no-search` 刷新职位（**0 消耗**，直接调用 ATS API）

---

## SerpAPI 免费额度用量

| 操作 | 消耗次数 |
|---|---|
| 完整运行（3 ATS × 3 模板 × 3 地点）| 27 次 |
| `--no-search` 刷新 | 0 次 |
| 免费额度 | 100 次/月 |
| **可跑完整发现** | **约 3 次/月** |

---

## 评分说明（0–100）

| 条件 | 分值 |
|---|---|
| 标题匹配 "data engineer" | +50 |
| 标题匹配 "analytics engineer" | +45 |
| 标题匹配 "data platform engineer" | +45 |
| Senior / Staff 级别 | +10 |
| 地点：Seattle / Bellevue | +25 |
| 地点：Remote | +20 |
| 地点：San Francisco | +15 |
| 技术栈关键词（每个 +3，最高）| +25 |

分数 < 35 的职位会被自动过滤。

---

## 手动添加已知公司

```bash
# 一次性添加多个 Ashby slug
python ats_scraper.py --add-slug ashby openai anthropic modal-labs replicate

# 然后刷新
python ats_scraper.py --no-search
```

常见 AI 初创公司的 Ashby slug：
```
openai, anthropic, scale-ai, cohere, modal-labs, replicate,
together-ai, anyscale, weights-biases, labelbox, snorkel-ai,
cleanlab, comet-ml, activeloop, qdrant
```

---

## 定期自动运行

```bash
# Mac/Linux crontab — 每周一 8am 刷新职位（不消耗 API）
0 8 * * 1 cd /path/to/scraper && python ats_scraper.py --no-search >> weekly.log 2>&1

# 每月 1 号重新发现新公司
0 9 1 * * cd /path/to/scraper && python ats_scraper.py >> monthly.log 2>&1
```

---

## Troubleshooting

### Chrome / Edge / Arc: "Open Selected in Tabs" only opens one tab

Chromium-based browsers treat "multiple tabs from one user gesture" as a popup
flood — by default the first call succeeds and the rest are silently blocked.
**There is no JS workaround**; popups must be allowed at the browser level.

One-time fix:
1. Click "Open Selected in Tabs"
2. A crossed-out window icon 🔲 appears at the right edge of the URL bar
   (next to the reload button)
3. Click that icon → select **"Always allow pop-ups and redirects from
   http://localhost:8765"** → Done
4. Click "Open Selected in Tabs" again — all selected tabs open at once,
   and the permission is remembered for future sessions

Safari: Safari menu → Settings → Websites → Pop-up Windows → set localhost to **Allow**.
