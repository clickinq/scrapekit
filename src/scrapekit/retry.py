"""Retry decisions and backoff timing.

This module is deliberately side-effect free: it decides *whether* to retry and
*how long* to wait, but it never sleeps and never makes requests. The actual
retry loop lives in :class:`~scrapekit.client.Scraper`, which interleaves these
decisions with rate limiting and proxy rotation.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING

import requests

from .config import Config

if TYPE_CHECKING:  # pragma: no cover - typing only
    from requests import Response

logger = logging.getLogger("scrapekit.retry")

#: Transport-level exceptions that are safe to retry. ``ProxyError`` is a
#: subclass of ``ConnectionError`` and is therefore covered as well.
RETRYABLE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)


def parse_retry_after(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header value into a number of seconds.

    The header may be either an integer number of seconds (``"120"``) or an
    HTTP date (``"Wed, 21 Oct 2025 07:28:00 GMT"``). For an HTTP date the result
    is the number of seconds from now until that moment.

    Args:
        value: The raw header value, or ``None``.

    Returns:
        A non-negative number of seconds to wait, or ``None`` if the value is
        absent or cannot be parsed.
    """
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    delta = (when - datetime.now(timezone.utc)).total_seconds()
    return max(0.0, delta)


@dataclass
class RetryPolicy:
    """Encapsulates the rules governing retries and backoff.

    Construct one directly, or use :meth:`from_config` to derive it from a
    :class:`~scrapekit.config.Config`.

    Attributes:
        max_retries: Maximum retries after the initial attempt.
        backoff_factor: Base multiplier for exponential backoff.
        backoff_max: Cap on any single backoff delay (seconds).
        jitter: Whether to apply randomised jitter to backoff delays.
        retry_statuses: Status codes that warrant a retry.
        respect_retry_after: Whether to honour the ``Retry-After`` header.
        retry_after_max: Cap on an honoured ``Retry-After`` value (seconds).
    """

    max_retries: int = 3
    backoff_factor: float = 0.5
    backoff_max: float = 60.0
    jitter: bool = True
    retry_statuses: frozenset[int] = frozenset({429, 500, 502, 503, 504})
    respect_retry_after: bool = True
    retry_after_max: float = 300.0

    @classmethod
    def from_config(cls, config: Config) -> RetryPolicy:
        """Build a :class:`RetryPolicy` from a :class:`~scrapekit.config.Config`."""
        return cls(
            max_retries=config.max_retries,
            backoff_factor=config.backoff_factor,
            backoff_max=config.backoff_max,
            jitter=config.backoff_jitter,
            retry_statuses=frozenset(config.retry_statuses),
            respect_retry_after=config.respect_retry_after,
            retry_after_max=config.retry_after_max,
        )

    def is_retryable_status(self, status_code: int) -> bool:
        """Return ``True`` if a response with this status should be retried."""
        return status_code in self.retry_statuses

    def is_retryable_exception(self, exc: BaseException) -> bool:
        """Return ``True`` if this transport exception should be retried."""
        return isinstance(exc, RETRYABLE_EXCEPTIONS)

    def backoff_delay(self, retry_index: int) -> float:
        """Compute the exponential-backoff delay for a given retry.

        Args:
            retry_index: Zero-based index of the retry (``0`` for the first
                retry, ``1`` for the second, and so on).

        Returns:
            The delay in seconds. With ``jitter`` enabled this uses "equal
            jitter": half the capped delay plus a random amount up to the other
            half, keeping delays in ``[capped / 2, capped]``.
        """
        if retry_index < 0:
            retry_index = 0
        base = self.backoff_factor * (2.0 ** retry_index)
        capped = min(base, self.backoff_max)
        if not self.jitter or capped == 0:
            return capped
        half = capped / 2.0
        return half + random.uniform(0.0, half)

    def retry_after_delay(self, response: Response) -> float | None:
        """Return the honoured ``Retry-After`` delay for a response, if any.

        Returns ``None`` when ``respect_retry_after`` is disabled, the header is
        absent, or it cannot be parsed. The returned value is capped at
        :attr:`retry_after_max`.
        """
        if not self.respect_retry_after:
            return None
        seconds = parse_retry_after(response.headers.get("Retry-After"))
        if seconds is None:
            return None
        return min(seconds, self.retry_after_max)

    def next_delay(self, retry_index: int, response: Response | None = None) -> float:
        """Compute how long to wait before the next attempt.

        Prefers a server-supplied ``Retry-After`` value when present and
        enabled; otherwise falls back to exponential backoff.

        Args:
            retry_index: Zero-based index of the retry about to be performed.
            response: The response from the failed attempt, if there was one.

        Returns:
            The delay in seconds.
        """
        if response is not None:
            retry_after = self.retry_after_delay(response)
            if retry_after is not None:
                return retry_after
        return self.backoff_delay(retry_index)
