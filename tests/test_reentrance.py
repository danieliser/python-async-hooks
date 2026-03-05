from __future__ import annotations

import pytest

from async_hooks import AsyncHooks


@pytest.fixture
def hooks() -> AsyncHooks:
    return AsyncHooks()


@pytest.mark.asyncio
async def test_action_callback_triggers_nested_action(hooks: AsyncHooks) -> None:
    events: list[str] = []

    async def inner() -> None:
        events.append("inner")

    async def outer() -> None:
        events.append("outer-start")
        await hooks.do_action("reentrance.inner")
        events.append("outer-end")

    hooks.add_action("reentrance.inner", inner)
    hooks.add_action("reentrance.outer", outer)

    await hooks.do_action("reentrance.outer")

    assert events == ["outer-start", "inner", "outer-end"]


@pytest.mark.asyncio
async def test_nested_action_levels_and_doing_action_states(hooks: AsyncHooks) -> None:
    events: list[str] = []
    state = {"level": 0}

    async def level_3() -> None:
        events.append(f"l3:{hooks.doing_action('reentrance.level_3')}")

    async def level_2() -> None:
        state["level"] += 1
        events.append(f"l2-enter:{hooks.doing_action('reentrance.level_2')}")
        await hooks.do_action("reentrance.level_3")
        events.append(f"l2-exit:{hooks.doing_action('reentrance.level_2')}")

    async def level_1() -> None:
        events.append(f"l1-enter:{hooks.doing_action('reentrance.level_1')}")
        await hooks.do_action("reentrance.level_2")
        events.append(f"l1-mid:{hooks.doing_action('reentrance.level_1')}")
        events.append(f"state:{state['level']}")
        events.append(f"l1-exit:{hooks.doing_action('reentrance.level_1')}")

    hooks.add_action("reentrance.level_1", level_1)
    hooks.add_action("reentrance.level_2", level_2)
    hooks.add_action("reentrance.level_3", level_3)

    await hooks.do_action("reentrance.level_1")

    assert hooks.doing_action("reentrance.level_1") is False
    assert hooks.doing_action("reentrance.level_2") is False
    assert hooks.doing_action("reentrance.level_3") is False

    assert events == [
        "l1-enter:True",
        "l2-enter:True",
        "l3:True",
        "l2-exit:True",
        "l1-mid:True",
        "state:1",
        "l1-exit:True",
    ]


@pytest.mark.asyncio
async def test_deferred_removals_cleanup_only_on_outermost_return(hooks: AsyncHooks) -> None:
    events: list[str] = []
    state = {"calls": 0}

    async def keep(value: int = 0) -> None:
        state["calls"] += 1
        if state["calls"] == 1:
            hooks.remove_action("reentrance.nested_same", removed_id)
            events.append(f"during:{hooks.has_action('reentrance.nested_same')}")
            await hooks.do_action("reentrance.nested_same", 1)
            events.append(f"after_nested:{hooks.has_action('reentrance.nested_same')}")
        else:
            events.append(f"nested:{hooks.has_action('reentrance.nested_same')}")

    async def removed() -> None:
        events.append("removed-ran")

    removed_id: str = hooks.add_action("reentrance.nested_same", removed, priority=20)
    hooks.add_action("reentrance.nested_same", keep, priority=10)

    await hooks.do_action("reentrance.nested_same")

    assert events == [
        "during:2",
        "nested:2",
        "after_nested:2",
    ]
    assert hooks.has_action("reentrance.nested_same") == 1


@pytest.mark.asyncio
async def test_deferred_removal_cleanup_after_nested_levels(hooks: AsyncHooks) -> None:
    events: list[str] = []
    ids: list[str] = []

    async def a() -> None:
        events.append("a1")
        if len(events) == 1:
            hooks.remove_action("reentrance.outer", ids[1])
            await hooks.do_action("reentrance.inner")
        events.append("a2")

    async def b() -> None:
        events.append("b")

    async def c() -> None:
        events.append("c")

    b_id = hooks.add_action("reentrance.outer", b, priority=20)
    c_id = hooks.add_action("reentrance.outer", c, priority=30)
    ids.extend([b_id, c_id])
    hooks.add_action("reentrance.outer", a, priority=10)
    hooks.add_action("reentrance.inner", c)

    await hooks.do_action("reentrance.outer")

    assert events == ["a1", "c", "a2", "b"]
    assert hooks.has_action("reentrance.outer") == 2
    assert hooks.has_action("reentrance.inner") == 1


@pytest.mark.asyncio
async def test_multiple_levels_of_nesting_for_actions(hooks: AsyncHooks) -> None:
    events: list[str] = []

    async def level0() -> None:
        events.append("l0")
        await hooks.do_action("reentrance.n0")

    async def level1() -> None:
        events.append("l1")
        await hooks.do_action("reentrance.n1")

    async def level2() -> None:
        events.append("l2")
        await hooks.do_action("reentrance.n2")

    async def level3() -> None:
        events.append("l3")

    hooks.add_action("reentrance.level0", level0, priority=10)
    hooks.add_action("reentrance.n0", level1, priority=10)
    hooks.add_action("reentrance.n1", level2, priority=10)
    hooks.add_action("reentrance.n2", level3, priority=10)

    await hooks.do_action("reentrance.level0")

    assert events == ["l0", "l1", "l2", "l3"]


@pytest.mark.asyncio
@pytest.mark.parametrize("hook_name", ["a", "b", "c", "d", "e"])
async def test_reentrant_no_deadlock_with_multiple_hook_fires(hooks: AsyncHooks, hook_name: str) -> None:
    events: list[str] = []

    async def cb_a() -> None:
        events.append("a")
        await hooks.do_action("reentrance.chain")

    async def cb_b() -> None:
        events.append("b")

    hooks.remove_action("reentrance.chain", "never")

    hooks.add_action(f"reentrance.{hook_name}", lambda: events.append(hook_name))
    await hooks.do_action(f"reentrance.{hook_name}")
    await hooks.do_action(f"reentrance.{hook_name}")

    hooks.add_action("reentrance.chain", cb_a)
    hooks.add_action("reentrance.chain", cb_b)
    await hooks.do_action("reentrance.chain")

    assert "a" in events and "b" in events


@pytest.mark.asyncio
async def test_callback_runs_even_when_parent_scope_is_not_current(hooks: AsyncHooks) -> None:
    events: list[str] = []

    async def child() -> None:
        events.append("child")

    async def parent() -> None:
        events.append("parent-before")
        await hooks.do_action("reentrance.child")
        events.append("parent-after")

    hooks.add_action("reentrance.parent", parent)
    hooks.add_action("reentrance.child", child)

    await hooks.do_action("reentrance.parent")
    assert events == ["parent-before", "child", "parent-after"]
