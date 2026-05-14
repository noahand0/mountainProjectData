"""
Usage:
    python main.py <area-url> [--delay SECONDS] [--skip-ticks]

Examples:
    python main.py https://www.mountainproject.com/area/105799429/red-river-gorge
    python main.py https://www.mountainproject.com/area/105799429/red-river-gorge --delay 3
    python main.py https://www.mountainproject.com/area/105799429/red-river-gorge --skip-ticks
"""

import argparse
import sys

import db
from crawler import Crawler


def main() -> None:
    p = argparse.ArgumentParser(description="Scrape a Mountain Project area into climbing.db")
    p.add_argument("url", help="URL of the top-level area to crawl")
    p.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="Seconds between HTTP requests (default: 2.0)",
    )
    p.add_argument(
        "--skip-ticks",
        action="store_true",
        help="Skip fetching tick/stats pages (faster, less data)",
    )
    args = p.parse_args()

    conn = db.get_conn()
    db.init_db(conn)

    print(f"Starting crawl: {args.url}")
    print(f"Delay: {args.delay}s | Skip ticks: {args.skip_ticks}")
    print("Data will be written to climbing.db\n")

    crawler = Crawler(conn, delay=args.delay, skip_ticks=args.skip_ticks)
    try:
        crawler.crawl_area(args.url)
    except KeyboardInterrupt:
        print("\nInterrupted. Progress saved to climbing.db.")
        sys.exit(0)

    # Quick summary
    areas = conn.execute("SELECT COUNT(*) FROM areas").fetchone()[0]
    routes = conn.execute("SELECT COUNT(*) FROM routes").fetchone()[0]
    ticks = conn.execute("SELECT COUNT(*) FROM ticks").fetchone()[0]
    print(f"\nDone. areas={areas}  routes={routes}  ticks={ticks}")


if __name__ == "__main__":
    main()
