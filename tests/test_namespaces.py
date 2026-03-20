"""Tests for namespace support — subscribe_all(namespace=), registered_events(namespace=),
describe_all(namespace=), and remove_namespace()."""

from __future__ import annotations

import pytest

from async_hooks import AsyncHooks


@pytest.fixture
def hooks() -> AsyncHooks:
    return AsyncHooks(action_timeout_seconds=0.1)


# ─ _hook_matches_namespace helper ────────────────────────────────────────────

def test_namespace_exact_match(hooks: AsyncHooks) -> None:
    assert hooks._hook_matches_namespace("task", "task") is True


def test_namespace_child_match(hooks: AsyncHooks) -> None:
    assert hooks._hook_matches_namespace("task.created", "task") is True


def test_namespace_nested_child_match(hooks: AsyncHooks) -> None:
    assert hooks._hook_matches_namespace("task.lifecycle.start", "task") is True


def test_namespace_no_partial_prefix_match(hooks: AsyncHooks) -> None:
    # "taskrunner.created" should NOT match namespace "task"
    assert hooks._hook_matches_namespace("taskrunner.created", "task") is False


def test_namespace_sibling_no_match(hooks: AsyncHooks) -> None:
    assert hooks._hook_matches_namespace("config.changed", "task") is False


def test_namespace_sub_namespace_match(hooks: AsyncHooks) -> None:
    # namespace="task.lifecycle" matches "task.lifecycle.start" but not "task.created"
    assert hooks._hook_matches_namespace("task.lifecycle.start", "task.lifecycle") is True
    assert hooks._hook_matches_namespace("task.created", "task.lifecycle") is False


# ─ subscribe_all with namespace ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_subscribe_all_namespace_fires_for_matching_event(hooks: AsyncHooks) -> None:
    captured: list[str] = []

    async def handler(event_name: str, *args, **kwargs) -> None:
        captured.append(event_name)

    hooks.subscribe_all(handler, namespace="task")
    await hooks.do_action("task.created")
    assert captured == ["task.created"]


@pytest.mark.asyncio
async def test_subscribe_all_namespace_does_not_fire_for_other_namespace(hooks: AsyncHooks) -> None:
    captured: list[str] = []

    async def handler(event_name: str, *args, **kwargs) -> None:
        captured.append(event_name)

    hooks.subscribe_all(handler, namespace="task")
    await hooks.do_action("config.changed")
    assert captured == []


@pytest.mark.asyncio
async def test_subscribe_all_namespace_fires_for_all_matching_events(hooks: AsyncHooks) -> None:
    captured: list[str] = []

    async def handler(event_name: str, *args, **kwargs) -> None:
        captured.append(event_name)

    hooks.subscribe_all(handler, namespace="task")
    await hooks.do_action("task.created")
    await hooks.do_action("task.completed")
    await hooks.do_action("task.dispatch")
    await hooks.do_action("config.changed")

    assert set(captured) == {"task.created", "task.completed", "task.dispatch"}


@pytest.mark.asyncio
async def test_subscribe_all_no_namespace_fires_for_everything(hooks: AsyncHooks) -> None:
    captured: list[str] = []

    async def handler(event_name: str, *args, **kwargs) -> None:
        captured.append(event_name)

    hooks.subscribe_all(handler)  # no namespace = global
    await hooks.do_action("task.created")
    await hooks.do_action("config.changed")

    assert set(captured) == {"task.created", "config.changed"}


@pytest.mark.asyncio
async def test_multiple_namespace_subscriptions_independent(hooks: AsyncHooks) -> None:
    task_events: list[str] = []
    config_events: list[str] = []

    async def task_handler(event_name: str, *args, **kwargs) -> None:
        task_events.append(event_name)

    async def config_handler(event_name: str, *args, **kwargs) -> None:
        config_events.append(event_name)

    hooks.subscribe_all(task_handler, namespace="task")
    hooks.subscribe_all(config_handler, namespace="config")

    await hooks.do_action("task.created")
    await hooks.do_action("config.changed")

    assert task_events == ["task.created"]
    assert config_events == ["config.changed"]


@pytest.mark.asyncio
async def test_subscribe_all_namespace_fires_on_apply_filters(hooks: AsyncHooks) -> None:
    captured: list[str] = []

    async def handler(event_name: str, *args, **kwargs) -> None:
        captured.append(event_name)

    hooks.subscribe_all(handler, namespace="task")
    await hooks.apply_filters("task.payload", {"id": 1})
    await hooks.apply_filters("config.value", "x")

    assert captured == ["task.payload"]


@pytest.mark.asyncio
async def test_subscribe_all_namespace_exact_match_fires(hooks: AsyncHooks) -> None:
    """namespace="task" should also fire when hook_name == "task" exactly."""
    captured: list[str] = []

    hooks.subscribe_all(lambda name, *a, **k: captured.append(name), namespace="task")
    await hooks.do_action("task")
    assert captured == ["task"]


@pytest.mark.asyncio
async def test_subscribe_all_namespace_sub_namespace(hooks: AsyncHooks) -> None:
    """namespace="task.lifecycle" should only fire for task.lifecycle.* hooks."""
    lifecycle: list[str] = []
    other: list[str] = []

    hooks.subscribe_all(lambda n, *a, **k: lifecycle.append(n), namespace="task.lifecycle")
    hooks.subscribe_all(lambda n, *a, **k: other.append(n), namespace="task")

    await hooks.do_action("task.lifecycle.start")
    await hooks.do_action("task.created")

    assert lifecycle == ["task.lifecycle.start"]
    assert set(other) == {"task.lifecycle.start", "task.created"}


def test_subscribe_all_invalid_namespace_raises(hooks: AsyncHooks) -> None:
    with pytest.raises(ValueError):
        hooks.subscribe_all(lambda n: None, namespace="")


# ─ registered_events(namespace=) ─────────────────────────────────────────────

def test_registered_events_namespace_filter(hooks: AsyncHooks) -> None:
    hooks.add_action("task.created", lambda: None)
    hooks.add_action("task.completed", lambda: None)
    hooks.add_action("config.changed", lambda: None)

    events = hooks.registered_events(namespace="task")
    assert events == {"task.created", "task.completed"}


def test_registered_events_namespace_no_match_returns_empty(hooks: AsyncHooks) -> None:
    hooks.add_action("config.changed", lambda: None)
    assert hooks.registered_events(namespace="task") == set()


def test_registered_events_no_namespace_returns_all(hooks: AsyncHooks) -> None:
    hooks.add_action("task.created", lambda: None)
    hooks.add_filter("config.value", lambda v: v)
    events = hooks.registered_events()
    assert "task.created" in events
    assert "config.value" in events


def test_registered_events_namespace_no_partial_prefix(hooks: AsyncHooks) -> None:
    hooks.add_action("taskrunner.created", lambda: None)
    assert hooks.registered_events(namespace="task") == set()


# ─ describe_all(namespace=) ───────────────────────────────────────────────────

def test_describe_all_namespace_filter(hooks: AsyncHooks) -> None:
    hooks.add_action("task.created", lambda: None)
    hooks.add_action("task.completed", lambda: None)
    hooks.add_filter("config.value", lambda v: v)

    infos = hooks.describe_all(namespace="task")
    hook_names = {i["hook_name"] for i in infos}
    assert hook_names == {"task.created", "task.completed"}
    assert "config.value" not in hook_names


def test_describe_all_no_namespace_returns_all(hooks: AsyncHooks) -> None:
    hooks.add_action("task.created", lambda: None)
    hooks.add_filter("config.value", lambda v: v)

    infos = hooks.describe_all()
    hook_names = {i["hook_name"] for i in infos}
    assert "task.created" in hook_names
    assert "config.value" in hook_names


# ─ remove_namespace() ────────────────────────────────────────────────────────

def test_remove_namespace_clears_matching_hooks(hooks: AsyncHooks) -> None:
    hooks.add_action("task.created", lambda: None)
    hooks.add_action("task.completed", lambda: None)
    hooks.add_filter("config.value", lambda v: v)

    count = hooks.remove_namespace("task")
    assert count == 2
    assert hooks.registered_events(namespace="task") == set()
    assert "config.value" in hooks.registered_events()


def test_remove_namespace_returns_zero_if_no_match(hooks: AsyncHooks) -> None:
    hooks.add_action("config.changed", lambda: None)
    assert hooks.remove_namespace("task") == 0


def test_remove_namespace_no_partial_prefix_match(hooks: AsyncHooks) -> None:
    hooks.add_action("taskrunner.start", lambda: None)
    count = hooks.remove_namespace("task")
    assert count == 0
    assert "taskrunner.start" in hooks.registered_events()


def test_remove_namespace_removes_both_action_and_filter(hooks: AsyncHooks) -> None:
    hooks.add_action("task.created", lambda: None)
    hooks.add_filter("task.created", lambda v: v)

    hooks.remove_namespace("task")
    assert hooks.has_action("task.created") == 0
    assert hooks.has_filter("task.created") == 0


def test_remove_namespace_sub_namespace(hooks: AsyncHooks) -> None:
    hooks.add_action("task.lifecycle.start", lambda: None)
    hooks.add_action("task.lifecycle.stop", lambda: None)
    hooks.add_action("task.created", lambda: None)

    count = hooks.remove_namespace("task.lifecycle")
    assert count == 2
    assert "task.created" in hooks.registered_events()
    assert hooks.registered_events(namespace="task.lifecycle") == set()


def test_remove_namespace_invalid_raises(hooks: AsyncHooks) -> None:
    with pytest.raises(ValueError):
        hooks.remove_namespace("")


@pytest.mark.asyncio
async def test_remove_namespace_callbacks_no_longer_fire(hooks: AsyncHooks) -> None:
    fired: list[str] = []
    hooks.add_action("task.created", lambda: fired.append("task"))
    hooks.add_action("config.changed", lambda: fired.append("config"))

    hooks.remove_namespace("task")

    await hooks.do_action("task.created")
    await hooks.do_action("config.changed")
    assert fired == ["config"]
