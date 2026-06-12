"""Exception hierarchy for scrapekit.

All exceptions raised by scrapekit derive from :class:`ScrapeKitError`, so callers
can catch everything from the library with a single ``except`` clause while still
being able to handle specific failure modes when they care to.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from requests import Response


class ScrapeKitError(Exception):
    """Base class for every exception raised by scrapekit."""


class ConfigurationError(ScrapeKitError):
    """Raised when a :class:`~scrapekit.config.Config` value is invalid."""


class RetryExhaustedError(ScrapeKitError):
    """Raised when a request still fails after all retry attempts.

    Attributes:
        attempts: The total number of attempts that were made.
        last_response: The final HTTP response, if the last attempt produced one
            (for example a persistent ``503``). ``None`` if the last attempt
            raised a transport-level exception instead.
        last_exception: The final transport-level exception, if the last attempt
            raised one. ``None`` if the last attempt produced a response.
    """

    def __init__(
        self,
        message: str,
        *,
        attempts: int,
        last_response: Response | None = None,
        last_exception: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.last_response = last_response
        self.last_exception = last_exception


class ProxyError(ScrapeKitError):
    """Base class for proxy-related errors."""


class NoHealthyProxiesError(ProxyError):
    """Raised when a :class:`~scrapekit.proxies.ProxyPool` has no usable proxy.

    This happens when every proxy is either marked dead or currently cooling down
    after consecutive failures.
    """


class HookError(ScrapeKitError):
    """Raised when a user-registered hook fails in an unrecoverable way."""
