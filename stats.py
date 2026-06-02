"""
Terminal interface for querying climbing statistics.

Usage:
    python stats.py
"""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict

import db

MIN_STAR_VOTES = 3
MAX_RESULTS = 50
PENALTY_EXPONENT = 0.3  # < 1 softens the popularity penalty; 1.0 = fully linear


# ---------------------------------------------------------------------------
# Grade parsing
# ---------------------------------------------------------------------------

def grade_to_num(grade: str | None) -> float | None:
    """Convert a grade string (V-scale or YDS) to a numeric value for bucketing."""
    if not grade:
        return None
    g = grade.strip()

    # --- V-scale ---
    if g.lower() in ("v-easy", "vb"):
        return -1.0

    m = re.match(r"^[Vv](\d+)-(\d+)$", g)       # V6-7 range → average
    if m:
        return (int(m.group(1)) + int(m.group(2))) / 2.0

    m = re.match(r"^[Vv](\d+)([+-]?)$", g)       # V4, V4+, V4-
    if m:
        base = int(m.group(1))
        mod = {"+": 0.3, "-": -0.3}.get(m.group(2), 0.0)
        return base + mod

    # --- YDS (5.x) — mapped to a separate numeric range starting at 100 ---
    # so V-scale and YDS grades never share a bucket.
    m = re.match(r"^5\.(\d+)([a-dA-D]?)([+-]?)/?\w*$", g)
    if m:
        base = int(m.group(1))              # 5, 6, 7, … 15
        letter = m.group(2).lower()
        letter_off = {"a": 0.0, "b": 0.25, "c": 0.5, "d": 0.75}.get(letter, 0.0)
        mod = {"+": 0.1, "-": -0.1}.get(m.group(3), 0.0)
        return 100 + base + letter_off + mod

    return None


def parse_vscale_input(text: str) -> float | None:
    """Parse a user-supplied V-scale grade. Returns None if unparseable."""
    num = grade_to_num(text.strip())
    # Only accept V-scale results (< 100); reject if YDS was accidentally entered
    return num if (num is not None and num < 100) else None


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def compute_percentiles(routes: list[sqlite3.Row]) -> dict[int, float]:
    """
    For each route, compute its tick-count percentile within its grade bucket.
    V-scale and YDS grades are bucketed separately. Returns {route_id: 0.0–1.0}.
    """
    buckets: dict[int | None, list[sqlite3.Row]] = defaultdict(list)
    for r in routes:
        num = grade_to_num(r["grade_vscale"] or r["grade_yds"] or r["grade_raw"])
        bucket = round(num) if num is not None else None
        buckets[bucket].append(r)

    percentiles: dict[int, float] = {}
    for bucket_routes in buckets.values():
        sorted_routes = sorted(bucket_routes, key=lambda r: r["tick_count"] or 0)
        n = len(sorted_routes)
        for i, r in enumerate(sorted_routes):
            percentiles[r["route_id"]] = i / (n - 1) if n > 1 else 0.5

    return percentiles


def hidden_gem_score(route: sqlite3.Row, popularity_percentile: float) -> float | None:
    """Score = avg_stars × (1 − popularity_percentile)^PENALTY_EXPONENT.
    The exponent softens the penalty so mid-popularity routes aren't crushed."""
    if (route["star_votes"] or 0) < MIN_STAR_VOTES:
        return None
    return (route["avg_stars"] or 0) * (1.0 - popularity_percentile) ** PENALTY_EXPONENT


# ---------------------------------------------------------------------------
# Database queries
# ---------------------------------------------------------------------------

def find_areas(conn: sqlite3.Connection, query: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT area_id, name FROM areas WHERE name LIKE ? ORDER BY name",
        (f"%{query}%",),
    ).fetchall()


def get_routes_in_area(conn: sqlite3.Connection, area_id: int) -> list[sqlite3.Row]:
    sql = """
    WITH RECURSIVE descendants AS (
        SELECT area_id FROM areas WHERE area_id = ?
        UNION ALL
        SELECT a.area_id FROM areas a JOIN descendants d ON a.parent_area_id = d.area_id
    )
    SELECT r.route_id, r.name, r.url, r.grade_vscale, r.grade_yds, r.grade_raw,
           r.avg_stars, r.star_votes, r.tick_count, r.todo_count, r.type,
           a.name AS area_name
    FROM routes r
    JOIN areas a ON r.area_id = a.area_id
    WHERE r.area_id IN (SELECT area_id FROM descendants)
    """
    return conn.execute(sql, (area_id,)).fetchall()


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def show_hidden_gems(
    conn: sqlite3.Connection,
    area_id: int,
    area_name: str,
    climb_type: str,        # "boulder" or "route"
    max_grade: float | None,
) -> None:
    all_routes = get_routes_in_area(conn, area_id)
    if not all_routes:
        print("  No routes found.\n")
        return

    # Filter by type
    if climb_type == "boulder":
        typed = [r for r in all_routes if (r["type"] or "").lower() == "boulder"]
    else:
        typed = [r for r in all_routes if (r["type"] or "").lower() != "boulder"]

    # Grade filter (boulders only — routes have no cap for now)
    if climb_type == "boulder" and max_grade is not None:
        filtered = [
            r for r in typed
            if (lambda n: n is not None and n <= max_grade)(
                grade_to_num(r["grade_vscale"] or r["grade_yds"] or r["grade_raw"])
            )
        ]
    else:
        filtered = typed

    if not filtered:
        print("  No routes match those filters.\n")
        return

    percentiles = compute_percentiles(filtered)

    scored = []
    skipped = 0
    for r in filtered:
        pct = percentiles.get(r["route_id"], 0.5)
        score = hidden_gem_score(r, pct)
        if score is None:
            skipped += 1
            continue
        scored.append((score, r))

    scored.sort(key=lambda x: x[0], reverse=True)
    displayed = scored[:MAX_RESULTS]

    type_label = "Boulders" if climb_type == "boulder" else "Routes"
    grade_note = f"≤ V{max_grade:.0f}" if (climb_type == "boulder" and max_grade is not None) else "all grades"
    print(f"\nHidden gems in: {area_name}  [{type_label}, {grade_note}]")
    print(f"  Showing top {len(displayed)} of {len(scored)} ranked  ({skipped} skipped — fewer than {MIN_STAR_VOTES} star votes)\n")
    print(f"{'#':<4} {'Score':<7} {'Stars':<7} {'Ticks':<7} {'Grade':<7} {'Area':<28} Route")
    print("-" * 92)

    for rank, (score, r) in enumerate(displayed, 1):
        grade = r["grade_vscale"] or r["grade_yds"] or r["grade_raw"] or "?"
        stars = f"{r['avg_stars']:.1f}" if r["avg_stars"] is not None else "?"
        ticks = str(r["tick_count"]) if r["tick_count"] is not None else "?"
        area = (r["area_name"] or "")[:27]
        print(f"{rank:<4} {score:<7.2f} {stars:<7} {ticks:<7} {grade:<7} {area:<28} {r['name']}")

    print()


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

def pick_area(conn: sqlite3.Connection, query: str) -> tuple[int, str] | None:
    matches = find_areas(conn, query)
    if not matches:
        print(f"  No areas found matching '{query}'.\n")
        return None
    if len(matches) == 1:
        return matches[0]["area_id"], matches[0]["name"]

    print(f"\n  Multiple matches for '{query}':")
    for i, row in enumerate(matches, 1):
        print(f"    {i}. {row['name']}")
    choice = input("  Pick a number (or Enter to cancel): ").strip()
    if not choice.isdigit() or not (1 <= int(choice) <= len(matches)):
        return None
    row = matches[int(choice) - 1]
    return row["area_id"], row["name"]


def prompt_climb_type() -> str:
    while True:
        raw = input("  Type (b=boulders, r=routes): ").strip().lower()
        if raw in ("b", "boulder", "boulders"):
            return "boulder"
        if raw in ("r", "route", "routes"):
            return "route"
        print("  Please enter 'b' for boulders or 'r' for routes.")


def prompt_max_grade() -> float | None:
    raw = input("  Max grade (e.g. V6, or Enter for all): ").strip()
    if not raw:
        return None
    num = parse_vscale_input(raw)
    if num is None:
        print(f"  Couldn't parse '{raw}' as a V-scale grade — showing all grades.\n")
    return num


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    conn = db.get_conn()
    print("Climbing Stats — Hidden Gem Finder")
    print("Ranks climbs by star rating relative to how well-known they are for their grade.")
    print("Type an area name to search, or 'quit' to exit.\n")

    while True:
        try:
            query = input("Area: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if query.lower() in ("quit", "q", "exit"):
            break
        if not query:
            continue

        result = pick_area(conn, query)
        if not result:
            continue

        area_id, area_name = result
        climb_type = prompt_climb_type()
        max_grade = prompt_max_grade() if climb_type == "boulder" else None
        show_hidden_gems(conn, area_id, area_name, climb_type, max_grade)


if __name__ == "__main__":
    main()
