"""Basic scrape: defaults give you retries, backoff, and polite rate limiting.

Run with::

    python examples/basic_scrape.py

This uses https://httpbin.org as a friendly test target. The ``/status/503``
endpoint forces a server error so you can watch scrapekit retry with backoff
before finally raising :class:`RetryExhaustedError`.
"""

from __future__ import annotations

import logging

from scrapekit import Config, RetryExhaustedError, Scraper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-18s %(levelname)-7s %(message)s",
)


def main() -> None:
    # Sensible defaults: 3 retries, jittered backoff, 1s/domain rate limit,
    # rotating user-agents. Override only what you need.
    config = Config(max_retries=3, min_delay=0.5)

    with Scraper(config) as scraper:
        # A normal request.
        resp = scraper.get("https://httpbin.org/get", params={"q": "scrapekit"})
        print("GET /get          ->", resp.status_code)

        # The server reports our (rotated) headers back to us.
        resp = scraper.get("https://httpbin.org/headers")
        print("User-Agent sent   ->", resp.json()["headers"].get("User-Agent"))

        # This endpoint always fails; scrapekit retries, then gives up cleanly.
        try:
            scraper.get("https://httpbin.org/status/503")
        except RetryExhaustedError as exc:
            print(f"GET /status/503   -> gave up after {exc.attempts} attempts")


if __name__ == "__main__":
    main()
