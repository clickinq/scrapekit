"""The :class:`Scraper` client.

``Scraper`` wraps a :class:`requests.Session` and transparently applies retries,
per-domain rate limiting, proxy rotation, and header/user-agent rotation to every
request. Its method surface mirrors ``requests`` (:meth:`get`, :meth:`post`,
etc.) and it works as a context manager::

    from scrapekit import Scraper

    with Scraper() as scraper:
        resp = scraper.get("https://example.com")
        resp.raise_for_status()
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from typing import Any

import requests

from .config import Config
from .exceptions import RetryExhaustedError
from .headers import UserAgentRotator, random_headers
from .hooks import HookManager, RequestContext
from .proxies import Proxy, ProxyPool
from .ratelimit import RateLimiter
from .retry import RetryPolicy

logger = logging.getLogger("scrapekit.client")


class Scraper:
    """A resilient, well-behaved HTTP client for web scraping.

    Args:
        config: Settings controlling retries, rate limiting, headers, and
            proxies. Defaults to :class:`~scrapekit.config.Config` defaults.
        proxy_pool: A pre-built :class:`~scrapekit.proxies.ProxyPool`. Mutually
            exclusive with ``proxies``.
        proxies: A list of proxy URLs from which a pool is built using the
            proxy-related settings in ``config``. Mutually exclusive with
            ``proxy_pool``.
        user_agent_rotator: A custom :class:`~scrapekit.headers.UserAgentRotator`.
            One is created automatically when ``config.rotate_user_agent`` is set.
        hooks: A :class:`~scrapekit.hooks.HookManager` for request/response hooks.
        session: An existing :class:`requests.Session` to wrap. A new one is
            created if omitted.

    Raises:
        ValueError: If both ``proxy_pool`` and ``proxies`` are supplied.
    """

    def __init__(
        self,
        config: Config | None = None,
        *,
        proxy_pool: ProxyPool | None = None,
        proxies: Sequence[str] | None = None,
        user_agent_rotator: UserAgentRotator | None = None,
        hooks: HookManager | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.config = config or Config()

        if proxy_pool is not None and proxies is not None:
            raise ValueError("pass either proxy_pool or proxies, not both")
        if proxy_pool is not None:
            self.proxy_pool: ProxyPool | None = proxy_pool
        elif proxies:
            self.proxy_pool = ProxyPool.from_config(list(proxies), self.config)
        else:
            self.proxy_pool = None

        self.session = session or requests.Session()
        self.hooks = hooks or HookManager()
        self.retry_policy = RetryPolicy.from_config(self.config)
        self.rate_limiter: RateLimiter | None = RateLimiter.from_config(self.config)

        if user_agent_rotator is not None:
            self.user_agent_rotator: UserAgentRotator | None = user_agent_rotator
        elif self.config.rotate_user_agent:
            self.user_agent_rotator = UserAgentRotator()
        else:
            self.user_agent_rotator = None

    # -- Context manager ----------------------------------------------------
    def __enter__(self) -> Scraper:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying :class:`requests.Session`."""
        self.session.close()

    # -- Header assembly ----------------------------------------------------
    def _build_headers(
        self, per_request: dict[str, str] | None
    ) -> dict[str, str]:
        """Merge default, rotated, randomised, and per-request headers."""
        headers: dict[str, str] = dict(self.config.default_headers)
        if self.config.randomize_headers:
            headers.update(random_headers())
        if self.user_agent_rotator is not None:
            headers["User-Agent"] = self.user_agent_rotator.get()
        if per_request:
            headers.update(per_request)
        return headers

    # -- Core request loop --------------------------------------------------
    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        """Perform an HTTP request with retries, rate limiting, and rotation.

        Args:
            method: HTTP method, e.g. ``"GET"`` or ``"POST"``.
            url: Target URL.
            **kwargs: Passed through to :meth:`requests.Session.request` (e.g.
                ``params``, ``data``, ``json``, ``headers``, ``timeout``). An
                explicit ``proxies`` argument disables proxy-pool rotation for
                this call.

        Returns:
            The successful (or final non-retryable) :class:`requests.Response`.

        Raises:
            RetryExhaustedError: If every attempt failed with a retryable status
                or transport error.
            NoHealthyProxiesError: If a proxy pool is configured but has no
                usable proxy.
        """
        headers = self._build_headers(kwargs.pop("headers", None))
        timeout = kwargs.pop("timeout", self.config.timeout)
        explicit_proxies = kwargs.pop("proxies", None)

        max_attempts = self.config.max_retries + 1
        last_response: requests.Response | None = None
        last_exception: BaseException | None = None

        for attempt in range(1, max_attempts + 1):
            if self.rate_limiter is not None:
                self.rate_limiter.acquire(url)

            proxy: Proxy | None = None
            request_proxies = explicit_proxies
            if self.proxy_pool is not None and explicit_proxies is None:
                proxy = self.proxy_pool.get()  # may raise NoHealthyProxiesError
                request_proxies = proxy.as_requests_dict()

            context = RequestContext(
                method=method.upper(),
                url=url,
                headers=dict(headers),
                kwargs=dict(kwargs),
                proxy=proxy,
                attempt=attempt,
            )

            short_circuit = self.hooks.run_pre_request(context)
            if short_circuit is not None:
                return short_circuit

            try:
                response = self.session.request(
                    context.method,
                    context.url,
                    headers=context.headers,
                    timeout=timeout,
                    proxies=request_proxies,
                    **context.kwargs,
                )
            except Exception as exc:  # noqa: BLE001 - classified just below
                if not self.retry_policy.is_retryable_exception(exc):
                    raise
                last_exception = exc
                last_response = None
                if proxy is not None:
                    self.proxy_pool.mark_failure(proxy)  # type: ignore[union-attr]
                if attempt >= max_attempts:
                    break
                delay = self.retry_policy.backoff_delay(attempt - 1)
                logger.info(
                    "attempt %d/%d for %s failed (%s); retrying in %.2fs",
                    attempt, max_attempts, url, type(exc).__name__, delay,
                )
                self._sleep(delay)
                continue

            if proxy is not None:
                self.proxy_pool.mark_success(proxy)  # type: ignore[union-attr]

            response = self.hooks.run_post_response(response, context)

            if self.retry_policy.is_retryable_status(response.status_code):
                last_response = response
                last_exception = None
                if attempt >= max_attempts:
                    break  # exhausted; raise after the loop
                delay = self.retry_policy.next_delay(attempt - 1, response)
                logger.info(
                    "attempt %d/%d for %s returned %d; retrying in %.2fs",
                    attempt, max_attempts, url, response.status_code, delay,
                )
                self._sleep(delay)
                continue

            return response

        # All attempts exhausted.
        if last_response is not None:
            raise RetryExhaustedError(
                f"{method.upper()} {url} failed after {max_attempts} attempts; "
                f"last status {last_response.status_code}",
                attempts=max_attempts,
                last_response=last_response,
            )
        raise RetryExhaustedError(
            f"{method.upper()} {url} failed after {max_attempts} attempts; "
            f"last error {type(last_exception).__name__}: {last_exception}",
            attempts=max_attempts,
            last_exception=last_exception,
        )

    @staticmethod
    def _sleep(seconds: float) -> None:
        """Sleep wrapper (indirection keeps tests fast and deterministic)."""
        if seconds > 0:
            time.sleep(seconds)

    # -- requests-style convenience methods ---------------------------------
    def get(self, url: str, **kwargs: Any) -> requests.Response:
        """Send a ``GET`` request. See :meth:`request`."""
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> requests.Response:
        """Send a ``POST`` request. See :meth:`request`."""
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> requests.Response:
        """Send a ``PUT`` request. See :meth:`request`."""
        return self.request("PUT", url, **kwargs)

    def patch(self, url: str, **kwargs: Any) -> requests.Response:
        """Send a ``PATCH`` request. See :meth:`request`."""
        return self.request("PATCH", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> requests.Response:
        """Send a ``DELETE`` request. See :meth:`request`."""
        return self.request("DELETE", url, **kwargs)

    def head(self, url: str, **kwargs: Any) -> requests.Response:
        """Send a ``HEAD`` request. See :meth:`request`."""
        return self.request("HEAD", url, **kwargs)

    def options(self, url: str, **kwargs: Any) -> requests.Response:
        """Send an ``OPTIONS`` request. See :meth:`request`."""
        return self.request("OPTIONS", url, **kwargs)
