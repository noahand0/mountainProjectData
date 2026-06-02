"""
Streamlit web app for the Climbing Hidden Gems finder.
Run with: streamlit run app.py
"""

from __future__ import annotations

import sqlite3

import pandas as pd
import pydeck as pdk
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

st.markdown("""
<style>
    [data-testid="stSidebar"] { min-width: 220px; max-width: 220px; }
</style>
""", unsafe_allow_html=True)


@st.cache_resource
def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(db.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


conn = get_connection()


def _compute_top_areas(conn: sqlite3.Connection, limit: int = 5) -> list[str]:
    areas = conn.execute(
        "SELECT area_id, parent_area_id, name FROM areas"
    ).fetchall()
    direct = dict(conn.execute(
        "SELECT area_id, COUNT(*) FROM routes GROUP BY area_id"
    ).fetchall())

    name_of = {r["area_id"]: r["name"] for r in areas}
    parent_of = {r["area_id"]: r["parent_area_id"] for r in areas}
    children: dict[int, list[int]] = {aid: [] for aid in name_of}
    for aid, pid in parent_of.items():
        if pid is not None and pid in children:
            children[pid].append(aid)

    # Iterative bottom-up: process leaves first, propagate counts to parents.
    totals = {aid: direct.get(aid, 0) for aid in name_of}
    remaining_children = {aid: len(children[aid]) for aid in name_of}
    queue = [aid for aid in name_of if remaining_children[aid] == 0]
    while queue:
        aid = queue.pop()
        pid = parent_of.get(aid)
        if pid is not None and pid in totals:
            totals[pid] += totals[aid]
            remaining_children[pid] -= 1
            if remaining_children[pid] == 0:
                queue.append(pid)

    top = sorted(name_of, key=lambda aid: totals[aid], reverse=True)[:limit]
    return [name_of[aid] for aid in top]


TOP_AREAS = _compute_top_areas(conn)


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    # Apply any quick-start selection from the previous run BEFORE the widget
    # renders — setting a widget key after instantiation raises an error.
    if "area_pending" in st.session_state:
        st.session_state["area_query"] = st.session_state["area_pending"]
        del st.session_state["area_pending"]

    area_query = st.text_input("Area", placeholder="e.g. Yosemite Valley",
                               key="area_query")

    # Quick-start buttons shown only when the search box is empty
    if not area_query:
        st.caption("Popular areas:")
        for name in TOP_AREAS:
            if st.button(name, use_container_width=True, key=f"qs_{name}"):
                st.session_state.area_pending = name
                st.rerun()

    area_id: int | None = None
    area_name: str | None = None

    if area_query:
        matches = find_areas(conn, area_query)
        if not matches:
            st.warning("No areas found.")
        else:
            options = {r["name"]: r["area_id"] for r in matches}
            selected_name = st.selectbox("Select", list(options))
            area_id = options[selected_name]
            area_name = selected_name

    climb_type = st.radio("Type", ["Boulders", "Routes"], horizontal=True)

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
        "lat": r["latitude"],
        "lon": r["longitude"],
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
m1, m2 = st.columns(2)
m1.metric("Ranked", len(rows))
m2.metric("Skipped (< 3 star votes)", skipped)

# ── Side-by-side: ranked list (left) + map (right) ───────────────────────────
list_col, map_col = st.columns([2, 3])

with list_col:
    st.caption("Click a row to highlight it on the map.")
    event = st.dataframe(
        df.drop(columns=["lat", "lon"]),
        column_config={
            "URL": st.column_config.LinkColumn("Link", display_text="View on MP"),
            "Score": st.column_config.NumberColumn(format="%.2f"),
            "Stars": st.column_config.NumberColumn(format="%.1f"),
        },
        use_container_width=True,
        height=600,
        on_select="rerun",
        selection_mode="single-row",
    )

with map_col:
    map_data = df[["Route", "Grade", "Stars", "Ticks", "Score", "lat", "lon"]].dropna(
        subset=["lat", "lon"]
    )

    if map_data.empty:
        st.caption("No coordinates available.")
    else:
        # Determine which row (0-based) is selected
        selected = event.selection.rows
        sel_idx = selected[0] if selected else None

        # Build a colour column: orange for selected, blue for everything else
        map_data = map_data.copy()
        map_data["Rank"] = map_data.index  # df.index is already 1-based
        map_data["color"] = map_data.apply(
            lambda row: [255, 100, 0, 255]
            if row.name - 1 == sel_idx   # df.index is 1-based; sel_idx is 0-based
            else [59, 130, 246, 180],
            axis=1,
        )
        map_data["radius"] = map_data.apply(
            lambda row: 120 if row.name - 1 == sel_idx else 60,
            axis=1,
        )

        layer = pdk.Layer(
            "ScatterplotLayer",
            data=map_data,
            get_position="[lon, lat]",
            get_color="color",
            get_radius="radius",
            pickable=True,
        )

        view = pdk.ViewState(
            latitude=map_data["lat"].mean(),
            longitude=map_data["lon"].mean(),
            zoom=11,
            pitch=0,
        )

        st.pydeck_chart(
            pdk.Deck(
                layers=[layer],
                initial_view_state=view,
                tooltip={"text": "#{Rank} {Route}\n{Grade}  ·  {Stars}★  ·  {Ticks} ticks"},
            ),
            use_container_width=True,
            height=600,
        )
