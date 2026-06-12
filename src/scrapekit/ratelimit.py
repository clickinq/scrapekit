"""Per-domain rate limiting.

Two strategies are supported and both are thread-safe:

* **Fixed delay** (``min_delay``): enforce a minimum gap between consecutive
  requests to the same domain.
* **Token bucket** (``requests_per_second``): allow a steady rate with a
  configurable burst capacity.

Limits are tracked per domain by default so you stay polite to each host
independently. The clock and sleep functions are injectable, which keeps the
timing logic exact and fully testable without real waits.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable
from urllib.parse import urlsplit

from .config import Config

logger = logging.getLogger("scrapekit.ratelimit")

ClockFn = Callable[[], float]
SleepFn = Callable[[float], None]


class TokenBucket:
    """A thread-safe token-bucket rate limiter.

    Tokens refill continuously at ``rate`` per second up to ``capacity``. Each
    :meth:`acquire` consumes a token, sleeping if necessary until one is
    available.

    Args:
        rate: Tokens added per second (the steady-state request rate).
        capacity: Maximum number of tokens (the burst size).
        clock: Monotonic time source, in seconds.
        sleep: Sleep function taking a number of seconds.
    """

    def __init__(
        self,
        rate: float,
        capacity: float,
        *,
        clock: ClockFn = time.monotonic,
        sleep: SleepFn = time.sleep,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate must be > 0")
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self.rate = rate
        self.capacity = float(capacity)
        self._clock = clock
        self._sleep = sleep
        self._tokens = float(capacity)
        self._updated = clock()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = self._clock()
        elapsed = now - self._updated
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
            self._updated = now

    def acquire(self, tokens: float = 1.0) -> float:
        """Consume ``tokens``, sleeping if needed. Returns seconds slept."""
        with self._lock:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                return 0.0
            deficit = tokens - self._tokens
            wait = deficit / self.rate
            self._sleep(wait)
            self._refill()
            # We may have over- or under-refilled by a hair; clamp at zero.
            self._tokens = max(0.0, self._tokens - tokens)
            return wait


class _DomainState:
    """Per-domain limiter state."""

    __slots__ = ("lock", "last", "bucket")

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.last: float | None = None
        self.bucket: TokenBucket | None = None


class RateLimiter:
    """Per-domain rate limiter supporting fixed-delay or token-bucket strategies.

    Exactly one strategy is active. If ``requests_per_second`` is given it takes
    precedence and a token bucket is used; otherwise ``min_delay`` enforces a
    fixed gap. If neither is set, :meth:`acquire` is a no-op.

    Args:
        min_delay: Minimum seconds between requests to the same domain.
        requests_per_second: Steady token-bucket rate (overrides ``min_delay``).
        burst: Token-bucket capacity. Defaults to ``max(1, requests_per_second)``.
        per_domain: Track limits per domain (``True``) or globally (``False``).
        clock: Monotonic time source, in seconds.
        sleep: Sleep function taking a number of seconds.
    """

    def __init__(
        self,
        *,
        min_delay: float | None = None,
        requests_per_second: float | None = None,
        burst: int | None = None,
        per_domain: bool = True,
        clock: ClockFn = time.monotonic,
        sleep: SleepFn = time.sleep,
    ) -> None:
        self.min_delay = min_delay
        self.requests_per_second = requests_per_second
        self.per_domain = per_domain
        self._clock = clock
        self._sleep = sleep
        if requests_per_second is not None:
            self._capacity = float(burst) if burst else max(1.0, requests_per_second)
        else:
            self._capacity = float(burst) if burst else 1.0
        self._states: dict[str, _DomainState] = {}
        self._states_lock = threading.Lock()

    @classmethod
    def from_config(cls, config: Config) -> RateLimiter | None:
        """Build a :class:`RateLimiter` from config, or ``None`` if disabled.

        Returns ``None`` when rate limiting is turned off or no strategy is
        configured, so the caller can skip limiting entirely.
        """
        if not config.rate_limit:
            return None
        if config.requests_per_second is None and config.min_delay in (None, 0):
            return None
        return cls(
            min_delay=config.min_delay,
            requests_per_second=config.requests_per_second,
            burst=config.rate_limit_burst,
            per_domain=config.per_domain_rate_limit,
        )

    @property
    def enabled(self) -> bool:
        """Whether this limiter will actually throttle requests."""
        return self.requests_per_second is not None or bool(self.min_delay)

    def _domain(self, url: str) -> str:
        if not self.per_domain:
            return "*"
        return urlsplit(url).netloc.lower() or "*"

    def _state_for(self, domain: str) -> _DomainState:
        with self._states_lock:
            state = self._states.get(domain)
            if state is None:
                state = _DomainState()
                if self.requests_per_second is not None:
                    state.bucket = TokenBucket(
                        self.requests_per_second,
                        self._capacity,
                        clock=self._clock,
                        sleep=self._sleep,
                    )
                self._states[domain] = state
            return state

    def acquire(self, url: str) -> float:
        """Block until a request to ``url`` is permitted by the rate limit.

        Args:
            url: The URL about to be requested; its domain selects the bucket.

        Returns:
            The number of seconds slept (``0.0`` if no wait was needed).
        """
        if not self.enabled:
            return 0.0
        domain = self._domain(url)
        state = self._state_for(domain)

        if state.bucket is not None:
            slept = state.bucket.acquire()
            if slept:
                logger.debug("rate limit: slept %.3fs for %s", slept, domain)
            return slept

        # Fixed-delay strategy. Hold the per-domain lock across the sleep so
        # concurrent requests to the *same* domain serialise correctly, while
        # other domains remain unaffected.
        assert self.min_delay is not None
        with state.lock:
            now = self._clock()
            slept = 0.0
            if state.last is not None:
                wait = state.last + self.min_delay - now
                if wait > 0:
                    self._sleep(wait)
                    slept = wait
            state.last = self._clock()
            if slept:
                logger.debug("rate limit: slept %.3fs for %s", slept, domain)
            return slept
