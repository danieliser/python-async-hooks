"""Tests for issue #3 — registered_events() and describe() introspection API."""

from __future__ import annotations

import pytest

from async_hooks import AsyncHooks, HandlerInfo


@pytest.fixture
def hooks() -> AsyncHooks:
    return AsyncHooks()


# ─ registered_events() ───────────────────────────────────────────────────────

def test_registered_events_empty(hooks: AsyncHooks) -> None:
    assert hooks.registered_events() == set()


def test_registered_events_after_add_action(hooks: AsyncHooks) -> None:
    hooks.add_action("task.created", lambda: None)
    assert "task.created" in hooks.registered_events()


def test_registered_events_after_add_filter(hooks: AsyncHooks) -> None:
    hooks.add_filter("task.payload", lambda v: v)
    assert "task.payload" in hooks.registered_events()


def test_registered_events_covers_both_registries(hooks: AsyncHooks) -> None:
    hooks.add_action("evt.action", lambda: None)
    hooks.add_filter("evt.filter", lambda v: v)
    events = hooks.registered_events()
    assert "evt.action" in events
    assert "evt.filter" in events


def test_registered_events_excludes_removed_hooks(hooks: AsyncHooks) -> None:
    cid = hooks.add_action("evt.transient", lambda: None)
    hooks.remove_action("evt.transient", cid)
    assert "evt.transient" not in hooks.registered_events()


# ─ describe() ────────────────────────────────────────────────────────────────

def test_describe_unknown_hook_returns_empty(hooks: AsyncHooks) -> None:
    assert hooks.describe("no.such.hook") == []


def test_describe_action_basic_fields(hooks: AsyncHooks) -> None:
    async def my_handler() -> None:
        pass

    cid = hooks.add_action("task.created", my_handler, priority=5)
    infos = hooks.describe("task.created")

    assert len(infos) == 1
    info = infos[0]
    assert info["callback_id"] == cid
    assert info["hook_name"] == "task.created"
    assert info["hook_type"] == "action"
    assert info["priority"] == 5
    assert info["handler_name"].endswith("my_handler")
    assert info["detached"] is False
    assert info["accepted_args"] == 1


def test_describe_filter_basic_fields(hooks: AsyncHooks) -> None:
    async def my_filter(val):
        return val

    cid = hooks.add_filter("task.payload", my_filter, priority=20, accepted_args=2)
    infos = hooks.describe("task.payload")

    assert len(infos) == 1
    info = infos[0]
    assert info["hook_type"] == "filter"
    assert info["priority"] == 20
    assert info["accepted_args"] == 2
    assert info["detached"] is False


def test_describe_priority_ordering(hooks: AsyncHooks) -> None:
    async def low() -> None:
        pass

    async def high() -> None:
        pass

    hooks.add_action("evt.order", low, priority=50)
    hooks.add_action("evt.order", high, priority=5)

    infos = hooks.describe("evt.order")
    assert infos[0]["priority"] == 5
    assert infos[1]["priority"] == 50


def test_describe_detached_flag(hooks: AsyncHooks) -> None:
    async def handler() -> None:
        pass

    cid = hooks.add_action("evt.detached", handler, detach=True)
    infos = hooks.describe("evt.detached")
    assert infos[0]["detached"] is True


def test_describe_lambda_handler_name(hooks: AsyncHooks) -> None:
    hooks.add_action("evt.lambda", lambda: None)
    infos = hooks.describe("evt.lambda")
    assert infos[0]["handler_name"].endswith("<lambda>")


def test_describe_module_populated(hooks: AsyncHooks) -> None:
    async def handler() -> None:
        pass

    hooks.add_action("evt.module", handler)
    infos = hooks.describe("evt.module")
    assert infos[0]["module"] == __name__


def test_describe_covers_both_action_and_filter(hooks: AsyncHooks) -> None:
    async def action_handler() -> None:
        pass

    async def filter_handler(val):
        return val

    hooks.add_action("evt.mixed", action_handler)
    hooks.add_filter("evt.mixed", filter_handler)

    infos = hooks.describe("evt.mixed")
    types = {i["hook_type"] for i in infos}
    assert types == {"action", "filter"}


@pytest.mark.asyncio
async def test_describe_excludes_deferred_removal(hooks: AsyncHooks) -> None:
    """Callbacks pending deferred removal should not appear in describe()."""
    removed_cid: list[str] = []

    async def self_removing() -> None:
        hooks.remove_action("evt.deferred", removed_cid[0])

    cid = hooks.add_action("evt.deferred", self_removing)
    removed_cid.append(cid)

    # While executing, callback removes itself (deferred)
    await hooks.do_action("evt.deferred")

    # After execution completes, the callback should be gone from describe()
    infos = hooks.describe("evt.deferred")
    assert all(i["callback_id"] != cid for i in infos)


# ─ describe_all() ────────────────────────────────────────────────────────────

def test_describe_all_empty(hooks: AsyncHooks) -> None:
    assert hooks.describe_all() == []


def test_describe_all_covers_multiple_hooks(hooks: AsyncHooks) -> None:
    hooks.add_action("alpha", lambda: None)
    hooks.add_filter("beta", lambda v: v)
    hooks.add_action("gamma", lambda: None)

    infos = hooks.describe_all()
    hook_names = [i["hook_name"] for i in infos]
    assert "alpha" in hook_names
    assert "beta" in hook_names
    assert "gamma" in hook_names


def test_describe_all_sorted_by_hook_name(hooks: AsyncHooks) -> None:
    hooks.add_action("zzz", lambda: None)
    hooks.add_action("aaa", lambda: None)
    hooks.add_action("mmm", lambda: None)

    infos = hooks.describe_all()
    names = [i["hook_name"] for i in infos]
    assert names == sorted(names)
