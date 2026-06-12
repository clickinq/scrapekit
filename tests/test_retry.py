"""Tests for retry decisions, backoff, and Retry-After parsing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import requests

from scrapekit import Config, RetryPolicy
from scrapekit.retry import parse_retry_after

from .conftest import FakeResponse


# -- parse_retry_after ------------------------------------------------------
def test_parse_retry_after_integer_seconds():
    assert parse_retry_after("120") == 120.0


def test_parse_retry_after_none_and_empty():
    assert parse_retry_after(None) is None
    assert parse_retry_after("") is None
    assert parse_retry_after("   ") is None


def test_parse_retry_after_garbage_returns_none():
    assert parse_retry_after("soon-ish") is None


def test_parse_retry_after_http_date_future():
    future = datetime.now(timezone.utc) + timedelta(seconds=60)
    header = future.strftime("%a, %d %b %Y %H:%M:%S GMT")
    seconds = parse_retry_after(header)
    assert seconds is not None
    # Allow a little slack for execution time.
    assert 50 <= seconds <= 61


def test_parse_retry_after_http_date_past_clamped_to_zero():
    past = datetime.now(timezone.utc) - timedelta(seconds=60)
    header = past.strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert parse_retry_after(header) == 0.0


# -- RetryPolicy classification --------------------------------------------
def test_is_retryable_status():
    policy = RetryPolicy(retry_statuses=frozenset({429, 503}))
    assert policy.is_retryable_status(429)
    assert policy.is_retryable_status(503)
    assert not policy.is_retryable_status(200)
    assert not policy.is_retryable_status(404)


def test_is_retryable_exception():
    policy = RetryPolicy()
    assert policy.is_retryable_exception(requests.exceptions.ConnectionError())
    assert policy.is_retryable_exception(requests.exceptions.Timeout())
    assert policy.is_retryable_exception(requests.exceptions.ProxyError())
    assert not policy.is_retryable_exception(ValueError("nope"))
    assert not policy.is_retryable_exception(requests.exceptions.InvalidURL())


# -- backoff ----------------------------------------------------------------
def test_backoff_without_jitter_is_exponential():
    policy = RetryPolicy(backoff_factor=0.5, jitter=False, backoff_max=100)
    assert policy.backoff_delay(0) == 0.5
    assert policy.backoff_delay(1) == 1.0
    assert policy.backoff_delay(2) == 2.0
    assert policy.backoff_delay(3) == 4.0


def test_backoff_is_capped():
    policy = RetryPolicy(backoff_factor=10, jitter=False, backoff_max=15)
    assert policy.backoff_delay(0) == 10
    assert policy.backoff_delay(5) == 15  # would be 320, capped to 15


def test_backoff_with_jitter_within_equal_jitter_band():
    policy = RetryPolicy(backoff_factor=1.0, jitter=True, backoff_max=100)
    for _ in range(200):
        delay = policy.backoff_delay(3)  # capped base = 8.0
        assert 4.0 <= delay <= 8.0


def test_backoff_negative_index_treated_as_zero():
    policy = RetryPolicy(backoff_factor=0.5, jitter=False)
    assert policy.backoff_delay(-3) == 0.5


# -- Retry-After ------------------------------------------------------------
def test_retry_after_delay_is_capped():
    policy = RetryPolicy(respect_retry_after=True, retry_after_max=30)
    resp = FakeResponse(503, headers={"Retry-After": "9999"})
    assert policy.retry_after_delay(resp) == 30


def test_retry_after_delay_disabled():
    policy = RetryPolicy(respect_retry_after=False)
    resp = FakeResponse(503, headers={"Retry-After": "10"})
    assert policy.retry_after_delay(resp) is None


def test_retry_after_delay_absent():
    policy = RetryPolicy(respect_retry_after=True)
    resp = FakeResponse(503, headers={})
    assert policy.retry_after_delay(resp) is None


def test_next_delay_prefers_retry_after():
    policy = RetryPolicy(backoff_factor=0.5, jitter=False, respect_retry_after=True)
    resp = FakeResponse(429, headers={"Retry-After": "7"})
    assert policy.next_delay(0, resp) == 7.0


def test_next_delay_falls_back_to_backoff():
    policy = RetryPolicy(backoff_factor=0.5, jitter=False)
    resp = FakeResponse(503, headers={})
    assert policy.next_delay(2, resp) == 2.0
    assert policy.next_delay(2, None) == 2.0


# -- from_config ------------------------------------------------------------
def test_from_config_maps_fields():
    config = Config(
        max_retries=7,
        backoff_factor=0.25,
        backoff_max=99,
        backoff_jitter=False,
        retry_statuses=[418],
        respect_retry_after=False,
        retry_after_max=42,
    )
    policy = RetryPolicy.from_config(config)
    assert policy.max_retries == 7
    assert policy.backoff_factor == 0.25
    assert policy.backoff_max == 99
    assert policy.jitter is False
    assert policy.retry_statuses == frozenset({418})
    assert policy.respect_retry_after is False
    assert policy.retry_after_max == 42
