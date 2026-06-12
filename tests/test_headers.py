"""Tests for user-agent and header rotation."""

from __future__ import annotations

import random

import pytest

from scrapekit import UserAgentRotator, random_headers
from scrapekit.headers import DEFAULT_USER_AGENTS


def test_default_pool_is_nonempty():
    rotator = UserAgentRotator()
    assert len(rotator) == len(DEFAULT_USER_AGENTS)
    assert len(rotator) > 0


def test_round_robin_cycles():
    agents = ["ua-a", "ua-b", "ua-c"]
    rotator = UserAgentRotator(agents, strategy="round_robin")
    seen = [rotator.get() for _ in range(7)]
    assert seen == ["ua-a", "ua-b", "ua-c", "ua-a", "ua-b", "ua-c", "ua-a"]


def test_random_returns_pool_members():
    agents = ["ua-a", "ua-b", "ua-c"]
    rotator = UserAgentRotator(agents, strategy="random")
    for _ in range(30):
        assert rotator.get() in agents


def test_empty_pool_rejected():
    with pytest.raises(ValueError):
        UserAgentRotator([])


def test_bad_strategy_rejected():
    with pytest.raises(ValueError):
        UserAgentRotator(["ua"], strategy="spin")


def test_user_agents_property_is_a_copy():
    rotator = UserAgentRotator(["a", "b"])
    copy = rotator.user_agents
    copy.append("c")
    assert len(rotator) == 2


def test_random_headers_contains_expected_keys():
    headers = random_headers(user_agent="my-agent")
    assert headers["User-Agent"] == "my-agent"
    assert "Accept" in headers
    assert "Accept-Language" in headers
    assert "Accept-Encoding" in headers


def test_random_headers_without_user_agent():
    headers = random_headers()
    assert "User-Agent" not in headers


def test_random_headers_deterministic_with_seeded_rng():
    rng = random.Random(1234)
    first = random_headers(rng=rng)
    rng2 = random.Random(1234)
    second = random_headers(rng=rng2)
    assert first == second
