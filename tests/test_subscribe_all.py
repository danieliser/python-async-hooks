"""Tests for issue #4 — subscribe_all() / unsubscribe_all() wildcard subscriptions."""

from __future__ import annotations

import pytest

from async_hooks import AsyncHooks


@pytest.fixture
def hooks() -> AsyncHooks:
    return AsyncHooks(action_timeout_seconds=0.1)


# ─ subscribe_all() basics ────────────────────────────────────────────────────

def test_subscribe_all_returns_callback_id(hooks: AsyncHooks) -> None:
    cid = hooks.subscribe_all(lambda name: None)
    assert isinstance(cid, str)
    assert len(cid) > 0


def test_has_global_true_after_subscribe(hooks: AsyncHooks) -> None:
    cid = hooks.subscribe_all(lambda name: None)
    assert hooks.has_global(cid) is True


def test_has_global_false_for_unknown(hooks: AsyncHooks) -> None:
    assert hooks.has_global("nonexistent") is False


# ─ fires on do_action ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_global_fires_on_do_action(hooks: AsyncHooks) -> None:
    captured: list[str] = []

    async def global_handler(event_name: str, *args, **kwargs) -> None:
        captured.append(event_name)

    hooks.subscribe_all(global_handler)
    await hooks.do_action("task.created")
    assert captured == ["task.created"]


@pytest.mark.asyncio
async def test_global_fires_for_every_event(hooks: AsyncHooks) -> None:
    captured: list[str] = []

    async def global_handler(event_name: str, *args, **kwargs) -> None:
        captured.append(event_name)

    hooks.subscribe_all(global_handler)
    await hooks.do_action("task.created")
    await hooks.do_action("task.completed")
    await hooks.do_action("config.changed")
    assert set(captured) == {"task.created", "task.completed", "config.changed"}


@pytest.mark.asyncio
async def test_global_receives_event_name_as_first_arg(hooks: AsyncHooks) -> None:
    received: list = []

    async def global_handler(event_name: str, *args, **kwargs) -> None:
        received.append((event_name, args, kwargs))

    hooks.subscribe_all(global_handler)
    await hooks.do_action("my.event", "payload", key="val")
    assert received == [("my.event", ("payload",), {"key": "val"})]


# ─ fires on apply_filters ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_global_fires_on_apply_filters(hooks: AsyncHooks) -> None:
    captured: list[str] = []

    async def global_handler(event_name: str, *args, **kwargs) -> None:
        captured.append(event_name)

    hooks.subscribe_all(global_handler)
    await hooks.apply_filters("task.payload", {"id": 1})
    assert captured == ["task.payload"]


@pytest.mark.asyncio
async def test_global_filter_receives_final_value(hooks: AsyncHooks) -> None:
    received_value: list = []

    async def global_handler(event_name: str, value, *args, **kwargs) -> None:
        received_value.append(value)

    async def doubler(val: int) -> int:
        return val * 2

    hooks.add_filter("num.filter", doubler)
    hooks.subscribe_all(global_handler)

    await hooks.apply_filters("num.filter", 5)
    # Global handler sees the post-chain value (10), not the original (5)
    assert received_value == [10]


@pytest.mark.asyncio
async def test_global_return_value_ignored_in_filter_chain(hooks: AsyncHooks) -> None:
    async def global_handler(event_name: str, value, *args, **kwargs) -> int:
        return 9999  # should be ignored

    hooks.subscribe_all(global_handler)
    result = await hooks.apply_filters("passthrough", 42)
    assert result == 42


# ─ fires after name-specific handlers ────────────────────────────────────────

@pytest.mark.asyncio
async def test_global_fires_after_specific_handlers(hooks: AsyncHooks) -> None:
    order: list[str] = []

    async def specific() -> None:
        order.append("specific")

    async def global_handler(event_name: str, *args, **kwargs) -> None:
        order.append("global")

    hooks.add_action("evt.order", specific, priority=10)
    hooks.subscribe_all(global_handler, priority=90)

    await hooks.do_action("evt.order")
    assert order == ["specific", "global"]


@pytest.mark.asyncio
async def test_global_fires_even_with_no_specific_handlers(hooks: AsyncHooks) -> None:
    captured: list[str] = []

    async def global_handler(event_name: str, *args, **kwargs) -> None:
        captured.append(event_name)

    hooks.subscribe_all(global_handler)
    await hooks.do_action("evt.no_specific_handlers")
    assert captured == ["evt.no_specific_handlers"]


# ─ priority among global handlers ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_global_handlers_respect_priority(hooks: AsyncHooks) -> None:
    order: list[str] = []

    hooks.subscribe_all(lambda name, *a, **k: order.append("second"), priority=50)
    hooks.subscribe_all(lambda name, *a, **k: order.append("first"), priority=10)

    await hooks.do_action("any.event")
    assert order == ["first", "second"]


# ─ unsubscribe_all() ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unsubscribe_all_removes_handler(hooks: AsyncHooks) -> None:
    captured: list[str] = []

    async def global_handler(event_name: str, *args, **kwargs) -> None:
        captured.append(event_name)

    cid = hooks.subscribe_all(global_handler)
    hooks.unsubscribe_all(cid)

    await hooks.do_action("evt.after_unsub")
    assert captured == []


def test_unsubscribe_all_returns_false_for_unknown(hooks: AsyncHooks) -> None:
    assert hooks.unsubscribe_all("nonexistent") is False


def test_unsubscribe_all_returns_false_for_action_callback(hooks: AsyncHooks) -> None:
    cid = hooks.add_action("evt", lambda: None)
    assert hooks.unsubscribe_all(cid) is False


@pytest.mark.asyncio
async def test_has_global_false_after_unsubscribe(hooks: AsyncHooks) -> None:
    cid = hooks.subscribe_all(lambda name: None)
    hooks.unsubscribe_all(cid)
    assert hooks.has_global(cid) is False


@pytest.mark.asyncio
async def test_unsubscribe_all_deferred_during_execution(hooks: AsyncHooks) -> None:
    fired: list[str] = []
    cid_holder: list[str] = []

    async def self_removing(event_name: str, *args, **kwargs) -> None:
        hooks.unsubscribe_all(cid_holder[0])
        fired.append("ran")

    cid = hooks.subscribe_all(self_removing)
    cid_holder.append(cid)

    await hooks.do_action("evt.self_remove")
    assert fired == ["ran"]

    fired.clear()
    await hooks.do_action("evt.self_remove")
    assert fired == []  # removed after first run


# ─ error isolation ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_global_handler_exception_does_not_break_chain(hooks: AsyncHooks) -> None:
    second_fired: list[bool] = []

    async def bad_handler(event_name: str, *args, **kwargs) -> None:
        raise RuntimeError("boom")

    async def good_handler(event_name: str, *args, **kwargs) -> None:
        second_fired.append(True)

    hooks.subscribe_all(bad_handler, priority=10)
    hooks.subscribe_all(good_handler, priority=20)

    await hooks.do_action("evt.error")
    assert second_fired == [True]


# ─ zero overhead ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_global_hooks_no_overhead(hooks: AsyncHooks) -> None:
    # Smoke test: no global handlers, normal execution is unaffected
    fired: list[str] = []
    hooks.add_action("evt.no_global", lambda: fired.append("ok"))
    await hooks.do_action("evt.no_global")
    assert fired == ["ok"]
