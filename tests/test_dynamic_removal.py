from __future__ import annotations

import pytest

from async_hooks import AsyncHooks


@pytest.fixture
def hooks() -> AsyncHooks:
    return AsyncHooks()


@pytest.mark.asyncio
async def test_callback_unhooks_itself_during_execution(hooks: AsyncHooks) -> None:
    events: list[str] = []
    self_id: str = ""

    async def self_removing() -> None:
        events.append("self")
        hooks.remove_action("dynamic.self", self_id)

    self_id = hooks.add_action("dynamic.self", self_removing)
    hooks.add_action("dynamic.self", lambda: events.append("other"), priority=20)

    await hooks.do_action("dynamic.self")
    await hooks.do_action("dynamic.self")

    assert events == ["self", "other", "other"]
    assert hooks.has_action("dynamic.self") == 1


@pytest.mark.asyncio
async def test_callback_unhooks_other_during_execution(hooks: AsyncHooks) -> None:
    events: list[str] = []
    other_id: str = ""

    async def remover() -> None:
        events.append("remover")
        hooks.remove_action("dynamic.other", other_id)

    async def other() -> None:
        events.append("other")

    async def third() -> None:
        events.append("third")

    other_id = hooks.add_action("dynamic.other", other, priority=20)
    hooks.add_action("dynamic.other", remover, priority=10)
    hooks.add_action("dynamic.other", third, priority=30)

    await hooks.do_action("dynamic.other")

    assert events == ["remover", "third"]
    assert hooks.has_action("dynamic.other", other_id) is False


@pytest.mark.asyncio
async def test_callback_unhooks_other_by_priority_during_execution(hooks: AsyncHooks) -> None:
    events: list[str] = []
    async def remover() -> None:
        events.append("remover")
        hooks.remove_action("dynamic.by_priority", removee_id)

    async def removee() -> None:
        events.append("removee")

    async def keep() -> None:
        events.append("keep")

    removee_id = hooks.add_action("dynamic.by_priority", removee, priority=20)
    hooks.add_action("dynamic.by_priority", remover, priority=5)
    hooks.add_action("dynamic.by_priority", keep, priority=10)

    await hooks.do_action("dynamic.by_priority")
    assert events == ["remover", "keep"]
    assert hooks.has_action("dynamic.by_priority") == 2


@pytest.mark.asyncio
async def test_adding_callback_during_execution_does_not_run_same_cycle(hooks: AsyncHooks) -> None:
    events: list[str] = []

    async def add_later() -> None:
        events.append("adder")

        def late() -> None:
            events.append("late")

        hooks.add_action("dynamic.add", late, priority=10)

    hooks.add_action("dynamic.add", add_later, priority=10)
    await hooks.do_action("dynamic.add")

    assert events == ["adder"]
    assert hooks.has_action("dynamic.add") == 2

    await hooks.do_action("dynamic.add")
    assert events == ["adder", "adder", "late"]


@pytest.mark.asyncio
async def test_filter_can_unhook_itself_during_execution(hooks: AsyncHooks) -> None:
    events: list[str] = []
    self_id: str = ""

    def self_filter(value: str) -> str:
        events.append("self")
        hooks.remove_filter("dynamic.filter_self", self_id)
        return f"{value}-self"

    self_id = hooks.add_filter("dynamic.filter_self", self_filter)
    hooks.add_filter("dynamic.filter_self", lambda value: (events.append("other") or f"{value}-other"))

    result = await hooks.apply_filters("dynamic.filter_self", "v")
    assert result == "v-self-other"
    assert events == ["self", "other"]

    result2 = await hooks.apply_filters("dynamic.filter_self", "x")
    assert result2 == "x-other"
    assert events == ["self", "other", "other"]


@pytest.mark.asyncio
async def test_filter_can_unhook_other_during_execution(hooks: AsyncHooks) -> None:
    events: list[str] = []
    removee_id: str = ""

    def removee(value: str) -> str:
        events.append("removee")
        return f"{value}-removee"

    def remover(value: str) -> str:
        hooks.remove_filter("dynamic.filter_other", removee_id)
        events.append("remover")
        return f"{value}-remover"

    def late(value: str) -> str:
        events.append("late")
        return f"{value}-late"

    removee_id = hooks.add_filter("dynamic.filter_other", removee, priority=20)
    hooks.add_filter("dynamic.filter_other", remover, priority=10)
    hooks.add_filter("dynamic.filter_other", late, priority=30)

    result = await hooks.apply_filters("dynamic.filter_other", "seed")
    assert result == "seed-remover-late"
    assert events == ["remover", "late"]
    assert hooks.has_filter("dynamic.filter_other", removee_id) is False


@pytest.mark.asyncio
async def test_add_filter_during_filter_execution_does_not_run_in_same_chain(hooks: AsyncHooks) -> None:
    events: list[str] = []

    def first(value: str) -> str:
        events.append("first")

        def second(value: str) -> str:
            events.append("second")
            return f"{value}-second"

        hooks.add_filter("dynamic.filter_add", second)
        return f"{value}-first"

    hooks.add_filter("dynamic.filter_add", first)
    result1 = await hooks.apply_filters("dynamic.filter_add", "seed")

    assert result1 == "seed-first"
    assert events == ["first"]

    result2 = await hooks.apply_filters("dynamic.filter_add", "seed")
    assert result2 == "seed-first-second"


@pytest.mark.asyncio
async def test_remove_all_actions_during_execution_removes_all_after_cleanup(hooks: AsyncHooks) -> None:
    events: list[str] = []

    async def first() -> None:
        events.append("first")
        hooks.remove_all_actions("dynamic.remove_all_nested")

    async def second() -> None:
        events.append("second")

    hooks.add_action("dynamic.remove_all_nested", first, priority=5)
    hooks.add_action("dynamic.remove_all_nested", second, priority=10)

    await hooks.do_action("dynamic.remove_all_nested")
    assert events == ["first"]
    assert hooks.has_action("dynamic.remove_all_nested") == 0


@pytest.mark.asyncio
async def test_remove_all_filters_during_execution_removes_all_after_cleanup(hooks: AsyncHooks) -> None:
    events: list[str] = []

    def first(value: str) -> str:
        events.append("first")
        hooks.remove_all_filters("dynamic.remove_all_filters_nested")
        return f"{value}-first"

    def second(value: str) -> str:
        events.append("second")
        return f"{value}-second"

    hooks.add_filter("dynamic.remove_all_filters_nested", first, priority=5)
    hooks.add_filter("dynamic.remove_all_filters_nested", second, priority=10)

    result = await hooks.apply_filters("dynamic.remove_all_filters_nested", "seed")
    assert result == "seed-first"
    assert events == ["first"]
    assert hooks.has_filter("dynamic.remove_all_filters_nested") == 0


@pytest.mark.asyncio
async def test_dynamic_add_and_remove_multiple_calls(hooks: AsyncHooks) -> None:
    marker: list[str] = []

    async def adder() -> None:
        marker.append("adder")
        callback_id = hooks.add_action("dynamic.multi", lambda: marker.append("dynamic"), priority=5)
        hooks.remove_action("dynamic.multi", callback_id)

    hooks.add_action("dynamic.multi", adder)

    await hooks.do_action("dynamic.multi")
    await hooks.do_action("dynamic.multi")

    assert marker == ["adder", "adder"]
    assert hooks.has_action("dynamic.multi") == 1


@pytest.mark.asyncio
async def test_deferred_filter_removal_from_nested_filter_call(hooks: AsyncHooks) -> None:
    events: list[str] = []
    target_id: str = ""

    def first(value: str, depth: int = 0) -> str:
        events.append(f"first-{depth}")
        if depth < 1:
            hooks.remove_filter("dynamic.filter_nest", target_id)
        return value

    def target(value: str) -> str:
        events.append("target")
        return f"{value}-target"

    async def second(value: str, depth: int = 0) -> str:
        events.append(f"second-{depth}")
        if depth >= 1:
            return f"{value}-second"
        return await hooks.apply_filters("dynamic.filter_nest", f"{value}-inner", depth + 1)

    target_id = hooks.add_filter("dynamic.filter_nest", target, priority=5)
    hooks.add_filter(
        "dynamic.filter_nest",
        first,
        priority=10,
        accepted_args=2,
    )
    hooks.add_filter(
        "dynamic.filter_nest",
        second,
        priority=20,
        accepted_args=2,
    )

    result = await hooks.apply_filters("dynamic.filter_nest", "seed", 0)
    assert result == "seed-target-inner-second"
    assert events == ["target", "first-0", "second-0", "first-1", "second-1"]
    assert hooks.has_filter("dynamic.filter_nest", target_id) is False
