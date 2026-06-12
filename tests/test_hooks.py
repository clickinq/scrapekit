"""Tests for the hook system."""

from __future__ import annotations

from scrapekit import HookManager, RequestContext

from .conftest import FakeResponse


def make_context(**kwargs):
    base = dict(method="GET", url="https://example.com", headers={}, kwargs={})
    base.update(kwargs)
    return RequestContext(**base)


def test_pre_request_can_mutate_headers():
    hooks = HookManager()

    @hooks.pre_request
    def add_header(ctx):
        ctx.headers["X-Test"] = "1"

    ctx = make_context()
    assert hooks.run_pre_request(ctx) is None
    assert ctx.headers["X-Test"] == "1"


def test_pre_request_short_circuit_returns_response():
    hooks = HookManager()
    cached = FakeResponse(200, text="from cache")

    @hooks.pre_request
    def serve_cache(ctx):
        return cached

    @hooks.pre_request
    def should_not_run(ctx):  # pragma: no cover - must be skipped
        raise AssertionError("later hooks should not run after short-circuit")

    result = hooks.run_pre_request(make_context())
    assert result is cached


def test_post_response_can_replace_response():
    hooks = HookManager()
    replacement = FakeResponse(299)

    @hooks.post_response
    def swap(resp, ctx):
        return replacement

    original = FakeResponse(200)
    result = hooks.run_post_response(original, make_context())
    assert result is replacement


def test_post_response_none_leaves_response_unchanged():
    hooks = HookManager()
    calls = []

    @hooks.post_response
    def observe(resp, ctx):
        calls.append(resp.status_code)
        return None

    original = FakeResponse(200)
    result = hooks.run_post_response(original, make_context())
    assert result is original
    assert calls == [200]


def test_hooks_run_in_registration_order():
    hooks = HookManager()
    order = []

    @hooks.pre_request
    def first(ctx):
        order.append("first")

    @hooks.pre_request
    def second(ctx):
        order.append("second")

    hooks.run_pre_request(make_context())
    assert order == ["first", "second"]


def test_hook_registries_are_copies():
    hooks = HookManager()
    hooks.pre_request(lambda ctx: None)
    snapshot = hooks.pre_request_hooks
    snapshot.clear()
    assert len(hooks.pre_request_hooks) == 1
