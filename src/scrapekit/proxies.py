"""Proxy pool with rotation, health checks, and failure tracking.

A :class:`ProxyPool` holds a list of proxies and hands them out one at a time via
round-robin or random selection. It tracks per-proxy failures: after a
configurable number of consecutive failures a proxy is *cooled down* (temporarily
banned), and after repeated cool-downs it is permanently removed. A concurrent
:meth:`ProxyPool.health_check` can probe every proxy against a test URL and mark
the unreachable ones dead.

The pool is thread-safe and integrates with :class:`~scrapekit.client.Scraper`,
which selects a fresh proxy per attempt and reports success/failure back.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable

import requests

from .config import Config
from .exceptions import NoHealthyProxiesError

logger = logging.getLogger("scrapekit.proxies")

ClockFn = Callable[[], float]


@dataclass
class Proxy:
    """A single proxy endpoint and its health state.

    Attributes:
        url: The proxy URL, e.g. ``"http://user:pass@host:8080"``.
        consecutive_failures: Failures since the last success (reset on success).
        total_failures: Lifetime failure count.
        total_successes: Lifetime success count.
        ban_count: How many times this proxy has been cooled down.
        banned_until: Monotonic timestamp until which the proxy is cooling down.
        dead: If ``True``, the proxy is permanently removed from rotation.
    """

    url: str
    consecutive_failures: int = 0
    total_failures: int = 0
    total_successes: int = 0
    ban_count: int = 0
    banned_until: float = 0.0
    dead: bool = False

    def as_requests_dict(self) -> dict[str, str]:
        """Return a ``proxies=`` mapping suitable for ``requests``."""
        return {"http": self.url, "https": self.url}

    def is_available(self, now: float) -> bool:
        """Return ``True`` if the proxy is neither dead nor currently banned."""
        return not self.dead and now >= self.banned_until


class ProxyPool:
    """A rotating, self-healing pool of proxies.

    Args:
        proxies: Proxy URLs (strings) or :class:`Proxy` objects.
        strategy: ``"round_robin"`` or ``"random"`` selection.
        max_failures: Consecutive failures before a proxy is cooled down.
        cooldown: Seconds a proxy is banned for after being cooled down.
        remove_after_bans: Cool-down cycles allowed before permanent removal.
        clock: Monotonic time source (injectable for tests).

    Raises:
        ValueError: If ``proxies`` is empty or ``strategy`` is unknown.
    """

    def __init__(
        self,
        proxies: Sequence[str | Proxy],
        *,
        strategy: str = "round_robin",
        max_failures: int = 3,
        cooldown: float = 60.0,
        remove_after_bans: int = 3,
        clock: ClockFn = time.monotonic,
    ) -> None:
        if strategy not in ("round_robin", "random"):
            raise ValueError("strategy must be 'round_robin' or 'random'")
        self._proxies: list[Proxy] = [
            p if isinstance(p, Proxy) else Proxy(url=str(p)) for p in proxies
        ]
        if not self._proxies:
            raise ValueError("proxies must not be empty")
        self.strategy = strategy
        self.max_failures = max_failures
        self.cooldown = cooldown
        self.remove_after_bans = remove_after_bans
        self._clock = clock
        self._lock = threading.Lock()
        self._rr_index = 0

    @classmethod
    def from_config(cls, proxies: Sequence[str | Proxy], config: Config) -> ProxyPool:
        """Build a pool using proxy-related settings from a :class:`Config`."""
        return cls(
            proxies,
            strategy=config.proxy_strategy,
            max_failures=config.proxy_max_failures,
            cooldown=config.proxy_cooldown,
            remove_after_bans=config.proxy_remove_after_bans,
        )

    # -- Introspection ------------------------------------------------------
    def __len__(self) -> int:
        return len(self._proxies)

    @property
    def proxies(self) -> list[Proxy]:
        """A snapshot copy of all proxies (including dead/banned ones)."""
        with self._lock:
            return list(self._proxies)

    def healthy(self) -> list[Proxy]:
        """Return the proxies currently available for use."""
        now = self._clock()
        with self._lock:
            return [p for p in self._proxies if p.is_available(now)]

    def _available_locked(self) -> list[Proxy]:
        now = self._clock()
        return [p for p in self._proxies if p.is_available(now)]

    # -- Selection ----------------------------------------------------------
    def get(self) -> Proxy:
        """Return the next available proxy.

        Returns:
            An available :class:`Proxy`.

        Raises:
            NoHealthyProxiesError: If no proxy is currently available.
        """
        with self._lock:
            available = self._available_locked()
            if not available:
                raise NoHealthyProxiesError(
                    f"no healthy proxies available (pool size {len(self._proxies)})"
                )
            if self.strategy == "random":
                return random.choice(available)
            # Round-robin over the *full* list so selection stays evenly
            # distributed; skip unavailable proxies.
            count = len(self._proxies)
            for _ in range(count):
                proxy = self._proxies[self._rr_index % count]
                self._rr_index = (self._rr_index + 1) % count
                if proxy.is_available(self._clock()):
                    return proxy
            # Fallback (shouldn't happen given the check above).
            return available[0]

    # -- Failure / success tracking ----------------------------------------
    def mark_success(self, proxy: Proxy) -> None:
        """Record a successful use of ``proxy``, clearing its failure state."""
        with self._lock:
            proxy.total_successes += 1
            proxy.consecutive_failures = 0
            proxy.banned_until = 0.0

    def mark_failure(self, proxy: Proxy) -> None:
        """Record a failed use of ``proxy``, cooling it down or removing it."""
        with self._lock:
            proxy.total_failures += 1
            proxy.consecutive_failures += 1
            if proxy.consecutive_failures >= self.max_failures:
                proxy.ban_count += 1
                proxy.consecutive_failures = 0
                if proxy.ban_count >= self.remove_after_bans:
                    proxy.dead = True
                    logger.warning("proxy %s permanently removed (too many bans)", proxy.url)
                else:
                    proxy.banned_until = self._clock() + self.cooldown
                    logger.info(
                        "proxy %s cooling down for %.0fs (ban #%d)",
                        proxy.url,
                        self.cooldown,
                        proxy.ban_count,
                    )

    def mark_dead(self, proxy: Proxy) -> None:
        """Permanently remove ``proxy`` from rotation."""
        with self._lock:
            proxy.dead = True

    def reset(self, proxy: Proxy) -> None:
        """Fully restore ``proxy`` to healthy state."""
        with self._lock:
            proxy.consecutive_failures = 0
            proxy.ban_count = 0
            proxy.banned_until = 0.0
            proxy.dead = False

    # -- Health checking ----------------------------------------------------
    def health_check(
        self,
        test_url: str,
        *,
        timeout: float = 10.0,
        max_workers: int = 10,
        session: Any = None,
    ) -> int:
        """Probe every proxy against ``test_url`` concurrently.

        Each proxy that fails to return a successful (2xx/3xx) response is marked
        dead; each that succeeds is reset to healthy.

        Args:
            test_url: A URL expected to respond quickly, e.g.
                ``"https://httpbin.org/ip"``.
            timeout: Per-probe timeout in seconds.
            max_workers: Maximum concurrent probes.
            session: Optional object with a ``get`` method (defaults to the
                ``requests`` module); useful for injecting a fake in tests.

        Returns:
            The number of proxies that are healthy after the check.
        """
        getter: Any = session if session is not None else requests
        targets = self.proxies

        def probe(proxy: Proxy) -> bool:
            try:
                resp = getter.get(
                    test_url,
                    proxies=proxy.as_requests_dict(),
                    timeout=timeout,
                )
                return bool(200 <= resp.status_code < 400)
            except Exception as exc:  # noqa: BLE001 - any failure means unhealthy
                logger.debug("health check failed for %s: %s", proxy.url, exc)
                return False

        if not targets:
            return 0

        workers = max(1, min(max_workers, len(targets)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            results = list(executor.map(probe, targets))

        healthy = 0
        for proxy, ok in zip(targets, results):
            if ok:
                self.reset(proxy)
                healthy += 1
            else:
                self.mark_dead(proxy)
        logger.info("health check: %d/%d proxies healthy", healthy, len(targets))
        return healthy
