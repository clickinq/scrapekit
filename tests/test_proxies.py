"""Tests for ProxyPool rotation, failure tracking, bans, and health checks."""

from __future__ import annotations

import pytest
import requests

from scrapekit import Config, Proxy, ProxyPool
from scrapekit.exceptions import NoHealthyProxiesError

from .conftest import FakeClock, FakeResponse


def make_pool(n=3, **kwargs):
    urls = [f"http://proxy{i}.test:8080" for i in range(n)]
    return ProxyPool(urls, **kwargs)


# -- construction -----------------------------------------------------------
def test_empty_pool_rejected():
    with pytest.raises(ValueError):
        ProxyPool([])


def test_bad_strategy_rejected():
    with pytest.raises(ValueError):
        ProxyPool(["http://p"], strategy="zigzag")


def test_accepts_proxy_objects_and_strings():
    pool = ProxyPool(["http://a", Proxy("http://b")])
    assert len(pool) == 2


def test_as_requests_dict():
    proxy = Proxy("http://user:pass@host:8080")
    assert proxy.as_requests_dict() == {
        "http": "http://user:pass@host:8080",
        "https": "http://user:pass@host:8080",
    }


# -- rotation ---------------------------------------------------------------
def test_round_robin_cycles_in_order():
    pool = make_pool(3, strategy="round_robin")
    seen = [pool.get().url for _ in range(6)]
    assert seen == [
        "http://proxy0.test:8080",
        "http://proxy1.test:8080",
        "http://proxy2.test:8080",
        "http://proxy0.test:8080",
        "http://proxy1.test:8080",
        "http://proxy2.test:8080",
    ]


def test_random_returns_pool_members():
    pool = make_pool(3, strategy="random")
    members = {p.url for p in pool.proxies}
    for _ in range(20):
        assert pool.get().url in members


def test_round_robin_skips_banned():
    clock = FakeClock()
    pool = make_pool(3, strategy="round_robin", max_failures=1, cooldown=100, clock=clock.time)
    # Ban the second proxy.
    second = pool.proxies[1]
    pool.mark_failure(second)
    assert not second.is_available(clock.time())
    # Over a full cycle the banned proxy is never returned.
    urls = {pool.get().url for _ in range(10)}
    assert second.url not in urls


# -- failure / ban / removal ------------------------------------------------
def test_failure_then_cooldown_then_recovery():
    clock = FakeClock()
    pool = make_pool(1, max_failures=2, cooldown=10, remove_after_bans=5, clock=clock.time)
    proxy = pool.proxies[0]

    pool.mark_failure(proxy)
    assert proxy.consecutive_failures == 1
    assert pool.healthy() == [proxy]  # one failure, not yet banned

    pool.mark_failure(proxy)  # hits max_failures -> cooldown
    assert proxy.ban_count == 1
    assert proxy.consecutive_failures == 0
    assert pool.healthy() == []  # cooling down

    clock.now += 11  # cooldown elapses
    assert pool.healthy() == [proxy]


def test_repeated_bans_remove_proxy():
    clock = FakeClock()
    pool = make_pool(1, max_failures=1, cooldown=5, remove_after_bans=2, clock=clock.time)
    proxy = pool.proxies[0]

    pool.mark_failure(proxy)  # ban #1
    assert proxy.ban_count == 1
    assert not proxy.dead

    clock.now += 6
    pool.mark_failure(proxy)  # ban #2 -> removal
    assert proxy.dead
    assert pool.healthy() == []


def test_mark_success_resets_state():
    pool = make_pool(1, max_failures=3)
    proxy = pool.proxies[0]
    pool.mark_failure(proxy)
    pool.mark_failure(proxy)
    pool.mark_success(proxy)
    assert proxy.consecutive_failures == 0
    assert proxy.banned_until == 0.0
    assert proxy.total_successes == 1


def test_get_raises_when_all_unavailable():
    clock = FakeClock()
    pool = make_pool(2, max_failures=1, cooldown=100, remove_after_bans=9, clock=clock.time)
    for proxy in pool.proxies:
        pool.mark_failure(proxy)
    with pytest.raises(NoHealthyProxiesError):
        pool.get()


# -- health check -----------------------------------------------------------
class HealthCheckSession:
    """Fake session: succeeds for listed proxy URLs, fails otherwise."""

    def __init__(self, good_urls, status=200):
        self.good_urls = set(good_urls)
        self.status = status

    def get(self, test_url, proxies=None, timeout=None):
        url = proxies["http"]
        if url in self.good_urls:
            return FakeResponse(self.status)
        raise requests.exceptions.ConnectionError("unreachable")


def test_health_check_marks_dead_and_reset():
    pool = make_pool(3)
    good = pool.proxies[0].url
    session = HealthCheckSession(good_urls=[good])

    healthy = pool.health_check("https://check.test/ip", session=session)

    assert healthy == 1
    alive = {p.url for p in pool.healthy()}
    assert alive == {good}
    for proxy in pool.proxies:
        if proxy.url != good:
            assert proxy.dead


def test_health_check_treats_5xx_as_unhealthy():
    pool = make_pool(2)
    session = HealthCheckSession(good_urls=[p.url for p in pool.proxies], status=500)
    healthy = pool.health_check("https://check.test", session=session)
    assert healthy == 0


# -- from_config ------------------------------------------------------------
def test_from_config_applies_proxy_settings():
    config = Config(proxy_strategy="random", proxy_max_failures=7, proxy_cooldown=42)
    pool = ProxyPool.from_config(["http://p"], config)
    assert pool.strategy == "random"
    assert pool.max_failures == 7
    assert pool.cooldown == 42
