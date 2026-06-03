# Climbing Hidden Gems

A web scraper, database, and interactive web app that surfaces underrated climbing routes by analyzing tick history and star ratings from Mountain Project.

---

## Problem & Motivation

Mountain Project is the dominant public database for climbing routes in the United States. It has ratings, reviews, and tick logs for hundreds of thousands of climbs — but its default rankings are entirely popularity-driven. The most-ticked, most-starred routes dominate the "classics" pages (see local example by scrolling midway down this page: https://www.mountainproject.com/area/107178880/castle-and-sanborn-area-bouldering), while many high quality climbs fall under the radar.

This project builds a system to answer the question: **given everything Mountain Project knows about a climbing area, which routes are most underrated relative to their actual quality?**

---

## What Was Built

The project has four components:

### 1. Web Scraper (`main.py`, `crawler.py`, `fetcher.py`, `parser.py`)
A breadth-first crawler that traverses Mountain Project's area hierarchy, collecting routes, star ratings, tick logs, suggested grades, and todo counts. Stats pages (ticks, ratings) are rendered via headless Chromium (Playwright) since they are JavaScript-loaded. Plain HTTP is used for area and route pages.

### 2. SQLite Database (`climbing.db`)
Structured storage for the full area hierarchy, route metadata, and per-route social data:

| Table | Contents |
|---|---|
| `areas` | Hierarchy of climbing areas with parent-child relationships and GPS coordinates |
| `routes` | Route metadata: grade, type, description, FA, star rating, tick/todo counts |
| `ticks` | Individual logged ascents with date, style, and user comment |
| `suggested_ratings` | Grade suggestions submitted by users |

Current dataset (Yosemite Valley + surrounding areas):
- **1,139 areas**, **4,305 routes**, **174,090 ticks** from **18,880 unique users**
- **35,777** suggested grade ratings

### 3. Hidden Gem Scoring Engine (`stats.py`)
A statistical ranking that identifies routes with high quality relative to how well-known they are **within their grade**. The formula:

```
score = avg_stars × (1 − popularity_percentile_within_grade) ^ 0.3
```

- **`avg_stars`**: Mountain Project community star rating (0–4 scale)
- **`popularity_percentile_within_grade`**: where this route ranks by tick count among all routes of the same grade in the queried area (0 = least ticked, 1 = most ticked)
- **Exponent 0.3**: softens the penalty so somewhat popular good routes aren't crushed

Controlling for grade is the key design decision: without it, the ranking trivially surfaces hard routes, since V10s always have fewer ticks than V4s regardless of quality.

### 4. Streamlit Web App (`app.py`)
An interactive browser UI with:
- Area search with hierarchical drill-down and back navigation
- Quick-start buttons showing the largest areas in the current database
- Boulder / route type filter
- V-scale grade range filter (boulders only)
- Ranked results table with clickable links to Mountain Project
- Interactive pydeck map showing route locations, with click-to-highlight

---

## Technical Approach

### Scraping
Mountain Project's area pages list child areas in a left sidebar (`div.mp-sidebar`) and routes in a `table.width100`. The scraper does a BFS over the area graph, maintaining correct `parent_area_id` at each level. Route stats pages (`/route/stats/{id}/{slug}`) are JavaScript-rendered and require Playwright with a `domcontentloaded` + `wait_for_selector` strategy rather than `networkidle`, which reduced per-page wait time substantially.

HTML is cached in a `raw_pages` SQLite table so re-runs parse from cache rather than re-fetching. This also allows re-parsing after parser fixes without a new network run.

Key parsing challenges solved:
- Mountain Project embeds area GPS coordinates in a Google Maps anchor (`?q=lat,lon`), not JSON-LD
- Area names include a hidden type label (`<span class="hidden-md-down">`) inside the `<h1>` — and an HTML comment (`<!--EDIT-...-->`) that is a `NavigableString` subclass in BeautifulSoup, requiring explicit `Comment` exclusion
- V-scale grades appear in `rateYDS`-classed spans on area listing pages, requiring grade-format detection to route them to the correct database column
- As is clear from the commit history, getting the webscraper and parser to actually log the correct data in a reasonable way was the most challenging part of this project.

### Scoring Design Iterations
The initial formula used a log penalty on raw tick count (`avg_stars / log10(tick_count + 10)`). This was replaced with grade-relative percentile scoring after observing that the log approach systematically ranked hard routes first regardless of quality. The penalty exponent was tuned from 1.0 (linear, too harsh) to 0.5 (softer) to 0.3 (current) based on inspection of results across areas of different sizes.

---

## Evaluation & Evidence

### Validation on Known Area (Castle Rock Loop)
The system was validated on Castle Rock Loop (San Jose, CA) — a small bouldering area that I know well. The top-ranked results under a V6 cap were verified manually:

- Routes ranked highly (e.g. *Yabo*, *North Shore aka Waimea Arete*, *The Beak*) are genuinely quality problems that see less traffic than the area classics
- Routes ranked low (e.g. *Bates Arete*, *The Spoon*, *Waimea Wall*) are the area's most-trafficked classics — correctly identified as well-known, and even somewhat overrated.
- The grade-relative scoring successfully distributes recommendations across V1–V6, rather than concentrating on a single difficulty

### Limitations
- **Tick cap**: Mountain Project displays a maximum of 250 ticks per route. High-traffic routes are likely more popular than the data indicates, which slightly underestimates their popularity percentile.
- **Grade bucket size**: The grade-relative percentile is only meaningful when a grade bucket contains enough routes. In small areas (< 50 routes), buckets may have 2–3 entries, making percentile values coarse.
- **Webscraping speed**: The mountainproject.com stated query-rate on their "robots.txt" is once per minute, which is far too slow. However, in the name of at least somewhat respecting a limited query rate (especially because mountainproject is a completely free, and community run site), I imposed a query rate limiter of 1 second. This meant that the fastest I could possibly gather all of the date for, say Yosemite Valley, which has 1,800+ routes, is 30 minutes. In practice, the scraping and parsing took far longer than that per route, and getting yosemite into my dataset took running my computer overnight. For this reason, I opted to only gather a few major areas like that, just to show a proof of concept.

---

## Setup & Usage

### Requirements
```
Python 3.10+
pip install -r requirements.txt
playwright install chromium
```

### Scraping a new area
```bash
python main.py <mountain-project-area-url> --deep-routes
```
Example:
```bash
python main.py https://www.mountainproject.com/area/105833388/yosemite-valley --deep-routes
```
The `--deep-routes` flag fetches individual route pages (for description, FA, protection) and stats pages (for ticks and suggested ratings). Without it, only basic route data is extracted from area listing pages.

After a completed scrape, the raw HTML cache can be cleared to reclaim space:
```bash
sqlite3 climbing.db "DELETE FROM raw_pages; VACUUM;"
```

### Running the web app
```bash
streamlit run app.py
```

### Running the terminal interface
```bash
python stats.py
```

---

## AI Usage Disclosure

This project was built with substantial assistance from Claude (Anthropic) via Claude Code. AI was used throughout: architecture decisions, HTML parsing, scoring formula design, debugging, and the Streamlit UI. The core ideas — the problem framing, the grade-relative scoring approach, and the decision to use tick history as a proxy for route popularity — originated in discussion with the AI but were directed and refined by me through iterative testing on real data.

I should also note that prior to this project, I knew nothing about webscraping, common webscraping challenges, parsing HTML, etc. It was all new to me, and it was very fun to see just how far I could get in a completely foreign regime by leveraging AI tools.

---

## Data Source

All climbing data is sourced from [Mountain Project](https://www.mountainproject.com) and is used here for non-commercial academic research. The scraper respects a polite crawl delay and identifies itself via a custom User-Agent string (`CS153-research/1.0`).
