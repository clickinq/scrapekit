"""Shared test fixtures and HTTP fakes.

No test makes a real network call: everything goes through the in-memory
``FakeSession`` / ``FakeResponse`` doubles, and all timing uses ``FakeClock`` so
the suite runs instantly and deterministically.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Union

import pytest
import requests


class FakeResponse:
    """A minimal stand-in for :class:`requests.Response`."""

    def __init__(
        self,
        status_code: int = 200,
        *,
        headers: dict[str, str] | None = None,
        text: str = "",
        url: str = "",
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text
        self.content = text.encode("utf-8")
        self.url = url

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)  # type: ignore[arg-type]

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"FakeResponse({self.status_code})"


# An "action" is either a response to return or an exception to raise.
Action = Union[FakeResponse, BaseException]


class FakeSession:
    """A scripted replacement for :class:`requests.Session`.

    Each call to :meth:`request` consumes the next scripted action: a
    :class:`FakeResponse` is returned, an exception instance is raised.
    """

    def __init__(self, actions: Sequence[Action]) -> None:
        self.actions: list[Action] = list(actions)
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.actions:
            raise AssertionError("FakeSession ran out of scripted actions")
        action = self.actions.pop(0)
        if isinstance(action, BaseException):
            raise action
        return action

    def close(self) -> None:
        self.closed = True


class FakeClock:
    """A controllable monotonic clock whose ``sleep`` advances time."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start
        self.slept: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    @property
    def total_slept(self) -> float:
        return sum(self.slept)


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def response_factory():
    """Return a helper that builds :class:`FakeResponse` objects."""

    def _make(status_code: int = 200, **kwargs: Any) -> FakeResponse:
        return FakeResponse(status_code, **kwargs)

    return _make
