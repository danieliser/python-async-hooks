from __future__ import annotations

import asyncio

import pytest

from async_hooks import AsyncHooks


@pytest.fixture
def hooks() -> AsyncHooks:
    return AsyncHooks()


def _sync_action(events: list[str], label: str) -> None:
    events.append(label)


async def _returning_coroutine(events: list[str], label: str) -> None:
    await asyncio.sleep(0)
    events.append(label)


@pytest.mark.asyncio
async def test_sync_action_auto_wrapped_and_works(hooks: AsyncHooks) -> None:
    events: list[str] = []
    hooks.add_action("sync_async.action", lambda: _sync_action(events, "sync"))

    await hooks.do_action("sync_async.action")

    assert events == ["sync"]


@pytest.mark.asyncio
async def test_async_action_works(hooks: AsyncHooks) -> None:
    events: list[str] = []

    async def cb() -> None:
        await asyncio.sleep(0)
        events.append("async")

    hooks.add_action("sync_async.action", cb)

    await hooks.do_action("sync_async.action")

    assert events == ["async"]


@pytest.mark.asyncio
async def test_mixed_sync_and_async_callbacks_in_action(hooks: AsyncHooks) -> None:
    events: list[str] = []

    hooks.add_action("sync_async.mix", lambda: _sync_action(events, "sync1"), priority=10)
    hooks.add_action("sync_async.mix", lambda: _sync_action(events, "sync2"), priority=20)

    async def async_cb() -> None:
        await asyncio.sleep(0)
        events.append("async")

    hooks.add_action("sync_async.mix", async_cb, priority=15)

    await hooks.do_action("sync_async.mix")

    assert events == ["sync1", "async", "sync2"]


@pytest.mark.asyncio
async def test_sync_action_returning_coroutine_is_awaited(hooks: AsyncHooks) -> None:
    events: list[str] = []

    def sync_returning_coro() -> asyncio.Task[None]:
        return asyncio.create_task(_returning_coroutine(events, "inner"))

    hooks.add_action("sync_async.awaitable", sync_returning_coro)

    await hooks.do_action("sync_async.awaitable")

    assert events == ["inner"]


@pytest.mark.asyncio
async def test_sync_filter_auto_wrapped_and_works(hooks: AsyncHooks) -> None:
    events: list[str] = []

    def sync_filter(value: str) -> str:
        events.append("sync")
        return f"{value}-sync"

    hooks.add_filter("sync_async.filter", sync_filter)

    result = await hooks.apply_filters("sync_async.filter", "start")

    assert result == "start-sync"
    assert events == ["sync"]


@pytest.mark.asyncio
async def test_async_filter_works(hooks: AsyncHooks) -> None:
    events: list[str] = []

    async def filt(value: str) -> str:
        await asyncio.sleep(0)
        events.append("async")
        return f"{value}-async"

    hooks.add_filter("sync_async.filter", filt)

    result = await hooks.apply_filters("sync_async.filter", "seed")

    assert result == "seed-async"
    assert events == ["async"]


@pytest.mark.asyncio
async def test_mixed_sync_async_filters(hooks: AsyncHooks) -> None:
    events: list[str] = []

    def sync1(value: str) -> str:
        events.append("sync1")
        return f"{value}-sync1"

    def sync2(value: str) -> str:
        events.append("sync2")
        return f"{value}-sync2"

    def sync3(value: str) -> str:
        events.append("sync3")
        return f"{value}-sync3"

    hooks.add_filter("sync_async.filter2", sync1)
    hooks.add_filter(
        "sync_async.filter2",
        sync2,
        priority=20,
    )
    hooks.add_filter(
        "sync_async.filter2",
        sync3,
        priority=30,
    )

    async def late(value: str) -> str:
        events.append("async")
        await asyncio.sleep(0)
        return f"{value}-async"

    hooks.add_filter("sync_async.filter2", late, priority=15)

    result = await hooks.apply_filters("sync_async.filter2", "seed")

    assert result == "seed-sync1-async-sync2-sync3"
    assert events == ["sync1", "async", "sync2", "sync3"]


@pytest.mark.asyncio
async def test_sync_filter_returning_coroutine_is_awaited(hooks: AsyncHooks) -> None:
    events: list[str] = []

    def sync_returning_coro_filter(value: str) -> asyncio.Task[str]:
        async def inner() -> str:
            await asyncio.sleep(0)
            events.append("inner")
            return f"{value}-inner"

        return asyncio.create_task(inner())

    hooks.add_filter("sync_async.filter3", sync_returning_coro_filter)
    result = await hooks.apply_filters("sync_async.filter3", "x")

    assert result == "x-inner"
    assert events == ["inner"]


@pytest.mark.asyncio
async def test_sync_action_can_use_positional_args(hooks: AsyncHooks) -> None:
    events: list[str] = []

    def sync_with_args(value: str, suffix: str) -> None:
        events.append(value + suffix)

    hooks.add_action("sync_async.args", sync_with_args)
    await hooks.do_action("sync_async.args", "v", "s")

    assert events == ["vs"]


@pytest.mark.asyncio
async def test_async_action_can_use_positional_args(hooks: AsyncHooks) -> None:
    events: list[str] = []

    async def async_with_args(x: int, y: int, z: int = 0) -> None:
        events.append(str(x + y + z))

    hooks.add_action("sync_async.args2", async_with_args)
    await hooks.do_action("sync_async.args2", 1, 2)

    assert events == ["3"]


@pytest.mark.asyncio
async def test_filter_chain_preserves_priority_order(hooks: AsyncHooks) -> None:
    events: list[str] = []
    seq = ["a", "b", "c", "d", "e", "f"]
    priorities = [30, 10, 50, 20, 40, 5]

    for label, priority in zip(seq, priorities):
        hooks.add_filter(
            "sync_async.order",
            lambda value, label=label: events.append(label) or f"{value}|{label}",
            priority=priority,
        )

    result = await hooks.apply_filters("sync_async.order", "start")
    assert events == ["f", "b", "d", "a", "e", "c"]
    assert result == "start|f|b|d|a|e|c"


@pytest.mark.asyncio
async def test_filter_can_return_none_and_continue(hooks: AsyncHooks) -> None:
    events: list[str] = []

    def first(value: str) -> str:
        events.append("first")
        return None  # type: ignore[return-value]

    def second(value: str | None) -> str:
        events.append("second")
        return "final" if value is None else f"{value}:second"

    hooks.add_filter("sync_async.none", first)
    hooks.add_filter("sync_async.none", second)

    result = await hooks.apply_filters("sync_async.none", "start")

    assert events == ["first", "second"]
    assert result == "final"


@pytest.mark.asyncio
async def test_filter_with_keyword_args(hooks: AsyncHooks) -> None:
    events: list[str] = []

    def first(value: str, suffix: str = "x") -> str:
        events.append(f"{value}:{suffix}")
        return f"{value}:{suffix}"

    async def second(value: str, suffix: str = "y") -> str:
        events.append(f"{value}:{suffix}")
        return f"{value}:{suffix}:a"

    hooks.add_filter("sync_async.kw", first, priority=10)
    hooks.add_filter("sync_async.kw", second, priority=20)

    result = await hooks.apply_filters("sync_async.kw", "seed", suffix="custom")
    assert result == "seed:custom:custom:a"
    assert events == ["seed:custom", "seed:custom:custom"]


@pytest.mark.asyncio
async def test_action_returns_value_is_ignored(hooks: AsyncHooks) -> None:
    events: list[str] = []

    def sync_returns_value() -> str:
        events.append("v")
        return "value"

    hooks.add_action("sync_async.ignored", sync_returns_value)
    await hooks.do_action("sync_async.ignored")
    assert events == ["v"]


@pytest.mark.asyncio
@pytest.mark.parametrize("sleep_ms", [0.0, 0.001, 0.002])
async def test_action_and_filter_mixed_with_optional_yields(hooks: AsyncHooks, sleep_ms: float) -> None:
    events: list[str] = []

    async def a() -> None:
        await asyncio.sleep(sleep_ms)
        events.append("a")

    def b() -> None:
        events.append("b")

    async def c(value: str) -> str:
        await asyncio.sleep(sleep_ms)
        events.append("c")
        return f"{value}-c"

    hooks.add_action("sync_async.mixed", a)
    hooks.add_action("sync_async.mixed", b, priority=20)
    hooks.add_filter("sync_async.mixed", c)

    await hooks.do_action("sync_async.mixed")
    result = await hooks.apply_filters("sync_async.mixed", "base")

    assert events == ["a", "b", "c"]
    assert result == "base-c"


@pytest.mark.asyncio
async def test_many_sync_and_async_actions_with_mixed_order(hooks: AsyncHooks) -> None:
    events: list[str] = []

    def s1() -> None:
        events.append("s1")

    async def s2() -> None:
        await asyncio.sleep(0)
        events.append("s2")

    def s3() -> None:
        events.append("s3")

    async def s4() -> None:
        await asyncio.sleep(0)
        events.append("s4")

    async def s5() -> None:
        await asyncio.sleep(0)
        events.append("s5")

    hooks.add_action("sync_async.extended", s1, priority=1)
    hooks.add_action("sync_async.extended", s2, priority=2)
    hooks.add_action("sync_async.extended", s3, priority=3)
    hooks.add_action("sync_async.extended", s4, priority=4)
    hooks.add_action("sync_async.extended", s5, priority=5)

    await hooks.do_action("sync_async.extended")

    assert events == ["s1", "s2", "s3", "s4", "s5"]
