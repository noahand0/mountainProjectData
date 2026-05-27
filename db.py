from __future__ import annotations

import sqlite3
from pathlib import Path
from datetime import datetime, timezone

DB_PATH = Path("climbing.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS areas (
    area_id        INTEGER PRIMARY KEY,
    parent_area_id INTEGER REFERENCES areas(area_id),
    name           TEXT,
    url            TEXT,
    latitude       REAL,
    longitude      REAL,
    scraped_at     TIMESTAMP
);

CREATE TABLE IF NOT EXISTS routes (
    route_id     INTEGER PRIMARY KEY,
    area_id      INTEGER REFERENCES areas(area_id),
    name         TEXT,
    url          TEXT,
    type         TEXT,
    grade_yds    TEXT,
    grade_vscale TEXT,
    grade_raw    TEXT,
    length_ft    INTEGER,
    pitches      INTEGER,
    fa           TEXT,
    description  TEXT,
    protection   TEXT,
    avg_stars    REAL,
    star_votes   INTEGER,
    tick_count   INTEGER,
    todo_count   INTEGER,
    scraped_at   TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ticks (
    tick_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    route_id     INTEGER REFERENCES routes(route_id),
    author       TEXT,
    date_climbed TEXT,
    tick_type    TEXT,
    stars        INTEGER,
    comment      TEXT,
    scraped_at   TIMESTAMP
);

CREATE TABLE IF NOT EXISTS suggested_ratings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    route_id   INTEGER REFERENCES routes(route_id),
    author     TEXT,
    grade      TEXT,
    scraped_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS route_comments (
    comment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    route_id   INTEGER REFERENCES routes(route_id),
    author     TEXT,
    posted_at  TEXT,
    text       TEXT,
    scraped_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS raw_pages (
    url        TEXT PRIMARY KEY,
    html       TEXT,
    fetched_at TIMESTAMP
);
"""


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_area(conn: sqlite3.Connection, area: dict) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO areas
            (area_id, parent_area_id, name, url, latitude, longitude, scraped_at)
        VALUES
            (:area_id, :parent_area_id, :name, :url, :latitude, :longitude, :scraped_at)
        """,
        area,
    )
    conn.commit()


def upsert_route(conn: sqlite3.Connection, route: dict) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO routes
            (route_id, area_id, name, url, type, grade_yds, grade_vscale, grade_raw,
             length_ft, pitches, fa, description, protection,
             avg_stars, star_votes, tick_count, todo_count, scraped_at)
        VALUES
            (:route_id, :area_id, :name, :url, :type, :grade_yds, :grade_vscale, :grade_raw,
             :length_ft, :pitches, :fa, :description, :protection,
             :avg_stars, :star_votes, :tick_count, :todo_count, :scraped_at)
        """,
        route,
    )
    conn.commit()


def replace_ticks(conn: sqlite3.Connection, ticks: list[dict]) -> None:
    if not ticks:
        return
    conn.execute("DELETE FROM ticks WHERE route_id = ?", (ticks[0]["route_id"],))
    conn.executemany(
        """
        INSERT INTO ticks (route_id, author, date_climbed, tick_type, stars, comment, scraped_at)
        VALUES (:route_id, :author, :date_climbed, :tick_type, :stars, :comment, :scraped_at)
        """,
        ticks,
    )
    conn.commit()


def replace_suggested_ratings(conn: sqlite3.Connection, ratings: list[dict]) -> None:
    if not ratings:
        return
    conn.execute("DELETE FROM suggested_ratings WHERE route_id = ?", (ratings[0]["route_id"],))
    conn.executemany(
        """
        INSERT INTO suggested_ratings (route_id, author, grade, scraped_at)
        VALUES (:route_id, :author, :grade, :scraped_at)
        """,
        ratings,
    )
    conn.commit()


def update_route_counts(conn: sqlite3.Connection, route_id: int, tick_count: int, todo_count: int) -> None:
    conn.execute(
        "UPDATE routes SET tick_count = ?, todo_count = ? WHERE route_id = ?",
        (tick_count, todo_count, route_id),
    )
    conn.commit()


def replace_route_comments(conn: sqlite3.Connection, comments: list[dict]) -> None:
    if not comments:
        return
    conn.execute("DELETE FROM route_comments WHERE route_id = ?", (comments[0]["route_id"],))
    conn.executemany(
        """
        INSERT INTO route_comments (route_id, author, posted_at, text, scraped_at)
        VALUES (:route_id, :author, :posted_at, :text, :scraped_at)
        """,
        comments,
    )
    conn.commit()


def area_scraped(conn: sqlite3.Connection, area_id: int) -> bool:
    row = conn.execute(
        "SELECT scraped_at FROM areas WHERE area_id = ?", (area_id,)
    ).fetchone()
    return row is not None and row["scraped_at"] is not None


def route_scraped(conn: sqlite3.Connection, route_id: int) -> bool:
    row = conn.execute(
        "SELECT scraped_at FROM routes WHERE route_id = ?", (route_id,)
    ).fetchone()
    return row is not None and row["scraped_at"] is not None
