# scrapekit

**Resilient, well-behaved web scraping for Python — retries, rate limiting, proxy rotation, and header rotation around `requests`, behind one clean API.**

[![CI](https://github.com/your-username/scrapekit/actions/workflows/ci.yml/badge.svg)](https://github.com/your-username/scrapekit/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/downloads/)
[![Code style: ruff](https://img.shields.io/badge/lint-ruff-261230.svg)](https://github.com/astral-sh/ruff)

---

## What it is & why

Real-world scraping breaks for boring reasons: a flaky connection, a `503` under
load, a `429` that wants you to slow down, a dead proxy, a server that frowns at
a stale `User-Agent`. Handling all of that by hand turns a five-line script into
a tangle of `try`/`except`, `time.sleep`, and copy-pasted retry loops.

**scrapekit** is the infrastructure layer that makes a scraper robust and polite,
so you can focus on the data. It wraps [`requests`](https://requests.readthedocs.io/)
and transparently adds:

- 🔁 **Automatic retries** with exponential backoff + jitter, honouring `Retry-After`.
- 🐢 **Per-domain rate limiting** (fixed delay *or* token bucket) — be a good citizen by default.
- 🔀 **Proxy pool** with concurrent health checks, round-robin/random rotation, cooldowns, and auto-removal of dead proxies.
- 🎭 **User-agent & header rotation** from a built-in pool of realistic strings.
- 🪝 **Hooks** for logging, metrics, caching, and custom parsing.
- 🧩 A clean, `requests`-style API (`get`/`post`/...) with a `Config` dataclass and full type hints.

> scrapekit is **general-purpose data-collection infrastructure**. It deliberately
> does **not** include anything to target or defeat a specific site's bot
> protection or to solve CAPTCHAs. Please scrape responsibly: respect
> `robots.txt`, terms of service, and applicable laws.

## Install

```bash
pip install scrapekit
```

Or for local development:

```bash
git clone https://github.com/your-username/scrapekit
cd scrapekit
pip install -e ".[dev]"
```

Requires Python 3.9+ and `requests`.

## Quickstart

```python
from scrapekit import Scraper

with Scraper() as scraper:
    resp = scraper.get("https://example.com")
    resp.raise_for_status()
    print(resp.text)
```

That's it — the defaults already give you 3 retries with jittered backoff, a
1-second-per-domain rate limit, and rotating user-agents. Everything is
overridable via `Config`.

## Configuration

Every knob lives on a single `Config` dataclass with sensible defaults:

```python
from scrapekit import Config, Scraper

config = Config(
    timeout=30.0,
    # retries
    max_retries=5,
    backoff_factor=0.5,
    backoff_max=60.0,
    retry_statuses=(429, 500, 502, 503, 504),
    respect_retry_after=True,
    # rate limiting (per domain)
    min_delay=1.0,              # or set requests_per_second=... for a token bucket
    # headers
    rotate_user_agent=True,
    randomize_headers=False,
    # proxies
    proxy_strategy="round_robin",
    proxy_max_failures=3,
    proxy_cooldown=60.0,
)

scraper = Scraper(config)
```

## Usage examples

### 1. Basic scrape with retries

```python
from scrapekit import Config, Scraper, RetryExhaustedError

with Scraper(Config(max_retries=4)) as scraper:
    try:
        resp = scraper.get("https://httpbin.org/status/503")
    except RetryExhaustedError as exc:
        print(f"gave up after {exc.attempts} attempts; last status "
              f"{exc.last_response.status_code}")
```

### 2. Scrape through a proxy pool with health checks

```python
from scrapekit import Config, ProxyPool, Scraper

pool = ProxyPool(
    ["http://proxy-a:8080", "http://proxy-b:8080", "http://proxy-c:8080"],
    strategy="round_robin",
    max_failures=2,      # cool a proxy down after 2 consecutive failures
    cooldown=30.0,
)

# Concurrently probe every proxy and drop the unreachable ones.
print("healthy:", pool.health_check("https://httpbin.org/ip"))

with Scraper(Config(), proxy_pool=pool) as scraper:
    # A fresh proxy is used per request; on a proxy-level failure scrapekit
    # marks it and retries through the next one automatically.
    resp = scraper.get("https://httpbin.org/ip")
    print(resp.json())
```

### 3. Per-domain rate limiting

```python
from scrapekit import Config, Scraper

# Token bucket: ~2 requests/second per domain, burst of 2.
config = Config(requests_per_second=2.0, rate_limit_burst=2, min_delay=None)

with Scraper(config) as scraper:
    for i in range(6):
        scraper.get("https://httpbin.org/get", params={"i": i})
        # requests to the same host are paced; different hosts are independent
```

### Bonus: hooks for logging, metrics, and caching

```python
from scrapekit import HookManager, Scraper

hooks = HookManager()
cache: dict[str, object] = {}

@hooks.pre_request
def serve_from_cache(ctx):
    # Returning a Response short-circuits the network call.
    return cache.get(ctx.url)

@hooks.post_response
def store_in_cache(resp, ctx):
    if resp.status_code == 200:
        cache[ctx.url] = resp

with Scraper(hooks=hooks) as scraper:
    scraper.get("https://example.com")  # miss -> fetched and cached
    scraper.get("https://example.com")  # hit  -> served from cache
```

More runnable scripts live in [`examples/`](examples/).

## Feature reference

| Area | Highlights |
| --- | --- |
| **Retries** (`retry.py`) | Configurable max retries; exponential backoff with equal-jitter; retry on connection errors, timeouts, and configurable status codes; honours `Retry-After`; raises `RetryExhaustedError` when exhausted. |
| **Proxies** (`proxies.py`) | `ProxyPool` with round-robin/random rotation; concurrent health checks; per-proxy failure tracking; cooldown bans; auto-removal of persistently dead proxies. |
| **Rate limiting** (`ratelimit.py`) | Per-domain (or global) limiting; fixed `min_delay` or token-bucket `requests_per_second`; thread-safe. |
| **Headers** (`headers.py`) | `UserAgentRotator` with a built-in realistic pool; optional header randomization (`Accept-Language`, etc.). |
| **Hooks** (`hooks.py`) | Register pre-request and post-response hooks; pre-request hooks may short-circuit (caching). |
| **Config** (`config.py`) | Single `Config` dataclass; validated; sensible defaults. |
| **Exceptions** (`exceptions.py`) | `ScrapeKitError` base, plus `RetryExhaustedError`, `NoHealthyProxiesError`, `ConfigurationError`, ... |

## Logging

scrapekit uses the standard `logging` module under the `scrapekit` logger (with
per-module children like `scrapekit.client`). It never prints. Enable diagnostics
with:

```python
import logging
logging.basicConfig(level=logging.INFO)
```

## Development

```bash
pip install -e ".[dev]"
pytest            # run the test suite (no network access required)
ruff check .      # lint
mypy              # type-check
```

The test suite mocks all HTTP and uses an injectable clock, so it is fast and
deterministic — no real network calls.

## License

[MIT](LICENSE) © scrapekit contributors
