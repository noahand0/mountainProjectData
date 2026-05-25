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
from bs4 import BeautifulSoup, NavigableString

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
    h1 = soup.find("h1")
    name = h1.get_text(strip=True) if h1 else (soup.title.string or "").split("|")[0].strip()

    # --- Coordinates ---
    # MP embeds lat/lon in JSON-LD or as data attributes on a map element
    lat, lon = None, None
    coord_match = re.search(
        r'"latitude"\s*:\s*([\-\d.]+).*?"longitude"\s*:\s*([\-\d.]+)',
        html,
        re.DOTALL,
    )
    if coord_match:
        lat = float(coord_match.group(1))
        lon = float(coord_match.group(2))
    else:
        # Fallback: look for a map element with data-lat / data-lng attributes
        map_el = soup.find(attrs={"data-lat": True})
        if map_el:
            try:
                lat = float(map_el["data-lat"])
                lon = float(map_el["data-lng"])
            except (KeyError, ValueError):
                pass

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
    grade_yds = (
        "".join(t for t in grade_yds_tag.children if isinstance(t, NavigableString)).strip()
        if grade_yds_tag else None
    ) or None
    grade_vscale = (
        "".join(t for t in grade_vscale_tag.children if isinstance(t, NavigableString)).strip()
        if grade_vscale_tag else None
    ) or None

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

        # Grade — boulders use rateYDS for V-scale on the listing page
        grade_tag = row.find("span", class_="rateYDS")
        grade_yds = grade_tag.get_text(strip=True) if grade_tag else None
        hueco_tag = row.find("span", class_="rateHueco")
        grade_vscale = hueco_tag.get_text(strip=True) if hueco_tag else None
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
# Ticks/stats page parser  (/route/stats/{route_id})
# ---------------------------------------------------------------------------

def parse_ticks(html: str, route_id: int) -> list[dict]:
    """
    Parse the /route/stats/{id} page.
    Returns a list of tick dicts.
    """
    soup = BeautifulSoup(html, "lxml")
    ticks = []
    now = _now()

    # Ticks are in a table; each row is one tick entry
    # Columns (approximate): Date | User | Style/Type | Stars | Comment
    for row in soup.select("table tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue

        date_climbed = cells[0].get_text(strip=True) or None

        author_link = cells[1].find("a")
        author = author_link.get_text(strip=True) if author_link else cells[1].get_text(strip=True)

        tick_type = cells[2].get_text(strip=True) if len(cells) > 2 else None

        stars = None
        if len(cells) > 3:
            stars = _count_stars(cells[3])

        comment = cells[4].get_text(strip=True) if len(cells) > 4 else None

        if not author and not date_climbed:
            continue

        ticks.append(
            {
                "route_id": route_id,
                "author": author or None,
                "date_climbed": date_climbed,
                "tick_type": tick_type or None,
                "stars": stars,
                "comment": comment or None,
                "scraped_at": now,
            }
        )

    return ticks


def _count_stars(cell) -> int | None:
    """
    Count filled stars in a table cell.
    MP renders stars as <img> tags or <span> elements with a class like
    'fa-star' (filled) vs 'fa-star-o' (empty). Adapt if needed.
    """
    # Try Font Awesome icon approach
    filled = cell.find_all(class_=re.compile(r"\bfa-star\b(?!.*-o)", re.I))
    if filled:
        return len(filled)

    # Try counting star images (some MP pages use img tags)
    imgs = cell.find_all("img", src=re.compile(r"star", re.I))
    if imgs:
        return len(imgs)

    # Try plain text digit
    text = cell.get_text(strip=True)
    m = re.search(r"(\d)", text)
    return int(m.group(1)) if m else None


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
