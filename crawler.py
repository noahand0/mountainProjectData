"""
Orchestrates a breadth-first crawl of a Mountain Project area.

Usage (programmatic):
    crawler = Crawler(conn)
    crawler.crawl_area("https://www.mountainproject.com/area/105799429/red-river-gorge")
"""

from collections import deque

import db
import parser
from fetcher import Fetcher

MP_STATS_BASE = "https://www.mountainproject.com/route/stats/"


class Crawler:
    def __init__(self, conn, delay: float = 2.0, skip_ticks: bool = False, deep_routes: bool = False):
        self.conn = conn
        self.fetcher = Fetcher(conn, delay=delay)
        self.skip_ticks = skip_ticks
        self.deep_routes = deep_routes  # fetch individual route pages for full detail

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def crawl_area(self, root_url: str) -> None:
        """
        Crawl root_url and all descendant areas, then all their routes.
        Already-scraped areas and routes are skipped (idempotent).
        """
        area_queue: deque[tuple[str, int | None]] = deque()
        area_queue.append((root_url, None))
        visited_areas: set[int] = set()

        while area_queue:
            area_url, parent_id = area_queue.popleft()
            area_id = parser._extract_id(area_url)
            if area_id is None or area_id in visited_areas:
                continue
            visited_areas.add(area_id)

            print(f"[area] {area_url}")
            try:
                html = self.fetcher.fetch(area_url)
            except Exception as e:
                print(f"  ERROR fetching area: {e}")
                continue

            area_dict, child_area_urls, route_urls = parser.parse_area(html, area_url)
            area_dict["parent_area_id"] = parent_id
            db.upsert_area(self.conn, area_dict)

            for child_url in child_area_urls:
                child_id = parser._extract_id(child_url)
                if child_id and child_id not in visited_areas:
                    area_queue.append((child_url, area_id))

            if self.deep_routes:
                for route_url in route_urls:
                    self._crawl_route(route_url, area_id)
            else:
                # Extract route data directly from the already-fetched area page
                routes = parser.parse_routes_from_area(html, area_id)
                for route_dict in routes:
                    db.upsert_route(self.conn, route_dict)
                if routes:
                    print(f"  extracted {len(routes)} routes from area page")

    # ------------------------------------------------------------------
    # Route crawl
    # ------------------------------------------------------------------

    def _crawl_route(self, route_url: str, area_id: int) -> None:
        route_id = parser._extract_id(route_url)
        if route_id is None:
            return

        if db.route_scraped(self.conn, route_id):
            print(f"  [route] skip (cached): {route_url}")
            return

        print(f"  [route] {route_url}")
        try:
            html = self.fetcher.fetch(route_url)
        except Exception as e:
            print(f"    ERROR fetching route: {e}")
            return

        route_dict = parser.parse_route(html, route_url)
        route_dict["area_id"] = area_id
        db.upsert_route(self.conn, route_dict)

        # Route-level comments (beta notes)
        comments = parser.parse_route_comments(html, route_id)
        if comments:
            db.replace_route_comments(self.conn, comments)

        if not self.skip_ticks:
            self._crawl_ticks(route_id)

    # ------------------------------------------------------------------
    # Ticks crawl  (/route/stats/{id}?page=N)
    # ------------------------------------------------------------------

    def _crawl_ticks(self, route_id: int) -> None:
        all_ticks = []
        page = 1

        while True:
            url = f"{MP_STATS_BASE}{route_id}"
            if page > 1:
                url += f"?page={page}"

            print(f"    [ticks] page {page}: {url}")
            try:
                html = self.fetcher.fetch(url)
            except Exception as e:
                print(f"      ERROR fetching ticks: {e}")
                break

            ticks = parser.parse_ticks(html, route_id)
            if not ticks:
                break

            all_ticks.extend(ticks)

            # Stop if this page looks like the last one (MP typically shows
            # a "next" link when there are more pages)
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "lxml")
            next_link = soup.find("a", string=re.compile(r"next", re.I)) or \
                        soup.find("a", rel="next")
            if not next_link:
                break

            page += 1

        if all_ticks:
            db.replace_ticks(self.conn, all_ticks)
            print(f"    saved {len(all_ticks)} ticks")


import re  # noqa: E402  (needed for _crawl_ticks; placed here to avoid circular import)
