"""Per-domain rate limiting plus a metrics hook.

Run with::

    python examples/rate_limited.py

Demonstrates two things:

1. A token-bucket rate limit (``requests_per_second``) applied *per domain*, so
   requests to the same host are paced while different hosts run independently.
2. A post-response hook that records simple metrics without touching the
   scraping logic.
"""

from __future__ import annotations

import logging
import time
from collections import Counter

from scrapekit import Config, HookManager, Scraper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-18s %(levelname)-7s %(message)s",
)

status_counts: Counter = Counter()


def build_hooks() -> HookManager:
    hooks = HookManager()

    @hooks.post_response
    def count_statuses(response, context):
        status_counts[response.status_code] += 1
        # Returning None leaves the response unchanged.
        return None

    return hooks


def main() -> None:
    # Allow ~2 requests/second to each domain, with a small burst of 2.
    config = Config(requests_per_second=2.0, rate_limit_burst=2, min_delay=None)

    with Scraper(config, hooks=build_hooks()) as scraper:
        start = time.monotonic()
        for i in range(6):
            resp = scraper.get("https://httpbin.org/get", params={"i": i})
            print(f"request {i}: {resp.status_code}  (t+{time.monotonic() - start:0.2f}s)")

    print("status counts:", dict(status_counts))


if __name__ == "__main__":
    main()
