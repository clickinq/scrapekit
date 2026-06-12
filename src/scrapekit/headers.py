"""User-agent and header rotation.

Provides :class:`UserAgentRotator` with a built-in pool of realistic, current
user-agent strings, plus helpers to assemble a plausible set of request headers.
Rotation happens per request when enabled in :class:`~scrapekit.config.Config`.
"""

from __future__ import annotations

import itertools
import random
import threading
from collections.abc import Sequence

#: A small, curated pool of realistic, current desktop user-agent strings
#: spanning the major browser/OS combinations. These are intentionally generic
#: and are not tailored to defeat any particular site's bot detection.
DEFAULT_USER_AGENTS: tuple[str, ...] = (
    # Chrome / Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    # Chrome / macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    # Chrome / Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    # Firefox / Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) "
    "Gecko/20100101 Firefox/133.0",
    # Firefox / macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:133.0) "
    "Gecko/20100101 Firefox/133.0",
    # Firefox / Linux
    "Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0",
    # Safari / macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.1 Safari/605.1.15",
    # Edge / Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
)

#: Plausible ``Accept-Language`` values for light header randomisation.
_ACCEPT_LANGUAGES: tuple[str, ...] = (
    "en-US,en;q=0.9",
    "en-GB,en;q=0.9",
    "en-US,en;q=0.8",
    "en-CA,en;q=0.9,fr-CA;q=0.8",
    "en-AU,en;q=0.9",
)

_ACCEPT = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,"
    "image/avif,image/webp,*/*;q=0.8"
)


class UserAgentRotator:
    """Rotates through a pool of user-agent strings.

    Args:
        user_agents: The pool to rotate through. Defaults to
            :data:`DEFAULT_USER_AGENTS`.
        strategy: ``"random"`` to pick a random agent each call, or
            ``"round_robin"`` to cycle deterministically through the pool.

    Raises:
        ValueError: If ``user_agents`` is empty or ``strategy`` is unknown.
    """

    def __init__(
        self,
        user_agents: Sequence[str] | None = None,
        strategy: str = "random",
    ) -> None:
        if user_agents is None:
            agents: list[str] = list(DEFAULT_USER_AGENTS)
        else:
            agents = list(user_agents)
        if not agents:
            raise ValueError("user_agents must not be empty")
        if strategy not in ("random", "round_robin"):
            raise ValueError("strategy must be 'random' or 'round_robin'")
        self._agents = agents
        self._strategy = strategy
        self._lock = threading.Lock()
        self._cycle = itertools.cycle(self._agents)

    def __len__(self) -> int:
        return len(self._agents)

    @property
    def user_agents(self) -> list[str]:
        """A copy of the user-agent pool."""
        return list(self._agents)

    def get(self) -> str:
        """Return the next user-agent string according to the strategy."""
        if self._strategy == "random":
            return random.choice(self._agents)
        with self._lock:
            return next(self._cycle)


def random_headers(
    user_agent: str | None = None,
    *,
    rng: random.Random | None = None,
) -> dict[str, str]:
    """Build a realistic set of request headers.

    Args:
        user_agent: If given, included as the ``User-Agent`` header.
        rng: Optional random source (useful for deterministic tests).

    Returns:
        A new dictionary of headers including a randomised ``Accept-Language``
        and standard ``Accept``/``Accept-Encoding`` values.
    """
    chooser = (rng or random).choice
    headers: dict[str, str] = {
        "Accept": _ACCEPT,
        "Accept-Language": chooser(_ACCEPT_LANGUAGES),
        "Accept-Encoding": "gzip, deflate, br",
        "Upgrade-Insecure-Requests": "1",
    }
    if user_agent is not None:
        headers["User-Agent"] = user_agent
    return headers
