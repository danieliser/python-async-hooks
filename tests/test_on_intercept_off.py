"""Tests for issue #5 — on() / intercept() / off() ergonomic API."""

from __future__ import annotations

import pytest

from async_hooks import AsyncHooks


@pytest.fixture
def hooks() -> AsyncHooks:
    return AsyncHooks(action_timeout_seconds=0.05)


# ─ on() ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_on_registers_in_action_registry(hooks: AsyncHooks) -> None:
    async def handler() -> None:
        pass

    cid = hooks.on("evt.on", handler)
    assert hooks.has_action("evt.on", cid)
    assert not hooks.has_filter("evt.on", cid)


@pytest.mark.asyncio
async def test_on_fires_on_do_action(hooks: AsyncHooks) -> None:
    fired: list[str] = []

    async def handler() -> None:
        fired.append("fired")

    hooks.on("evt.action", handler)
    await hooks.do_action("evt.action")
    assert fired == ["fired"]


@pytest.mark.asyncio
async def test_on_does_not_fire_on_apply_filters(hooks: AsyncHooks) -> None:
    fired: list[str] = []

    async def handler(val):
        fired.append("fired")
        return val

    hooks.on("evt.filter_only", handler)
    result = await hooks.apply_filters("evt.filter_only", 42)
    assert fired == []
    assert result == 42


@pytest.mark.asyncio
async def test_on_return_value_is_ignored(hooks: AsyncHooks) -> None:
    async def handler():
        return "this should be ignored"

    hooks.on("evt.return", handler)
    # do_action returns None always
    result = await hooks.do_action("evt.return")
    assert result is None


@pytest.mark.asyncio
async def test_on_respects_priority(hooks: AsyncHooks) -> None:
    order: list[str] = []

    hooks.on("evt.priority", lambda: order.append("b"), priority=20)
    hooks.on("evt.priority", lambda: order.append("a"), priority=5)

    await hooks.do_action("evt.priority")
    assert order == ["a", "b"]


# ─ intercept() ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_intercept_registers_in_filter_registry(hooks: AsyncHooks) -> None:
    async def handler(val):
        return val

    cid = hooks.intercept("evt.intercept", handler)
    assert hooks.has_filter("evt.intercept", cid)
    assert not hooks.has_action("evt.intercept", cid)


@pytest.mark.asyncio
async def test_intercept_fires_on_apply_filters(hooks: AsyncHooks) -> None:
    async def double(val: int) -> int:
        return val * 2

    hooks.intercept("evt.double", double)
    result = await hooks.apply_filters("evt.double", 5)
    assert result == 10


@pytest.mark.asyncio
async def test_intercept_does_not_fire_on_do_action(hooks: AsyncHooks) -> None:
    fired: list[str] = []

    async def handler():
        fired.append("fired")

    hooks.intercept("evt.action_only", handler)
    await hooks.do_action("evt.action_only")
    assert fired == []


@pytest.mark.asyncio
async def test_intercept_chains_values(hooks: AsyncHooks) -> None:
    hooks.intercept("evt.chain", lambda v: v + 1, priority=10)
    hooks.intercept("evt.chain", lambda v: v * 3, priority=20)
    result = await hooks.apply_filters("evt.chain", 4)
    assert result == 15  # (4 + 1) * 3


# ─ off() ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_off_removes_action_registered_via_on(hooks: AsyncHooks) -> None:
    fired: list[str] = []

    async def handler():
        fired.append("fired")

    cid = hooks.on("evt.off_action", handler)
    hooks.off("evt.off_action", cid)

    await hooks.do_action("evt.off_action")
    assert fired == []


@pytest.mark.asyncio
async def test_off_removes_filter_registered_via_intercept(hooks: AsyncHooks) -> None:
    async def double(val: int) -> int:
        return val * 2

    cid = hooks.intercept("evt.off_filter", double)
    hooks.off("evt.off_filter", cid)

    result = await hooks.apply_filters("evt.off_filter", 5)
    assert result == 5  # passthrough, handler removed


@pytest.mark.asyncio
async def test_off_removes_action_registered_via_add_action(hooks: AsyncHooks) -> None:
    fired: list[str] = []

    async def handler():
        fired.append("fired")

    cid = hooks.add_action("evt.off_add_action", handler)
    hooks.off("evt.off_add_action", cid)

    await hooks.do_action("evt.off_add_action")
    assert fired == []


@pytest.mark.asyncio
async def test_off_removes_filter_registered_via_add_filter(hooks: AsyncHooks) -> None:
    async def double(val: int) -> int:
        return val * 2

    cid = hooks.add_filter("evt.off_add_filter", double)
    hooks.off("evt.off_add_filter", cid)

    result = await hooks.apply_filters("evt.off_add_filter", 5)
    assert result == 5


@pytest.mark.asyncio
async def test_off_returns_false_for_unknown_callback_id(hooks: AsyncHooks) -> None:
    assert hooks.off("evt.unknown", "nonexistent-id") is False


@pytest.mark.asyncio
async def test_off_deferred_during_action_execution(hooks: AsyncHooks) -> None:
    fired: list[str] = []
    cid_holder: list[str] = []

    async def self_removing():
        hooks.off("evt.deferred_off", cid_holder[0])
        fired.append("ran")

    cid = hooks.on("evt.deferred_off", self_removing)
    cid_holder.append(cid)

    await hooks.do_action("evt.deferred_off")
    assert fired == ["ran"]

    fired.clear()
    await hooks.do_action("evt.deferred_off")
    assert fired == []  # removed after first run
