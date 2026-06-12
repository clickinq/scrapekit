"""scrapekit — a resilient, well-behaved toolkit for web scraping.

scrapekit wraps :mod:`requests` with the boring-but-essential infrastructure that
turns a fragile script into a robust scraper: automatic retries with jittered
backoff, per-domain rate limiting, proxy rotation with health checks, and
user-agent rotation — all behind a clean, ``requests``-style API.

Example:
    >>> from scrapekit import Scraper
    >>> with Scraper() as scraper:
    ...     resp = scraper.get("https://example.com")
"""

from __future__ import annotations

import logging

from .client import Scraper
from .config import DEFAULT_RETRY_STATUSES, Config
from .exceptions import (
    ConfigurationError,
    HookError,
    NoHealthyProxiesError,
    ProxyError,
    RetryExhaustedError,
    ScrapeKitError,
)
from .headers import DEFAULT_USER_AGENTS, UserAgentRotator, random_headers
from .hooks import HookManager, RequestContext
from .proxies import Proxy, ProxyPool
from .ratelimit import RateLimiter, TokenBucket
from .retry import RetryPolicy, parse_retry_after

__version__ = "0.1.0"

__all__ = [
    "__version__",
    # Client & config
    "Scraper",
    "Config",
    "DEFAULT_RETRY_STATUSES",
    # Retry
    "RetryPolicy",
    "parse_retry_after",
    # Proxies
    "Proxy",
    "ProxyPool",
    # Rate limiting
    "RateLimiter",
    "TokenBucket",
    # Headers
    "UserAgentRotator",
    "random_headers",
    "DEFAULT_USER_AGENTS",
    # Hooks
    "HookManager",
    "RequestContext",
    # Exceptions
    "ScrapeKitError",
    "ConfigurationError",
    "RetryExhaustedError",
    "ProxyError",
    "NoHealthyProxiesError",
    "HookError",
]

# Library best practice: attach a no-op handler so importing scrapekit never
# emits "No handlers could be found" warnings. Applications configure logging.
logging.getLogger("scrapekit").addHandler(logging.NullHandler())
