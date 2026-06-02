"""
Streamlit web app for the Climbing Hidden Gems finder.
Run with: streamlit run app.py
"""

from __future__ import annotations

import sqlite3

import pandas as pd
import streamlit as st

import db
from stats import (
    MAX_RESULTS,
    compute_percentiles,
    find_areas,
    get_routes_in_area,
    grade_to_num,
    hidden_gem_score,
    parse_vscale_input,
)

st.set_page_config(page_title="Climbing Hidden Gems", layout="wide")


@st.cache_resource
def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(db.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


conn = get_connection()


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Search")

    area_query = st.text_input("Area name", placeholder="e.g. Yosemite Valley")

    area_id: int | None = None
    area_name: str | None = None

    if area_query:
        matches = find_areas(conn, area_query)
        if not matches:
            st.warning("No areas found.")
        else:
            options = {r["name"]: r["area_id"] for r in matches}
            selected_name = st.selectbox("Select area", list(options))
            area_id = options[selected_name]
            area_name = selected_name

    st.divider()
    st.header("Filters")

    climb_type = st.radio("Climb type", ["Boulders", "Routes"], horizontal=True)

    min_grade: float | None = None
    max_grade: float | None = None
    if climb_type == "Boulders":
        col_min, col_max = st.columns(2)
        min_input = col_min.text_input("Min grade", placeholder="e.g. V4")
        max_input = col_max.text_input("Max grade", placeholder="e.g. V8")
        if min_input:
            min_grade = parse_vscale_input(min_input)
            if min_grade is None:
                st.warning(f"Could not parse '{min_input}' as a V-scale grade.")
        if max_input:
            max_grade = parse_vscale_input(max_input)
            if max_grade is None:
                st.warning(f"Could not parse '{max_input}' as a V-scale grade.")


# ── Main content ──────────────────────────────────────────────────────────────
st.title("Climbing Hidden Gems")
st.caption(
    "Ranks climbs by star rating relative to how well-known they are for their grade. "
    "High score = quality route that hasn't been discovered yet."
)

if area_id is None:
    st.info("Search for an area in the sidebar to get started.")
    st.stop()

# Load and filter
all_routes = get_routes_in_area(conn, area_id)

if climb_type == "Boulders":
    typed = [r for r in all_routes if (r["type"] or "").lower() == "boulder"]
else:
    typed = [r for r in all_routes if (r["type"] or "").lower() != "boulder"]

if min_grade is not None or max_grade is not None:
    filtered = []
    for r in typed:
        n = grade_to_num(r["grade_vscale"] or r["grade_yds"] or r["grade_raw"])
        if n is None:
            continue
        if min_grade is not None and n < min_grade:
            continue
        if max_grade is not None and n > max_grade:
            continue
        filtered.append(r)
else:
    filtered = typed

if not filtered:
    st.warning("No climbs match those filters.")
    st.stop()

# Score
percentiles = compute_percentiles(filtered)
rows = []
skipped = 0
for r in filtered:
    pct = percentiles.get(r["route_id"], 0.5)
    score = hidden_gem_score(r, pct)
    if score is None:
        skipped += 1
        continue
    rows.append({
        "Score": round(score, 2),
        "Route": r["name"],
        "URL": r["url"] or "",
        "Grade": r["grade_vscale"] or r["grade_yds"] or r["grade_raw"] or "?",
        "Stars": r["avg_stars"],
        "Votes": r["star_votes"],
        "Ticks": r["tick_count"],
        "Area": r["area_name"],
    })

if not rows:
    st.warning("No climbs have enough star votes to rank.")
    st.stop()

df = (
    pd.DataFrame(rows)
    .sort_values("Score", ascending=False)
    .head(MAX_RESULTS)
    .reset_index(drop=True)
)
df.index += 1  # 1-based rank

# Summary line
type_label = "Boulders" if climb_type == "Boulders" else "Routes"
if min_grade is not None and max_grade is not None:
    grade_note = f" V{min_grade:.0f}–V{max_grade:.0f}"
elif min_grade is not None:
    grade_note = f" ≥ V{min_grade:.0f}"
elif max_grade is not None:
    grade_note = f" ≤ V{max_grade:.0f}"
else:
    grade_note = ""
st.subheader(f"{area_name} — {type_label}{grade_note}")
col1, col2 = st.columns(2)
col1.metric("Ranked", len(rows))
col2.metric("Skipped (< 3 star votes)", skipped)

# Results table
st.dataframe(
    df,
    column_config={
        "URL": st.column_config.LinkColumn("Link", display_text="View on MP"),
        "Score": st.column_config.NumberColumn(format="%.2f"),
        "Stars": st.column_config.NumberColumn(format="%.1f"),
    },
    use_container_width=True,
)
