from __future__ import annotations

import time
import requests
from datetime import datetime, timezone

CRAWL_DELAY = 0.5  # seconds between live fetches; well under robots.txt's 60s but polite

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
        self._pw = None
        self._browser = None

    # ------------------------------------------------------------------
    # Public fetch methods
    # ------------------------------------------------------------------

    def fetch(self, url: str, force: bool = False) -> str:
        """Return HTML for url via requests (no JS). Serves from cache unless force=True."""
        if not force:
            cached = self._from_cache(url)
            if cached is not None:
                return cached

        self._throttle()
        resp = self.session.get(url, timeout=20)
        resp.raise_for_status()
        html = resp.text
        self._last_fetch = time.time()
        self._cache(url, html)
        return html

    def fetch_js(self, url: str, force: bool = False) -> str:
        """Return HTML for url via headless Chromium (JS executed). Serves from cache unless force=True.

        Images, fonts, media, and analytics are blocked to reduce bandwidth on the
        remote server. networkidle already makes each load take several seconds, so
        no additional throttle delay is applied.
        """
        if not force:
            cached = self._from_cache(url)
            if cached is not None:
                return cached

        page = self._get_browser().new_page()
        try:
            page.route(
                "**/*",
                lambda route: route.abort()
                if route.request.resource_type in {"image", "media", "font", "stylesheet"}
                else route.continue_(),
            )
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            try:
                page.wait_for_selector("tr[id^='ticks.']", timeout=10000)
            except Exception:
                pass
            html = page.content()
        except Exception:
            html = page.content()
        finally:
            page.close()

        self._cache(url, html)
        return html

    def close(self) -> None:
        """Shut down the Playwright browser if it was started."""
        if self._browser:
            self._browser.close()
            self._browser = None
        if self._pw:
            self._pw.stop()
            self._pw = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_fetch
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)

    def _get_browser(self):
        if self._browser is None:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=True)
        return self._browser

    def _from_cache(self, url: str) -> str | None:
        row = self.conn.execute(
            "SELECT html FROM raw_pages WHERE url = ?", (url,)
        ).fetchone()
        return row["html"] if row else None

    def _cache(self, url: str, html: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO raw_pages (url, html, fetched_at) VALUES (?, ?, ?)",
            (url, html, datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()
