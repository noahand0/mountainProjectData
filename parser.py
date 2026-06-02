"""
HTML parsers for Mountain Project pages.

Selectors are based on MP's page structure and may need adjustment if the site
changes. The easiest way to verify/fix a selector is:
  1. Save a raw page HTML from raw_pages to a file
  2. Open it in a browser or inspect it with soup.prettify()
  3. Find the right selector and update here
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from bs4 import BeautifulSoup, NavigableString, Comment

MP_BASE = "https://www.mountainproject.com"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_id(url: str) -> int | None:
    m = re.search(r"/(area|route)/(\d+)", url)
    return int(m.group(2)) if m else None


def _abs_url(href: str) -> str:
    return href if href.startswith("http") else MP_BASE + href


def _clean(href: str) -> str:
    """Strip query params and fragments from a URL."""
    return href.split("?")[0].split("#")[0]


# ---------------------------------------------------------------------------
# Area page parser
# ---------------------------------------------------------------------------

def parse_area(html: str, url: str) -> tuple[dict, list[str], list[str]]:
    """
    Parse an area page.
    Returns (area_dict, child_area_urls, route_urls).
    child_area_urls are direct sub-areas only (breadcrumb parents are excluded).
    """
    soup = BeautifulSoup(html, "lxml")
    area_id = _extract_id(url)

    # --- Name ---
    # Use only direct text nodes — MP puts the area type in a child <span> and
    # an HTML comment (<!--EDIT-...-->) in the h1; Comment is a NavigableString
    # subclass, so we must exclude it explicitly.
    h1 = soup.find("h1")
    if h1:
        name = "".join(
            t for t in h1.children
            if isinstance(t, NavigableString) and not isinstance(t, Comment)
        ).strip()
        if not name:
            name = h1.get_text(strip=True)
    else:
        name = (soup.title.string or "").split("|")[0].strip()

    # --- Coordinates ---
    # MP links to Google Maps with a ?q=lat,lon parameter in a directions anchor.
    lat, lon = None, None
    coord_match = re.search(
        r'maps\.google\.com/maps\?q=(-?\d+\.\d+),(-?\d+\.\d+)',
        html,
    )
    if coord_match:
        lat = float(coord_match.group(1))
        lon = float(coord_match.group(2))

    # --- Breadcrumb area IDs (ancestors; exclude from children) ---
    # MP's breadcrumb is in div.mb-half.small.text-warm inside the right column.
    breadcrumb_ids: set[int] = set()
    breadcrumb = soup.find("div", class_=lambda c: c and "mb-half" in c and "text-warm" in c)
    if breadcrumb:
        for a in breadcrumb.find_all("a", href=re.compile(r"/area/\d+")):
            bid = _extract_id(a["href"])
            if bid:
                breadcrumb_ids.add(bid)

    # --- Child area URLs ---
    # MP lists direct sub-areas in the left sidebar (div.mp-sidebar / div.left-nav).
    # This is the authoritative list; the route table only shows a curated subset.
    child_area_urls: list[str] = []
    seen_areas: set[int] = set()

    sidebar = soup.find("div", class_="mp-sidebar") or soup.find("div", class_=lambda c: c and "left-nav" in c)
    if sidebar:
        for a in sidebar.find_all("a", href=re.compile(r"/area/\d+")):
            href = _clean(_abs_url(a["href"]))
            child_id = _extract_id(href)
            if (
                child_id
                and child_id != area_id
                and child_id not in breadcrumb_ids
                and child_id not in seen_areas
            ):
                child_area_urls.append(href)
                seen_areas.add(child_id)

    # --- Route URLs (used by --deep-routes mode) ---
    # Leaf pages use class "width100"; parent pages use "route-table" and defer
    # route collection to when we visit each child area.
    route_urls: list[str] = []
    seen_routes: set[int] = set()

    leaf_table = soup.find("table", class_="width100")
    if leaf_table:
        for a in leaf_table.find_all("a", href=re.compile(r"/route/\d+")):
            href = _clean(_abs_url(a["href"]))
            route_id = _extract_id(href)
            if route_id and route_id not in seen_routes:
                route_urls.append(href)
                seen_routes.add(route_id)

    area = {
        "area_id": area_id,
        "parent_area_id": None,  # set by crawler
        "name": name,
        "url": url,
        "latitude": lat,
        "longitude": lon,
        "scraped_at": _now(),
    }

    return area, child_area_urls, route_urls


# ---------------------------------------------------------------------------
# Route page parser
# ---------------------------------------------------------------------------


def parse_route(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    route_id = _extract_id(url)

    # --- Name ---
    h1 = soup.find("h1")
    name = h1.get_text(strip=True) if h1 else ""

    # --- Grade ---
    # MP shows YDS grades in <span class="rateYDS"> and V-scale in <span class="rateHueco">
    # Grade — rateYDS span may contain a child <a>YDS</a> label link; use direct
    # text nodes only to avoid appending that label to the grade string.
    grade_yds_tag = soup.find("span", class_="rateYDS")
    grade_vscale_tag = soup.find("span", class_="rateHueco")
    raw_yds = (
        "".join(t for t in grade_yds_tag.children if isinstance(t, NavigableString)).strip()
        if grade_yds_tag else None
    ) or None
    grade_vscale = (
        "".join(t for t in grade_vscale_tag.children if isinstance(t, NavigableString)).strip()
        if grade_vscale_tag else None
    ) or None

    # MP sometimes uses rateYDS for V-scale grades; route to the right column.
    if raw_yds and re.match(r"^V", raw_yds, re.I):
        grade_yds = None
        grade_vscale = grade_vscale or raw_yds
    else:
        grade_yds = raw_yds

    parts = [g for g in [grade_yds, grade_vscale] if g]
    grade_raw = " ".join(parts) if parts else None

    # --- Route type ---
    # The type ("Boulder", "Sport", etc.) is the second CSS class on the route-type
    # span, not its text content (which is the grade).
    type_tag = soup.find("span", class_=re.compile(r"\broute-type\b", re.I))
    route_type = None
    if type_tag:
        extra = [c for c in type_tag.get("class", []) if c.lower() != "route-type"]
        route_type = extra[0] if extra else None

    # --- Length and pitches ---
    length_ft = None
    pitches = None
    # MP shows these in a table row: "Length: 70 ft (21 m), 1 pitch"
    length_row = soup.find(string=re.compile(r"Length", re.I))
    if length_row:
        length_text = length_row.parent.get_text()
        ft_match = re.search(r"(\d+)\s*ft", length_text, re.I)
        pitch_match = re.search(r"(\d+)\s*pitch", length_text, re.I)
        if ft_match:
            length_ft = int(ft_match.group(1))
        if pitch_match:
            pitches = int(pitch_match.group(1))

    # --- First ascent ---
    fa = None
    fa_label = soup.find(string=re.compile(r"^FA\s*:", re.I))
    if fa_label:
        fa = fa_label.parent.get_text(strip=True)
        fa = re.sub(r"^FA\s*:\s*", "", fa, flags=re.I)

    # --- Description / Protection ---
    description = _extract_section(soup, "description")
    protection = _extract_section(soup, "protection")

    # --- Star rating ---
    # MP shows avg stars and vote count in the page
    avg_stars = None
    star_votes = None

    # The avg stars value is often in a span with id containing "starsWithAvg"
    stars_span = soup.find("span", id=re.compile(r"starsWithAvg", re.I))
    if stars_span:
        m = re.search(r"Avg:\s*([\d.]+)", stars_span.get_text())
        if m:
            avg_stars = float(m.group(1))
        votes_m = re.search(r"([\d,]+)\s*vote", stars_span.get_text(), re.I)
        if votes_m:
            star_votes = int(votes_m.group(1).replace(",", ""))

    # tick_count and todo_count are rendered via JavaScript on the stats page
    # and are not present in the static HTML — left as None.
    tick_count = None
    todo_count = None

    return {
        "route_id": route_id,
        "area_id": None,  # set by crawler
        "name": name,
        "url": url,
        "type": route_type,
        "grade_yds": grade_yds,
        "grade_vscale": grade_vscale,
        "grade_raw": grade_raw,
        "length_ft": length_ft,
        "pitches": pitches,
        "fa": fa,
        "description": description,
        "protection": protection,
        "avg_stars": avg_stars,
        "star_votes": star_votes,
        "tick_count": tick_count,
        "todo_count": todo_count,
        "scraped_at": _now(),
    }


def _extract_section(soup: BeautifulSoup, label: str) -> str | None:
    """Find a labeled content section (Description, Protection, etc.)."""
    # Use get_text() matching because MP's headers contain nested edit links,
    # so string= (which requires an exact NavigableString) never matches.
    header = next(
        (h for h in soup.find_all(["h2", "h3", "h4"])
         if re.search(rf"\b{label}\b", h.get_text(), re.I)),
        None,
    )
    if not header:
        return None
    sibling = header.find_next_sibling(["div", "p"])
    return sibling.get_text(separator="\n", strip=True) if sibling else None


# ---------------------------------------------------------------------------
# Lightweight route extraction from the area listing table
# (no per-route HTTP fetch required)
# ---------------------------------------------------------------------------

def parse_routes_from_area(html: str, area_id: int) -> list[dict]:
    """
    Extract basic route data from the area page's width100 table.
    Returns route dicts suitable for db.upsert_route.
    Missing fields (description, FA, ticks…) are left None.
    """
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", class_="width100")
    if not table:
        return []

    routes = []
    now = _now()

    for row in table.find_all("tr"):
        link = row.find("a", href=re.compile(r"/route/\d+"))
        if not link:
            continue

        route_url = _clean(_abs_url(link["href"]))
        route_id = _extract_id(route_url)
        if not route_id:
            continue

        name = link.get_text(strip=True)

        # Grade — MP uses the rateYDS CSS class for both YDS and V-scale grades
        # on area listing pages. Route to the correct column by grade format.
        grade_tag = row.find("span", class_="rateYDS")
        raw_grade = grade_tag.get_text(strip=True) if grade_tag else None
        hueco_tag = row.find("span", class_="rateHueco")
        raw_hueco = hueco_tag.get_text(strip=True) if hueco_tag else None

        if raw_grade and re.match(r"^V", raw_grade, re.I):
            grade_yds = None
            grade_vscale = raw_grade
        else:
            grade_yds = raw_grade or None
            grade_vscale = None
        if raw_hueco:
            grade_vscale = raw_hueco

        parts = [g for g in [grade_yds, grade_vscale] if g]
        grade_raw = " ".join(parts) if parts else None

        # Type — second class on the route-type span, e.g. "Boulder" or "Sport"
        type_span = row.find("span", class_=re.compile(r"\broute-type\b"))
        route_type = None
        if type_span:
            extra = [c for c in type_span.get("class", []) if c != "route-type"]
            route_type = extra[0] if extra else None

        # Stars — count filled/half SVG images inside scoreStars
        stars_span = row.find("span", class_="scoreStars")
        avg_stars = None
        if stars_span:
            full = len(stars_span.find_all("img", src=re.compile(r"starBlue\.svg$")))
            half = len(stars_span.find_all("img", src=re.compile(r"starBlueHalf\.svg$")))
            avg_stars = full + 0.5 * half if (full or half) else None

        routes.append({
            "route_id": route_id,
            "area_id": area_id,
            "name": name,
            "url": route_url,
            "type": route_type,
            "grade_yds": grade_yds,
            "grade_vscale": grade_vscale,
            "grade_raw": grade_raw,
            "length_ft": None,
            "pitches": None,
            "fa": None,
            "description": None,
            "protection": None,
            "avg_stars": avg_stars,
            "star_votes": None,
            "tick_count": None,
            "todo_count": None,
            "scraped_at": now,
        })

    return routes


# ---------------------------------------------------------------------------
# Stats page parsers  (/route/stats/{route_id}/{slug})
#
# The rendered page has four table.table-striped elements:
#   tr[id^="stars."]   — individual star ratings
#   tr[id^="ratings."] — suggested grades
#   tr[id^="ticks."]   — logged ticks (date, style, comment)
#   (no id prefix)     — to-do list users
# ---------------------------------------------------------------------------

def parse_ticks(html: str, route_id: int) -> list[dict]:
    """Parse logged ticks from the stats page. Returns a list of tick dicts."""
    soup = BeautifulSoup(html, "lxml")
    ticks = []
    now = _now()
    seen = set()

    for row in soup.select("tr[id^='ticks.']"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue

        # Col 0: user link or <strong>Private Tick</strong>
        user_link = cells[0].find("a")
        author = user_link.get_text(strip=True) if user_link else None

        # Col 1: <strong>Date</strong> · Style. Comment text
        date_tag = cells[1].find("strong")
        date_climbed = date_tag.get_text(strip=True) if date_tag else None

        # Everything after the date tag is " · Style. Optional comment."
        tick_type = None
        comment = None
        cell_text = cells[1].get_text(separator=" ", strip=True)
        if date_climbed:
            after_date = cell_text[len(date_climbed):].strip().lstrip("·•").strip()
            if after_date and "No names/notes" not in after_date:
                # Split "Send. Rest of comment" into type and comment
                dot_idx = after_date.find(". ")
                if dot_idx != -1:
                    tick_type = after_date[:dot_idx].strip() or None
                    comment = after_date[dot_idx + 2:].strip() or None
                else:
                    tick_type = after_date.rstrip(".").strip() or None

        # Deduplicate by (author, date_climbed, tick_type)
        key = (author, date_climbed, tick_type)
        if key in seen:
            continue
        seen.add(key)

        ticks.append({
            "route_id": route_id,
            "author": author,
            "date_climbed": date_climbed,
            "tick_type": tick_type,
            "stars": None,
            "comment": comment,
            "scraped_at": now,
        })

    return ticks


def parse_suggested_ratings(html: str, route_id: int) -> list[dict]:
    """Parse suggested grade ratings from the stats page."""
    soup = BeautifulSoup(html, "lxml")
    now = _now()
    ratings = []

    for row in soup.select("tr[id^='ratings.']"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        user_link = cells[0].find("a")
        author = user_link.get_text(strip=True) if user_link else None
        grade = cells[1].get_text(strip=True) or None
        if grade:
            ratings.append({
                "route_id": route_id,
                "author": author,
                "grade": grade,
                "scraped_at": now,
            })

    return ratings


def parse_stats_counts(html: str) -> tuple[int, int]:
    """Return (tick_count, todo_count) from the stats page."""
    soup = BeautifulSoup(html, "lxml")
    ticks = len(soup.select("tr[id^='ticks.']"))
    # To-do rows have no id prefix; they're in the table that only has single-cell rows
    todos = 0
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if rows and all(len(r.find_all("td")) == 1 for r in rows):
            todos = len(rows)
            break
    return ticks, todos




# ---------------------------------------------------------------------------
# Route comments parser  (the non-tick comment section on a route page)
# ---------------------------------------------------------------------------

def parse_route_comments(html: str, route_id: int) -> list[dict]:
    """
    Parse the user comments section on a route page (beta / general notes).
    These are distinct from ticks.
    """
    soup = BeautifulSoup(html, "lxml")
    comments = []
    now = _now()

    # MP renders comments in a <div class="comment-body"> or similar structure
    for block in soup.find_all(class_=re.compile(r"comment", re.I)):
        author_tag = block.find(class_=re.compile(r"author|user", re.I))
        author = author_tag.get_text(strip=True) if author_tag else None

        date_tag = block.find(["time", "span"], class_=re.compile(r"date|time", re.I))
        posted_at = None
        if date_tag:
            posted_at = date_tag.get("datetime") or date_tag.get_text(strip=True)

        text_tag = block.find(class_=re.compile(r"body|text|content", re.I))
        text = text_tag.get_text(separator="\n", strip=True) if text_tag else block.get_text(strip=True)

        if not text:
            continue

        comments.append(
            {
                "route_id": route_id,
                "author": author,
                "posted_at": posted_at,
                "text": text,
                "scraped_at": now,
            }
        )

    return comments
