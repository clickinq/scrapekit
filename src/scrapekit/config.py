"""Configuration for :class:`~scrapekit.client.Scraper`.

Everything that controls scrapekit's behaviour lives on a single
:class:`Config` dataclass. Every field has a sensible, good-citizen default, so
the common case is simply ``Scraper()``. Override only what you need::

    from scrapekit import Config, Scraper

    config = Config(max_retries=5, min_delay=2.0, rotate_user_agent=True)
    scraper = Scraper(config)
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field

from .exceptions import ConfigurationError

#: Status codes retried by default. These are transient/server-side errors and
#: rate-limit responses, all of which are reasonable to retry.
DEFAULT_RETRY_STATUSES: tuple[int, ...] = (429, 500, 502, 503, 504)

#: Valid proxy rotation strategies.
PROXY_STRATEGIES = frozenset({"round_robin", "random"})


@dataclass
class Config:
    """All tunable settings for a :class:`~scrapekit.client.Scraper`.

    Attributes:
        timeout: Default per-request timeout in seconds. Overridable per call.
        max_retries: Maximum number of *retries* after the initial attempt. A
            value of ``3`` means up to four total attempts.
        backoff_factor: Base multiplier for exponential backoff. The delay for
            retry ``n`` (0-indexed) is ``backoff_factor * 2 ** n``.
        backoff_max: Upper bound, in seconds, on any single backoff delay.
        backoff_jitter: If ``True``, apply randomised "equal jitter" to backoff
            delays to avoid thundering-herd retries.
        retry_statuses: HTTP status codes that should trigger a retry.
        respect_retry_after: If ``True``, honour a ``Retry-After`` response
            header (in preference to computed backoff) when present.
        retry_after_max: Upper bound, in seconds, on a honoured ``Retry-After``
            value. Protects against absurdly long server-supplied waits.
        rate_limit: Master switch for per-domain rate limiting.
        min_delay: Minimum delay in seconds between requests to the same domain.
            Used when ``requests_per_second`` is ``None``.
        requests_per_second: If set, use a token-bucket limiter at this steady
            rate instead of a fixed ``min_delay``. Takes precedence over
            ``min_delay``.
        rate_limit_burst: Token-bucket capacity (how many requests may burst
            before throttling kicks in). Defaults to ``max(1, requests_per_second)``.
        per_domain_rate_limit: If ``True``, rate limits are tracked per domain;
            if ``False``, a single global limit applies to all requests.
        rotate_user_agent: If ``True``, rotate the ``User-Agent`` header per
            request using the built-in pool.
        randomize_headers: If ``True``, add lightly randomised realistic headers
            (``Accept-Language`` etc.) per request.
        default_headers: Headers applied to every request (lowest precedence).
        proxy_strategy: ``"round_robin"`` or ``"random"`` proxy selection.
        proxy_max_failures: Consecutive failures before a proxy is cooled down.
        proxy_cooldown: Seconds a proxy is banned for after hitting
            ``proxy_max_failures``.
        proxy_remove_after_bans: Number of cool-down cycles a proxy may incur
            before it is permanently removed (marked dead).
    """

    # --- General -----------------------------------------------------------
    timeout: float = 30.0

    # --- Retry / backoff ---------------------------------------------------
    max_retries: int = 3
    backoff_factor: float = 0.5
    backoff_max: float = 60.0
    backoff_jitter: bool = True
    retry_statuses: Collection[int] = DEFAULT_RETRY_STATUSES
    respect_retry_after: bool = True
    retry_after_max: float = 300.0

    # --- Rate limiting (per-domain) ----------------------------------------
    rate_limit: bool = True
    min_delay: float | None = 1.0
    requests_per_second: float | None = None
    rate_limit_burst: int | None = None
    per_domain_rate_limit: bool = True

    # --- Headers / user-agent ----------------------------------------------
    rotate_user_agent: bool = True
    randomize_headers: bool = False
    default_headers: Mapping[str, str] = field(default_factory=dict)

    # --- Proxies -----------------------------------------------------------
    proxy_strategy: str = "round_robin"
    proxy_max_failures: int = 3
    proxy_cooldown: float = 60.0
    proxy_remove_after_bans: int = 3

    def __post_init__(self) -> None:
        if self.timeout is not None and self.timeout <= 0:
            raise ConfigurationError("timeout must be positive")
        if self.max_retries < 0:
            raise ConfigurationError("max_retries must be >= 0")
        if self.backoff_factor < 0:
            raise ConfigurationError("backoff_factor must be >= 0")
        if self.backoff_max < 0:
            raise ConfigurationError("backoff_max must be >= 0")
        if self.retry_after_max < 0:
            raise ConfigurationError("retry_after_max must be >= 0")
        if self.min_delay is not None and self.min_delay < 0:
            raise ConfigurationError("min_delay must be >= 0")
        if self.requests_per_second is not None and self.requests_per_second <= 0:
            raise ConfigurationError("requests_per_second must be > 0")
        if self.rate_limit_burst is not None and self.rate_limit_burst < 1:
            raise ConfigurationError("rate_limit_burst must be >= 1")
        if self.proxy_strategy not in PROXY_STRATEGIES:
            raise ConfigurationError(
                f"proxy_strategy must be one of {sorted(PROXY_STRATEGIES)}, "
                f"got {self.proxy_strategy!r}"
            )
        if self.proxy_max_failures < 1:
            raise ConfigurationError("proxy_max_failures must be >= 1")
        if self.proxy_cooldown < 0:
            raise ConfigurationError("proxy_cooldown must be >= 0")
        if self.proxy_remove_after_bans < 1:
            raise ConfigurationError("proxy_remove_after_bans must be >= 1")
        # Normalise retry statuses to an immutable, deduplicated set for fast lookup.
        self.retry_statuses = frozenset(int(s) for s in self.retry_statuses)
