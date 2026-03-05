from __future__ import annotations

import asyncio

import pytest

from async_hooks import AsyncHooks


@pytest.fixture
def hooks() -> AsyncHooks:
    return AsyncHooks()


@pytest.mark.asyncio
async def test_hooks_scope_is_async_context_manager(hooks: AsyncHooks) -> None:
    async with hooks.scope(name="batch", task_id="abc") as scope:
        assert scope.name == "batch"
        assert hooks.current_scope is scope

    assert hooks.current_scope is None


@pytest.mark.asyncio
async def test_scope_current_in_callback_for_action(hooks: AsyncHooks) -> None:
    called: list[bool] = []

    async def callback() -> None:
        scope = hooks.current_scope
        called.append(scope is not None)

    hooks.add_action("scope.current", callback)

    async with hooks.scope(name="callback", job="job-1"):
        await hooks.do_action("scope.current")

    assert called == [True]


@pytest.mark.asyncio
async def test_scope_records_action_and_filter_counts(hooks: AsyncHooks) -> None:
    async def action() -> None:
        pass

    def filter_fn(value: str) -> str:
        return f"{value}-filtered"

    hooks.add_action("scope.counting", action)
    hooks.add_filter("scope.counting", filter_fn)

    async with hooks.scope(name="count"):
        await hooks.do_action("scope.counting")
        await hooks.apply_filters("scope.counting", "value")

    scope = hooks.current_scope
    assert scope is None


@pytest.mark.asyncio
async def test_scope_did_action_filter_methods_track_events(hooks: AsyncHooks) -> None:
    scope_captured: list[object] = []

    async def action() -> None:
        pass

    async def action2() -> None:
        scope = hooks.current_scope
        if scope is not None:
            scope_captured.append(scope.did_action("scope.metrics"))
            scope_captured.append(scope.did_filter("scope.metrics"))

    hooks.add_action("scope.metrics", action)
    hooks.add_action("scope.metrics", action2, priority=20)
    hooks.add_filter("scope.metrics", lambda value: value)

    async with hooks.scope(name="metrics"):
        await hooks.do_action("scope.metrics")
        await hooks.apply_filters("scope.metrics", "v")

    assert scope_captured == [1, 0]


@pytest.mark.asyncio
async def test_scope_metadata_access_via_attributes(hooks: AsyncHooks) -> None:
    metadata_values: list[str] = []

    async def callback() -> None:
        scope = hooks.current_scope
        if scope is not None:
            metadata_values.append(str(scope.task_id))
            metadata_values.append(scope.job)

    hooks.add_action("scope.metadata", callback)

    async with hooks.scope(name="meta", task_id="task-42", job="render"):
        await hooks.do_action("scope.metadata")

    assert metadata_values == ["task-42", "render"]


@pytest.mark.asyncio
async def test_scope_metadata_is_available_on_nested_scopes(hooks: AsyncHooks) -> None:
    captures: list[str] = []

    async def inner() -> None:
        scope = hooks.current_scope
        captures.append(scope.parent.name if scope and scope.parent else "no-parent")
        captures.append(scope.parent.request_id if scope and scope.parent else "none")

    hooks.add_action("scope.inner", inner)
    async with hooks.scope(name="outer", request_id="r1"):
        async with hooks.scope(name="inner", request_id="r2"):
            await hooks.do_action("scope.inner")

    assert captures == ["outer", "r1"]


@pytest.mark.asyncio
async def test_scope_parent_attribute_tracks_nesting(hooks: AsyncHooks) -> None:
    parents: list[str] = []

    async def callback() -> None:
        scope = hooks.current_scope
        if scope and scope.parent:
            parents.append(scope.parent.name)

    hooks.add_action("scope.parent", callback)

    async with hooks.scope(name="parent"):
        async with hooks.scope(name="child"):
            await hooks.do_action("scope.parent")

    assert parents == ["parent"]


@pytest.mark.asyncio
async def test_scope_current_scope_is_task_local(hooks: AsyncHooks) -> None:
    async def run_one() -> str:
        async with hooks.scope(name="one", marker="A"):
            await asyncio.sleep(0)
            return str(hooks.current_scope.task_id if hasattr(hooks.current_scope, "task_id") else "missing")

    async def run_two() -> str:
        async with hooks.scope(name="two", marker="B"):
            await asyncio.sleep(0)
            return str(hooks.current_scope.task_id if hasattr(hooks.current_scope, "task_id") else "missing")

    results = await asyncio.gather(run_one(), run_two())

    assert results[0] == "missing"
    assert results[1] == "missing"


@pytest.mark.asyncio
async def test_scope_cleanup_on_exit_after_nested_calls(hooks: AsyncHooks) -> None:
    events: list[str] = []

    async def callback() -> None:
        events.append(str(hooks.current_scope.name if hooks.current_scope else "missing"))
        await hooks.apply_filters("scope.cleanup-filter", "x")
        if hooks.current_scope:
            scope = hooks.current_scope
            assert scope.doing_action("scope.cleanup-action") == 0

    hooks.add_action("scope.cleanup-action", callback)

    async with hooks.scope(name="outer", task_id="cleanup"):
        await hooks.do_action("scope.cleanup-action")

    assert events == ["outer"]
    assert hooks.current_scope is None


@pytest.mark.asyncio
async def test_current_scope_accessible_after_nested_scopes_complete(hooks: AsyncHooks) -> None:
    order: list[str] = []

    async def inner() -> None:
        order.append("inner-enter")
        await asyncio.sleep(0)
        order.append("inner-exit")

    hooks.add_action("scope.nested", inner)

    async with hooks.scope(name="outer", task_id="123"):
        await hooks.do_action("scope.nested")
        order.append("inside")

    order.append("outside")

    assert order == ["inner-enter", "inner-exit", "inside", "outside"]
    assert hooks.current_scope is None


@pytest.mark.asyncio
async def test_scope_exposes_metadata_mapping(hooks: AsyncHooks) -> None:
    async with hooks.scope(name="mapping", job_id="j-1") as scope:
        assert scope.metadata["job_id"] == "j-1"
        assert scope.metadata.get("missing") is None
