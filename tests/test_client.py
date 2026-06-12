"""Integration tests for the Scraper request loop."""

from __future__ import annotations

import pytest
import requests

from scrapekit import Config, HookManager, ProxyPool, Scraper, UserAgentRotator
from scrapekit.exceptions import NoHealthyProxiesError, RetryExhaustedError
from scrapekit.headers import DEFAULT_USER_AGENTS

from .conftest import FakeResponse, FakeSession


def build_scraper(actions, config=None, **kwargs):
    """Build a Scraper over a FakeSession with sleeps recorded, not performed."""
    if config is None:
        config = Config(
            rate_limit=False,
            backoff_jitter=False,
            rotate_user_agent=False,
        )
    session = FakeSession(actions)
    scraper = Scraper(config, session=session, **kwargs)
    sleeps: list[float] = []
    scraper._sleep = sleeps.append  # type: ignore[assignment]
    return scraper, session, sleeps


# -- happy path -------------------------------------------------------------
def test_get_returns_response():
    scraper, session, _ = build_scraper([FakeResponse(200, text="ok")])
    resp = scraper.get("https://example.com")
    assert resp.status_code == 200
    assert session.calls[0]["method"] == "GET"
    assert session.calls[0]["url"] == "https://example.com"


def test_post_forwards_body():
    scraper, session, _ = build_scraper([FakeResponse(201)])
    resp = scraper.post("https://example.com/api", json={"a": 1})
    assert resp.status_code == 201
    assert session.calls[0]["method"] == "POST"
    assert session.calls[0]["json"] == {"a": 1}


def test_default_timeout_applied():
    scraper, session, _ = build_scraper(
        [FakeResponse(200)], config=Config(rate_limit=False, timeout=12.5, rotate_user_agent=False)
    )
    scraper.get("https://example.com")
    assert session.calls[0]["timeout"] == 12.5


# -- retry on status --------------------------------------------------------
def test_retries_on_retryable_status_then_succeeds():
    config = Config(rate_limit=False, backoff_jitter=False, rotate_user_agent=False, max_retries=3)
    scraper, session, sleeps = build_scraper(
        [FakeResponse(503), FakeResponse(200)], config=config
    )
    resp = scraper.get("https://example.com")
    assert resp.status_code == 200
    assert len(session.calls) == 2
    assert sleeps == [0.5]  # backoff_delay(0) with factor 0.5


def test_retry_exhausted_on_status_raises():
    config = Config(rate_limit=False, backoff_jitter=False, rotate_user_agent=False, max_retries=2)
    scraper, session, sleeps = build_scraper(
        [FakeResponse(503), FakeResponse(503), FakeResponse(503)], config=config
    )
    with pytest.raises(RetryExhaustedError) as excinfo:
        scraper.get("https://example.com")
    err = excinfo.value
    assert err.attempts == 3
    assert err.last_response is not None
    assert err.last_response.status_code == 503
    assert len(session.calls) == 3
    assert len(sleeps) == 2


def test_non_retryable_status_returned_not_raised():
    scraper, session, _ = build_scraper([FakeResponse(404)])
    resp = scraper.get("https://example.com")
    assert resp.status_code == 404
    assert len(session.calls) == 1


# -- retry on exception -----------------------------------------------------
def test_retries_on_connection_error_then_succeeds():
    config = Config(rate_limit=False, backoff_jitter=False, rotate_user_agent=False, max_retries=2)
    scraper, session, sleeps = build_scraper(
        [requests.exceptions.ConnectionError("boom"), FakeResponse(200)], config=config
    )
    resp = scraper.get("https://example.com")
    assert resp.status_code == 200
    assert len(sleeps) == 1


def test_retry_exhausted_on_exception_raises():
    config = Config(rate_limit=False, backoff_jitter=False, rotate_user_agent=False, max_retries=1)
    scraper, session, _ = build_scraper(
        [requests.exceptions.Timeout("t1"), requests.exceptions.Timeout("t2")], config=config
    )
    with pytest.raises(RetryExhaustedError) as excinfo:
        scraper.get("https://example.com")
    err = excinfo.value
    assert err.attempts == 2
    assert isinstance(err.last_exception, requests.exceptions.Timeout)


def test_non_retryable_exception_propagates():
    scraper, _, _ = build_scraper([ValueError("not a transport error")])
    with pytest.raises(ValueError):
        scraper.get("https://example.com")


# -- Retry-After ------------------------------------------------------------
def test_respects_retry_after_header():
    config = Config(
        rate_limit=False, backoff_jitter=False, rotate_user_agent=False,
        max_retries=3, respect_retry_after=True,
    )
    scraper, _, sleeps = build_scraper(
        [FakeResponse(429, headers={"Retry-After": "5"}), FakeResponse(200)], config=config
    )
    resp = scraper.get("https://example.com")
    assert resp.status_code == 200
    assert sleeps == [5.0]


# -- header rotation --------------------------------------------------------
def test_user_agent_rotation_sets_header():
    config = Config(rate_limit=False, rotate_user_agent=True)
    scraper, session, _ = build_scraper([FakeResponse(200)], config=config)
    scraper.get("https://example.com")
    assert session.calls[0]["headers"]["User-Agent"] in DEFAULT_USER_AGENTS


def test_default_and_per_request_headers_merge():
    config = Config(rate_limit=False, rotate_user_agent=False, default_headers={"X-A": "1"})
    scraper, session, _ = build_scraper([FakeResponse(200)], config=config)
    scraper.get("https://example.com", headers={"X-B": "2"})
    headers = session.calls[0]["headers"]
    assert headers["X-A"] == "1"
    assert headers["X-B"] == "2"


def test_custom_user_agent_rotator():
    config = Config(rate_limit=False, rotate_user_agent=True)
    rotator = UserAgentRotator(["only-agent"], strategy="round_robin")
    scraper, session, _ = build_scraper(
        [FakeResponse(200)], config=config, user_agent_rotator=rotator
    )
    scraper.get("https://example.com")
    assert session.calls[0]["headers"]["User-Agent"] == "only-agent"


# -- proxies ----------------------------------------------------------------
def test_proxy_used_and_marked_success():
    pool = ProxyPool(["http://p0:8080", "http://p1:8080"], strategy="round_robin")
    scraper, session, _ = build_scraper([FakeResponse(200)], proxy_pool=pool)
    scraper.get("https://example.com")
    assert session.calls[0]["proxies"] == {"http": "http://p0:8080", "https": "http://p0:8080"}
    assert pool.proxies[0].total_successes == 1


def test_proxy_failure_marks_and_rotates():
    config = Config(rate_limit=False, backoff_jitter=False, rotate_user_agent=False, max_retries=1)
    pool = ProxyPool(["http://p0:8080", "http://p1:8080"], strategy="round_robin", max_failures=5)
    scraper, session, _ = build_scraper(
        [requests.exceptions.ConnectionError("down"), FakeResponse(200)],
        config=config,
        proxy_pool=pool,
    )
    resp = scraper.get("https://example.com")
    assert resp.status_code == 200
    # First attempt used p0 (failed), second used p1 (succeeded).
    assert session.calls[0]["proxies"]["http"] == "http://p0:8080"
    assert session.calls[1]["proxies"]["http"] == "http://p1:8080"
    assert pool.proxies[0].total_failures == 1
    assert pool.proxies[1].total_successes == 1


def test_explicit_proxies_bypass_pool():
    pool = ProxyPool(["http://p0:8080"], strategy="round_robin")
    scraper, session, _ = build_scraper([FakeResponse(200)], proxy_pool=pool)
    scraper.get("https://example.com", proxies={"http": "http://manual:3128"})
    assert session.calls[0]["proxies"] == {"http": "http://manual:3128"}
    # Pool was not consulted.
    assert pool.proxies[0].total_successes == 0


def test_no_healthy_proxies_raises():
    pool = ProxyPool(["http://p0:8080"], strategy="round_robin")
    pool.mark_dead(pool.proxies[0])
    scraper, _, _ = build_scraper([FakeResponse(200)], proxy_pool=pool)
    with pytest.raises(NoHealthyProxiesError):
        scraper.get("https://example.com")


def test_cannot_pass_both_pool_and_proxies():
    pool = ProxyPool(["http://p0:8080"])
    with pytest.raises(ValueError):
        Scraper(Config(), proxy_pool=pool, proxies=["http://p1:8080"])


# -- hooks ------------------------------------------------------------------
def test_pre_request_hook_adds_header():
    hooks = HookManager()

    @hooks.pre_request
    def add(ctx):
        ctx.headers["X-Hook"] = "yes"

    scraper, session, _ = build_scraper([FakeResponse(200)], hooks=hooks)
    scraper.get("https://example.com")
    assert session.calls[0]["headers"]["X-Hook"] == "yes"


def test_pre_request_hook_can_short_circuit():
    hooks = HookManager()
    cached = FakeResponse(200, text="cached")

    @hooks.pre_request
    def serve(ctx):
        return cached

    scraper, session, _ = build_scraper([], hooks=hooks)  # no scripted network calls
    resp = scraper.get("https://example.com")
    assert resp is cached
    assert session.calls == []  # network never touched


def test_post_response_hook_observes_response():
    hooks = HookManager()
    seen = []

    @hooks.post_response
    def record(resp, ctx):
        seen.append((resp.status_code, ctx.url))

    scraper, _, _ = build_scraper([FakeResponse(200)], hooks=hooks)
    scraper.get("https://example.com")
    assert seen == [(200, "https://example.com")]


# -- verb methods & construction -------------------------------------------
@pytest.mark.parametrize("verb", ["get", "post", "put", "patch", "delete", "head", "options"])
def test_all_verb_methods(verb):
    scraper, session, _ = build_scraper([FakeResponse(200)])
    getattr(scraper, verb)("https://example.com")
    assert session.calls[0]["method"] == verb.upper()


def test_constructing_with_proxies_builds_pool():
    config = Config(rate_limit=False, rotate_user_agent=False, proxy_strategy="round_robin")
    session = FakeSession([FakeResponse(200)])
    scraper = Scraper(config, proxies=["http://p0:8080"], session=session)
    assert scraper.proxy_pool is not None
    assert len(scraper.proxy_pool) == 1
    scraper.get("https://example.com")
    assert session.calls[0]["proxies"]["http"] == "http://p0:8080"


def test_randomize_headers_adds_accept_language():
    config = Config(rate_limit=False, rotate_user_agent=False, randomize_headers=True)
    scraper, session, _ = build_scraper([FakeResponse(200)], config=config)
    scraper.get("https://example.com")
    assert "Accept-Language" in session.calls[0]["headers"]


# -- context manager --------------------------------------------------------
def test_context_manager_closes_session():
    session = FakeSession([FakeResponse(200)])
    with Scraper(Config(rate_limit=False), session=session) as scraper:
        scraper._sleep = lambda s: None  # type: ignore[assignment]
        scraper.get("https://example.com")
    assert session.closed is True
