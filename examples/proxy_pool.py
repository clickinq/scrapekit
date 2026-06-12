"""Scrape through a proxy pool with health checks and automatic rotation.

Run with::

    python examples/proxy_pool.py

Replace the placeholder proxy URLs with your own. scrapekit will:

* health-check every proxy concurrently and drop the dead ones,
* rotate proxies per request (round-robin here),
* cool down a proxy after repeated failures and retry the request through a
  fresh one automatically.
"""

from __future__ import annotations

import logging

from scrapekit import Config, NoHealthyProxiesError, ProxyPool, Scraper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-18s %(levelname)-7s %(message)s",
)

# Replace these with real proxies. Format: scheme://[user:pass@]host:port
PROXIES = [
    "http://proxy-a.example.com:8080",
    "http://proxy-b.example.com:8080",
    "http://proxy-c.example.com:8080",
]


def main() -> None:
    config = Config(
        proxy_strategy="round_robin",
        proxy_max_failures=2,   # cool down after 2 consecutive failures
        proxy_cooldown=30.0,    # ...for 30 seconds
        min_delay=1.0,
    )
    pool = ProxyPool.from_config(PROXIES, config)

    # Concurrently probe every proxy; unreachable ones are marked dead.
    healthy = pool.health_check("https://httpbin.org/ip", timeout=10.0)
    print(f"Healthy proxies after check: {healthy}/{len(pool)}")
    if healthy == 0:
        print("No usable proxies — update PROXIES with real values.")
        return

    with Scraper(config, proxy_pool=pool) as scraper:
        for i in range(5):
            try:
                resp = scraper.get("https://httpbin.org/ip")
                print(f"request {i}: {resp.status_code} via {resp.json().get('origin')}")
            except NoHealthyProxiesError:
                print(f"request {i}: pool exhausted — all proxies are down")
                break


if __name__ == "__main__":
    main()
