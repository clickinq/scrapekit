"""Tests for the rate limiter and token bucket (timing via FakeClock)."""

from __future__ import annotations

import pytest

from scrapekit import Config, RateLimiter, TokenBucket

from .conftest import FakeClock


# -- TokenBucket ------------------------------------------------------------
def test_token_bucket_allows_burst_then_throttles():
    clock = FakeClock()
    bucket = TokenBucket(rate=2.0, capacity=2, clock=clock.time, sleep=clock.sleep)
    # Two tokens available -> no waiting.
    assert bucket.acquire() == 0.0
    assert bucket.acquire() == 0.0
    # Third must wait for a token to refill: 1 token / 2 per sec = 0.5s.
    assert bucket.acquire() == pytest.approx(0.5)
    assert clock.total_slept == pytest.approx(0.5)


def test_token_bucket_refills_over_time():
    clock = FakeClock()
    bucket = TokenBucket(rate=1.0, capacity=1, clock=clock.time, sleep=clock.sleep)
    assert bucket.acquire() == 0.0
    # Simulate time passing without acquiring; a token should refill.
    clock.now += 1.0
    assert bucket.acquire() == 0.0


def test_token_bucket_validates_args():
    with pytest.raises(ValueError):
        TokenBucket(rate=0, capacity=1)
    with pytest.raises(ValueError):
        TokenBucket(rate=1, capacity=0)


# -- RateLimiter: fixed delay ----------------------------------------------
def test_min_delay_spaces_same_domain():
    clock = FakeClock()
    limiter = RateLimiter(min_delay=1.0, clock=clock.time, sleep=clock.sleep)
    url = "https://example.com/a"
    assert limiter.acquire(url) == 0.0  # first request: immediate
    assert limiter.acquire(url) == pytest.approx(1.0)  # second: waits min_delay
    assert clock.total_slept == pytest.approx(1.0)


def test_min_delay_is_per_domain():
    clock = FakeClock()
    limiter = RateLimiter(min_delay=1.0, clock=clock.time, sleep=clock.sleep)
    assert limiter.acquire("https://a.com/x") == 0.0
    # Different domain is independent -> no wait.
    assert limiter.acquire("https://b.com/y") == 0.0


def test_global_mode_shares_one_limit():
    clock = FakeClock()
    limiter = RateLimiter(
        min_delay=1.0, per_domain=False, clock=clock.time, sleep=clock.sleep
    )
    assert limiter.acquire("https://a.com/x") == 0.0
    # Same global bucket -> second request to a *different* domain still waits.
    assert limiter.acquire("https://b.com/y") == pytest.approx(1.0)


def test_no_wait_when_enough_time_passed():
    clock = FakeClock()
    limiter = RateLimiter(min_delay=1.0, clock=clock.time, sleep=clock.sleep)
    url = "https://example.com"
    limiter.acquire(url)
    clock.now += 5.0
    assert limiter.acquire(url) == 0.0


# -- RateLimiter: token bucket mode ----------------------------------------
def test_requests_per_second_uses_token_bucket():
    clock = FakeClock()
    limiter = RateLimiter(
        requests_per_second=2.0, burst=2, clock=clock.time, sleep=clock.sleep
    )
    url = "https://example.com"
    assert limiter.acquire(url) == 0.0
    assert limiter.acquire(url) == 0.0
    assert limiter.acquire(url) == pytest.approx(0.5)


# -- from_config ------------------------------------------------------------
def test_from_config_disabled_returns_none():
    assert RateLimiter.from_config(Config(rate_limit=False)) is None


def test_from_config_no_strategy_returns_none():
    assert RateLimiter.from_config(Config(min_delay=None, requests_per_second=None)) is None


def test_from_config_builds_delay_limiter():
    limiter = RateLimiter.from_config(Config(min_delay=2.0))
    assert limiter is not None
    assert limiter.enabled
    assert limiter.min_delay == 2.0


def test_from_config_prefers_requests_per_second():
    limiter = RateLimiter.from_config(Config(requests_per_second=5.0))
    assert limiter is not None
    assert limiter.requests_per_second == 5.0
