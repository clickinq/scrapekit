"""Tests for the Config dataclass and its validation."""

from __future__ import annotations

import pytest

from scrapekit import Config
from scrapekit.exceptions import ConfigurationError


def test_defaults_are_sane():
    config = Config()
    assert config.max_retries == 3
    assert config.timeout == 30.0
    assert config.rate_limit is True
    assert config.min_delay == 1.0
    assert config.rotate_user_agent is True
    assert 429 in config.retry_statuses


def test_retry_statuses_normalized_to_frozenset():
    config = Config(retry_statuses=[500, 500, 502])
    assert isinstance(config.retry_statuses, frozenset)
    assert config.retry_statuses == frozenset({500, 502})


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_retries": -1},
        {"timeout": 0},
        {"timeout": -5},
        {"backoff_factor": -0.1},
        {"backoff_max": -1},
        {"retry_after_max": -1},
        {"min_delay": -0.5},
        {"requests_per_second": 0},
        {"requests_per_second": -2},
        {"rate_limit_burst": 0},
        {"proxy_strategy": "nope"},
        {"proxy_max_failures": 0},
        {"proxy_cooldown": -1},
        {"proxy_remove_after_bans": 0},
    ],
)
def test_invalid_config_raises(kwargs):
    with pytest.raises(ConfigurationError):
        Config(**kwargs)


def test_valid_overrides_accepted():
    config = Config(
        max_retries=5,
        requests_per_second=2.0,
        min_delay=None,
        proxy_strategy="random",
    )
    assert config.max_retries == 5
    assert config.requests_per_second == 2.0
    assert config.proxy_strategy == "random"
