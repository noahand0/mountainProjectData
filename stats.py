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


# ---------------------------------------------------------------------------
# Grade parsing
# ---------------------------------------------------------------------------

def grade_to_num(grade: str | None) -> float | None:
    """Convert a V-scale grade string to a numeric value for sorting/filtering."""
    if not grade:
        return None
    g = grade.strip()

    if g.lower() in ("v-easy", "vb"):
        return -1.0

    # Ranges like V6-7 or V7-8 → average
    m = re.match(r"^[Vv](\d+)-(\d+)$", g)
    if m:
        return (int(m.group(1)) + int(m.group(2))) / 2.0

    # Standard V-grades with optional +/-
    m = re.match(r"^[Vv](\d+)([+-]?)$", g)
    if m:
        base = int(m.group(1))
        mod = {"+": 0.3, "-": -0.3}.get(m.group(2), 0.0)
        return base + mod

    return None  # YDS or unparseable — excluded from V-scale filtering


def parse_grade_input(text: str) -> float | None:
    """Parse a user-supplied grade string. Returns None if unparseable."""
    return grade_to_num(text.strip())


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _grade_bucket(num: float) -> int:
    """Round a numeric grade to an integer bucket for grouping."""
    return round(num)


def compute_percentiles(routes: list[sqlite3.Row]) -> dict[int, float]:
    """
    For each route, compute its tick-count percentile among routes in the
    same grade bucket (integer-rounded V-scale). Routes with no parseable
    grade share a single bucket. Returns {route_id: percentile 0.0–1.0}.
    """
    buckets: dict[int | None, list[sqlite3.Row]] = defaultdict(list)
    for r in routes:
        num = grade_to_num(r["grade_vscale"] or r["grade_yds"] or r["grade_raw"])
        bucket = _grade_bucket(num) if num is not None else None
        buckets[bucket].append(r)

    percentiles: dict[int, float] = {}
    for bucket_routes in buckets.values():
        sorted_routes = sorted(bucket_routes, key=lambda r: r["tick_count"] or 0)
        n = len(sorted_routes)
        for i, r in enumerate(sorted_routes):
            # 0.0 = least popular in grade, 1.0 = most popular in grade
            percentiles[r["route_id"]] = i / (n - 1) if n > 1 else 0.5

    return percentiles


def hidden_gem_score(route: sqlite3.Row, popularity_percentile: float) -> float | None:
    """
    Score = avg_stars × (1 − popularity_percentile_within_grade).
    Routes with fewer than MIN_STAR_VOTES are excluded (return None).
    """
    if (route["star_votes"] or 0) < MIN_STAR_VOTES:
        return None
    return (route["avg_stars"] or 0) * (1.0 - popularity_percentile)


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
    SELECT r.route_id, r.name, r.grade_vscale, r.grade_yds, r.grade_raw,
           r.avg_stars, r.star_votes, r.tick_count, r.todo_count,
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
    max_grade: float | None,
) -> None:
    all_routes = get_routes_in_area(conn, area_id)
    if not all_routes:
        print("  No routes found.\n")
        return

    # Apply grade filter before percentile calculation so percentiles reflect
    # only the grades the user cares about.
    if max_grade is not None:
        filtered = [
            r for r in all_routes
            if grade_to_num(r["grade_vscale"] or r["grade_yds"] or r["grade_raw"]) is not None
            and grade_to_num(r["grade_vscale"] or r["grade_yds"] or r["grade_raw"]) <= max_grade
        ]
    else:
        filtered = list(all_routes)

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

    grade_filter_note = f"  ≤ {max_grade:.0f} (V-scale)" if max_grade is not None else "  all grades"
    print(f"\nHidden gems in: {area_name}")
    print(f"  Grade filter:{grade_filter_note}")
    print(f"  {len(scored)} routes ranked, {skipped} skipped (fewer than {MIN_STAR_VOTES} star votes)\n")
    print(f"{'#':<4} {'Score':<7} {'Stars':<7} {'Ticks':<7} {'Grade':<7} {'Area':<28} Route")
    print("-" * 92)

    for rank, (score, r) in enumerate(scored, 1):
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


def prompt_max_grade() -> float | None:
    raw = input("  Max grade (e.g. V6, or Enter for all grades): ").strip()
    if not raw:
        return None
    num = parse_grade_input(raw)
    if num is None:
        print(f"  Couldn't parse '{raw}' — showing all grades.\n")
    return num


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    conn = db.get_conn()
    print("Climbing Stats — Hidden Gem Finder")
    print("Ranks routes by star rating relative to how well-known they are for their grade.")
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
        max_grade = prompt_max_grade()
        show_hidden_gems(conn, area_id, area_name, max_grade)


if __name__ == "__main__":
    main()
