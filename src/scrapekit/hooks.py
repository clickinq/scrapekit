"""Request/response hook system.

Hooks let you extend a :class:`~scrapekit.client.Scraper` without subclassing it:
attach callables that run before each request and after each response. Typical
uses are logging, metrics, custom parsing, and caching.

Pre-request hooks receive a mutable :class:`RequestContext` and may modify it in
place (for example to add a header). A pre-request hook may also *return* a
:class:`requests.Response` to short-circuit the network call entirely, which is
how you would implement a cache.

Post-response hooks receive the :class:`requests.Response` and the
:class:`RequestContext`. They may return a replacement response, or ``None`` to
leave it unchanged.
"""

from __future__ import annotations

import logging
from collections.abc import MutableMapping
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from requests import Response

    from .proxies import Proxy

logger = logging.getLogger("scrapekit.hooks")

PreRequestHook = Callable[["RequestContext"], "Response | None"]
PostResponseHook = Callable[["Response", "RequestContext"], "Response | None"]


@dataclass
class RequestContext:
    """Mutable description of a request as it is about to be sent.

    Hooks may mutate :attr:`headers` and :attr:`kwargs` in place to influence the
    outgoing request, and may stash arbitrary data on :attr:`metadata` to share
    state between pre- and post-hooks.

    Attributes:
        method: The HTTP method, e.g. ``"GET"``.
        url: The target URL.
        headers: The outgoing headers (mutable).
        kwargs: Extra keyword arguments forwarded to ``requests`` (mutable).
        proxy: The proxy selected for this attempt, if any.
        attempt: 1-based attempt number (increments on each retry).
        metadata: Free-form scratch space shared across hooks for one request.
    """

    method: str
    url: str
    headers: MutableMapping[str, str]
    kwargs: dict[str, Any]
    proxy: Proxy | None = None
    attempt: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)


class HookManager:
    """Registers and runs request/response hooks.

    Hooks run in registration order. The :meth:`pre_request` and
    :meth:`post_response` methods double as decorators::

        hooks = HookManager()

        @hooks.pre_request
        def add_token(ctx):
            ctx.headers["Authorization"] = "Bearer ..."
    """

    def __init__(self) -> None:
        self._pre_request: list[PreRequestHook] = []
        self._post_response: list[PostResponseHook] = []

    def pre_request(self, hook: PreRequestHook) -> PreRequestHook:
        """Register a pre-request hook. Returns the hook (usable as a decorator)."""
        self._pre_request.append(hook)
        return hook

    def post_response(self, hook: PostResponseHook) -> PostResponseHook:
        """Register a post-response hook. Returns the hook (usable as a decorator)."""
        self._post_response.append(hook)
        return hook

    @property
    def pre_request_hooks(self) -> list[PreRequestHook]:
        """A copy of the registered pre-request hooks."""
        return list(self._pre_request)

    @property
    def post_response_hooks(self) -> list[PostResponseHook]:
        """A copy of the registered post-response hooks."""
        return list(self._post_response)

    def run_pre_request(self, context: RequestContext) -> Response | None:
        """Run all pre-request hooks.

        Args:
            context: The mutable request context.

        Returns:
            A :class:`requests.Response` if any hook short-circuited the request
            (e.g. a cache hit), otherwise ``None``.
        """
        for hook in self._pre_request:
            result = hook(context)
            if result is not None:
                logger.debug("pre-request hook %s short-circuited %s", hook, context.url)
                return result
        return None

    def run_post_response(
        self, response: Response, context: RequestContext
    ) -> Response:
        """Run all post-response hooks, returning the (possibly replaced) response."""
        for hook in self._post_response:
            result = hook(response, context)
            if result is not None:
                response = result
        return response
