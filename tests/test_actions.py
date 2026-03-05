from __future__ import annotations

import asyncio
import logging

import pytest

from async_hooks import AsyncHooks


@pytest.fixture
def hooks() -> AsyncHooks:
    return AsyncHooks(action_timeout_seconds=0.05, filter_timeout_seconds=None)


def _make_sync_action(label: str, events: list[str]) -> None:
    events.append(label)


@pytest.mark.asyncio
async def test_add_action_returns_unique_callback_id(hooks: AsyncHooks) -> None:
    first = hooks.add_action("actions.unique", lambda: _make_sync_action("a", []))
    second = hooks.add_action("actions.unique", lambda: _make_sync_action("b", []))

    assert isinstance(first, str)
    assert isinstance(second, str)
    assert first != second


@pytest.mark.asyncio
async def test_add_and_do_action_priority_order(hooks: AsyncHooks) -> None:
    events: list[str] = []

    async def high() -> None:
        events.append("high")

    async def medium() -> None:
        events.append("medium")

    async def low() -> None:
        events.append("low")

    hooks.add_action("actions.priority", low, priority=20)
    hooks.add_action("actions.priority", high, priority=5)
    hooks.add_action("actions.priority", medium, priority=10)

    await hooks.do_action("actions.priority")

    assert events == ["high", "medium", "low"]


@pytest.mark.asyncio
async def test_do_action_no_callbacks_is_noop(hooks: AsyncHooks) -> None:
    await hooks.do_action("actions.noop")
    assert hooks.did_action("actions.noop") == 0


@pytest.mark.asyncio
async def test_multiple_callbacks_same_priority_run_in_insertion_order(hooks: AsyncHooks) -> None:
    events: list[str] = []
    hooks.add_action("actions.same_priority", lambda: _make_sync_action("first", events), priority=10)
    hooks.add_action("actions.same_priority", lambda: _make_sync_action("second", events), priority=10)
    hooks.add_action("actions.same_priority", lambda: _make_sync_action("third", events), priority=10)

    await hooks.do_action("actions.same_priority")

    assert events == ["first", "second", "third"]


@pytest.mark.asyncio
async def test_remove_action_removes_callback(hooks: AsyncHooks) -> None:
    events: list[str] = []
    callback_id = hooks.add_action("actions.remove", lambda: _make_sync_action("kept", events))
    hooks.remove_action("actions.remove", callback_id)

    await hooks.do_action("actions.remove")

    assert events == []
    assert hooks.has_action("actions.remove") == 0
    assert hooks.has_action("actions.remove", callback_id) is False


@pytest.mark.asyncio
async def test_remove_action_during_execution_is_deferred(hooks: AsyncHooks) -> None:
    events: list[str] = []
    second_id: str = ""

    async def first() -> None:
        nonlocal second_id
        events.append("first")
        hooks.remove_action("actions.deferred_remove", second_id)

    async def second() -> None:
        events.append("second")

    async def third() -> None:
        events.append("third")

    second_id = hooks.add_action("actions.deferred_remove", second, priority=20)
    hooks.add_action("actions.deferred_remove", first, priority=10)
    hooks.add_action("actions.deferred_remove", third, priority=30)

    await hooks.do_action("actions.deferred_remove")

    assert events == ["first", "third"]
    assert hooks.has_action("actions.deferred_remove", second_id) is False
    assert hooks.has_action("actions.deferred_remove") == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("priority", [5, 15, 25])
async def test_remove_all_actions_by_priority(hooks: AsyncHooks, priority: int) -> None:
    events: list[str] = []
    hooks.add_action("actions.remove_all_priority", lambda: _make_sync_action("p5-a", events), priority=5)
    hooks.add_action("actions.remove_all_priority", lambda: _make_sync_action("p5-b", events), priority=5)
    hooks.add_action("actions.remove_all_priority", lambda: _make_sync_action("p15", events), priority=15)
    hooks.add_action("actions.remove_all_priority", lambda: _make_sync_action("p25", events), priority=25)

    hooks.remove_all_actions("actions.remove_all_priority", priority=priority)
    await hooks.do_action("actions.remove_all_priority")

    if priority == 5:
        assert events == ["p15", "p25"]
        assert hooks.has_action("actions.remove_all_priority") == 2
    elif priority == 15:
        assert events == ["p5-a", "p5-b", "p25"]
        assert hooks.has_action("actions.remove_all_priority") == 3
    else:
        assert events == ["p5-a", "p5-b", "p15"]
        assert hooks.has_action("actions.remove_all_priority") == 3


@pytest.mark.asyncio
async def test_remove_all_actions_all_priorities_clears_all(hooks: AsyncHooks) -> None:
    hooks.add_action("actions.remove_all_all", lambda: None, priority=1)
    hooks.add_action("actions.remove_all_all", lambda: None, priority=10)
    hooks.add_action("actions.remove_all_all", lambda: None, priority=20)

    removed = hooks.remove_all_actions("actions.remove_all_all")
    assert removed is True
    assert hooks.has_action("actions.remove_all_all") == 0

    await hooks.do_action("actions.remove_all_all")


@pytest.mark.asyncio
async def test_has_action_returns_true_false_or_count(hooks: AsyncHooks) -> None:
    first_id = hooks.add_action("actions.count", lambda: None, priority=10)
    second_id = hooks.add_action("actions.count", lambda: None, priority=10)

    assert hooks.has_action("actions.count") == 2
    assert hooks.has_action("actions.count", first_id) is True
    assert hooks.has_action("actions.count", "missing") is False

    hooks.remove_action("actions.count", first_id)

    assert hooks.has_action("actions.count") == 1
    assert hooks.has_action("actions.count", first_id) is False
    assert hooks.has_action("actions.count", second_id) is True


@pytest.mark.asyncio
async def test_doing_action_true_during_execution(hooks: AsyncHooks) -> None:
    marker: list[bool] = []

    async def action() -> None:
        marker.append(hooks.doing_action("actions.during"))

    hooks.add_action("actions.during", action)
    assert hooks.doing_action("actions.during") is False

    await hooks.do_action("actions.during")

    assert marker == [True]
    assert hooks.doing_action("actions.during") is False


@pytest.mark.asyncio
async def test_did_action_invocation_count(hooks: AsyncHooks) -> None:
    hooks.add_action("actions.counts", lambda: None)
    hooks.add_action("actions.counts", lambda: None)

    assert hooks.did_action("actions.counts") == 0
    await hooks.do_action("actions.counts")
    await hooks.do_action("actions.counts", 1, 2, key="value")

    assert hooks.did_action("actions.counts") == 2


@pytest.mark.asyncio
async def test_action_timeout_logs_and_skips(hooks: AsyncHooks, caplog: pytest.LogCaptureFixture) -> None:
    events: list[str] = []

    async def slow() -> None:
        await asyncio.sleep(0.02)
        events.append("should_not_happen")

    hooks.add_action("actions.timeout", slow, timeout_seconds=0.001, priority=10)

    with caplog.at_level(logging.WARNING):
        await hooks.do_action("actions.timeout")

    assert events == []
    assert any(
        record.levelname == "WARNING" and "do_action timeout" in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_action_exception_is_logged_and_chain_continues(hooks: AsyncHooks, caplog: pytest.LogCaptureFixture) -> None:
    events: list[str] = []

    async def bad() -> None:
        raise RuntimeError("boom")

    async def good() -> None:
        events.append("good")

    hooks.add_action("actions.exception", bad, priority=10)
    hooks.add_action("actions.exception", good, priority=20)

    with caplog.at_level(logging.ERROR):
        await hooks.do_action("actions.exception")

    assert events == ["good"]
    assert any(
        record.levelname == "ERROR" and "do_action exception" in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "args",
    [
        ((),),
        ((1,),),
        ((1, 2),),
        ((1, 2, 3),),
        (("x", "y", "z"),),
    ],
)
async def test_do_action_accepts_variadic_args(hooks: AsyncHooks, args: tuple[object, ...]) -> None:
    received: list[tuple[object, ...]] = []

    async def action(*callback_args: object) -> None:
        received.append(callback_args)

    hooks.add_action("actions.varargs", action)
    await hooks.do_action("actions.varargs", *args)

    assert received == [args]


@pytest.mark.asyncio
async def test_adding_action_with_invalid_hook_name_fails(hooks: AsyncHooks) -> None:
    with pytest.raises(ValueError):
        hooks.add_action("", lambda: None)

    with pytest.raises(ValueError):
        hooks.add_action(123, lambda: None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_removing_missing_action_returns_false(hooks: AsyncHooks) -> None:
    assert hooks.remove_action("actions.missing", "not-there") is False
    assert hooks.remove_all_actions("actions.missing") is False


@pytest.mark.asyncio
async def test_action_chain_skips_callback_removed_early(hooks: AsyncHooks) -> None:
    events: list[str] = []

    first_id: str = ""

    async def first() -> None:
        events.append("first")
        hooks.remove_action("actions.early", first_id)

    async def second() -> None:
        events.append("second")

    first_id = hooks.add_action("actions.early", first)
    hooks.add_action("actions.early", second, priority=20)

    await hooks.do_action("actions.early")
    assert events == ["first", "second"]
    assert hooks.has_action("actions.early") == 1


@pytest.mark.asyncio
async def test_action_reusing_same_hook_name_is_isolated(hooks: AsyncHooks) -> None:
    events: list[str] = []
    hooks.add_action("actions.reuse", lambda: _make_sync_action("a1", events))
    await hooks.do_action("actions.reuse")
    hooks.remove_all_actions("actions.reuse")

    assert events == ["a1"]
    assert hooks.has_action("actions.reuse") == 0


@pytest.mark.asyncio
async def test_remove_all_actions_noop_for_empty_hook(hooks: AsyncHooks) -> None:
    assert hooks.remove_all_actions("actions.empty") is False


@pytest.mark.asyncio
async def test_doing_action_false_when_not_running(hooks: AsyncHooks) -> None:
    assert hooks.doing_action("never") is False
    hooks.add_action("never", lambda: None)
    assert hooks.doing_action("never") is False
