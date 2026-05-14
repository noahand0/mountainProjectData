from __future__ import annotations

import time
import requests
from datetime import datetime, timezone

CRAWL_DELAY = 2.0  # seconds between live fetches; well under robots.txt's 60s but polite

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36 CS153-research/1.0"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


class Fetcher:
    def __init__(self, conn, delay: float = CRAWL_DELAY):
        self.conn = conn
        self.delay = delay
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self._last_fetch: float = 0.0

    def fetch(self, url: str, force: bool = False) -> str:
        """Return HTML for url. Serves from cache unless force=True."""
        if not force:
            row = self.conn.execute(
                "SELECT html FROM raw_pages WHERE url = ?", (url,)
            ).fetchone()
            if row:
                return row["html"]

        elapsed = time.time() - self._last_fetch
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)

        resp = self.session.get(url, timeout=20)
        resp.raise_for_status()
        html = resp.text
        self._last_fetch = time.time()

        self.conn.execute(
            "INSERT OR REPLACE INTO raw_pages (url, html, fetched_at) VALUES (?, ?, ?)",
            (url, html, datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()

        return html
