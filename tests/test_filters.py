from __future__ import annotations

import asyncio
import logging

import pytest

from async_hooks import AsyncHooks


@pytest.fixture
def hooks() -> AsyncHooks:
    return AsyncHooks(action_timeout_seconds=0.05, filter_timeout_seconds=None)


def _maybe_add_one(value: str, *_) -> str:
    return f"{value}+1"


def _append_filter(value: str, marker: list[str], suffix: str) -> str:
    marker.append(suffix)
    return f"{value}:{suffix}"


@pytest.mark.asyncio
async def test_add_filter_and_apply_filters_chains_value(hooks: AsyncHooks) -> None:
    marker: list[str] = []
    hooks.add_filter("filters.chain", _maybe_add_one)
    hooks.add_filter(
        "filters.chain", lambda value, *args: _append_filter(value, marker, "second")
    )
    hooks.add_filter("filters.chain", lambda value, *args: f"{value}*3")

    result = await hooks.apply_filters("filters.chain", "v")

    assert result == "v+1:second*3"
    assert marker == ["second"]


@pytest.mark.asyncio
async def test_filter_priority_order(hooks: AsyncHooks) -> None:
    marker: list[str] = []

    def first(value: str, *_) -> str:
        marker.append("first")
        return f"{value}-first"

    async def second(value: str, *_) -> str:
        marker.append("second")
        return f"{value}-second"

    def third(value: str, *_) -> str:
        marker.append("third")
        return f"{value}-third"

    hooks.add_filter("filters.priority", third, priority=20)
    hooks.add_filter("filters.priority", second, priority=5)
    hooks.add_filter("filters.priority", first, priority=10)

    result = await hooks.apply_filters("filters.priority", "start")

    assert marker == ["second", "first", "third"]
    assert result == "start-second-first-third"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "accepted_args",
    [
        1,
        2,
        3,
    ],
)
async def test_accepted_args_controls_argument_passing(
    hooks: AsyncHooks,
    accepted_args: int,
) -> None:
    events: list[str] = []
    expected_events: dict[int, list[str]] = {
        1: ["one:0", "two:None", "three:None:None"],
        2: ["one:1", "two:10", "three:10:None"],
        3: ["one:2", "two:10", "three:10:20"],
    }

    def one_arg(value: str, *_) -> str:
        events.append(f"one:{len(_)}")
        return value

    def two_args(value: str, arg_a: int | None = None, *_) -> str:
        events.append(f"two:{arg_a}")
        if arg_a is None:
            return value
        return f"{value}-{arg_a}"

    def three_args(value: str, arg_a: int | None = None, arg_b: int | None = None) -> str:
        events.append(f"three:{arg_a}:{arg_b}")
        if arg_a is None:
            return value
        if arg_b is None:
            return f"{value}-{arg_a}"
        return f"{value}-{arg_a}-{arg_b}"

    hooks.add_filter("filters.args", one_arg, accepted_args=accepted_args, priority=1)
    hooks.add_filter("filters.args", two_args, accepted_args=accepted_args, priority=2)
    hooks.add_filter("filters.args", three_args, accepted_args=accepted_args, priority=3)

    result = await hooks.apply_filters("filters.args", "v", 10, 20)

    assert events == expected_events[accepted_args]
    if accepted_args == 1:
        assert result == "v"
    elif accepted_args == 2:
        assert result == "v-10-10"
    else:
        assert result == "v-10-10-20"


@pytest.mark.asyncio
async def test_remove_filter_works_immediately(hooks: AsyncHooks) -> None:
    marker: list[str] = []
    keep_id = hooks.add_filter("filters.remove", lambda value: (marker.append("keep") or value), priority=10)
    remove_id = hooks.add_filter("filters.remove", lambda value: marker.append("remove") or value, priority=20)

    hooks.remove_filter("filters.remove", remove_id)
    result = await hooks.apply_filters("filters.remove", "start")

    assert marker == ["keep"]
    assert result == "start"


@pytest.mark.asyncio
async def test_remove_filter_deferred_until_execution_completes(hooks: AsyncHooks) -> None:
    marker: list[str] = []

    deferred_id: str = ""

    def first(value: str, *_) -> str:
        hooks.remove_filter("filters.remove_deferred", deferred_id)
        return f"{value}:first"

    def second(value: str, *_) -> str:
        marker.append("second")
        return f"{value}:second"

    hooks.add_filter("filters.remove_deferred", lambda value: f"{value}:first")
    deferred_id = hooks.add_filter("filters.remove_deferred", first, priority=20)
    hooks.add_filter("filters.remove_deferred", second, priority=30)

    result = await hooks.apply_filters("filters.remove_deferred", "seed")

    assert result == "seed:first:first:second"
    assert marker == ["second"]
    assert hooks.has_filter("filters.remove_deferred", deferred_id) is False


@pytest.mark.asyncio
async def test_has_filter_returns_true_false_or_count(hooks: AsyncHooks) -> None:
    first_id = hooks.add_filter("filters.has", lambda value: value)
    second_id = hooks.add_filter("filters.has", lambda value: value, priority=20)

    assert hooks.has_filter("filters.has") == 2
    assert hooks.has_filter("filters.has", first_id) is True
    assert hooks.has_filter("filters.has", "missing") is False

    hooks.remove_filter("filters.has", first_id)
    assert hooks.has_filter("filters.has") == 1
    assert hooks.has_filter("filters.has", first_id) is False
    assert hooks.has_filter("filters.has", second_id) is True


@pytest.mark.asyncio
async def test_doing_filter_tracks_execution_in_callbacks(hooks: AsyncHooks) -> None:
    marker: list[bool] = []

    def filter_fn(value: str) -> str:
        marker.append(hooks.doing_filter("filters.state"))
        return value

    hooks.add_filter("filters.state", filter_fn)
    assert hooks.doing_filter("filters.state") is False

    await hooks.apply_filters("filters.state", "x")

    assert marker == [True]
    assert hooks.doing_filter("filters.state") is False


@pytest.mark.asyncio
async def test_did_filter_counts_invocations(hooks: AsyncHooks) -> None:
    hooks.add_filter("filters.count", lambda value: value)
    hooks.add_filter("filters.count", lambda value: value, priority=20)

    await hooks.apply_filters("filters.count", "one")
    await hooks.apply_filters("filters.count", "two")

    assert hooks.did_filter("filters.count") == 2


@pytest.mark.asyncio
async def test_exception_in_filter_logs_error_and_keeps_current_value(hooks: AsyncHooks, caplog: pytest.LogCaptureFixture) -> None:
    marker: list[str] = []

    async def bad(value: str) -> str:
        raise RuntimeError("bad filter")

    def good(value: str) -> str:
        marker.append("good")
        return f"{value}:good"

    hooks.add_filter("filters.exception", bad, priority=10)
    hooks.add_filter("filters.exception", good, priority=20)

    with caplog.at_level(logging.ERROR):
        result = await hooks.apply_filters("filters.exception", "seed")

    assert result == "seed:good"
    assert marker == ["good"]
    assert hooks.did_filter("filters.exception") == 1
    assert any(
        record.levelname == "ERROR" and "apply_filters exception" in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_empty_filter_chain_returns_original_value(hooks: AsyncHooks) -> None:
    result = await hooks.apply_filters("filters.empty", {"alpha": 1})
    assert result == {"alpha": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("n", [1, 5, 25, 50])
async def test_many_filters_transform_stabilization(hooks: AsyncHooks, n: int) -> None:
    def make(value: str, index: int = 0) -> str:
        return f"{value}|{index}"

    for i in range(n):
        hooks.add_filter("filters.large_chain", lambda value, i=i: make(value, i), priority=i)

    result = await hooks.apply_filters("filters.large_chain", "start")
    assert result.startswith("start")
    assert result.count("|") == n


@pytest.mark.asyncio
async def test_filter_with_sync_and_async_callbacks(hooks: AsyncHooks) -> None:
    async def fast(value: str) -> str:
        await asyncio.sleep(0)
        return f"{value}:a"

    def slowish(value: str) -> str:
        return f"{value}:s"

    hooks.add_filter("filters.mix", slowish, priority=5)
    hooks.add_filter("filters.mix", fast, priority=10)
    hooks.add_filter("filters.mix", slowish, priority=15)

    result = await hooks.apply_filters("filters.mix", "seed")

    assert result == "seed:s:a:s"


@pytest.mark.asyncio
async def test_filter_timeout_logs_warning_and_continues(hooks: AsyncHooks, caplog: pytest.LogCaptureFixture) -> None:
    async def timeout(value: str) -> str:
        await asyncio.sleep(0.02)
        return f"{value}:late"

    hooks.add_filter("filters.timeout", timeout, timeout_seconds=0.001, priority=10)

    with caplog.at_level(logging.WARNING):
        result = await hooks.apply_filters("filters.timeout", "seed")

    assert result == "seed"
    assert any(
        record.levelname == "WARNING" and "apply_filters timeout" in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_filter_applied_to_complex_types(hooks: AsyncHooks) -> None:
    def dict_passthrough(value: dict[str, int]) -> dict[str, int]:
        value["count"] += 1
        return value

    hooks.add_filter("filters.types", dict_passthrough)
    value = {"count": 1}
    result = await hooks.apply_filters("filters.types", value)

    assert result is value
    assert value["count"] == 2


@pytest.mark.asyncio
async def test_remove_all_filters_by_priority(hooks: AsyncHooks) -> None:
    values: list[str] = []
    hooks.add_filter("filters.remove_priority", lambda value: (values.append("a") or f"{value}a"), priority=5)
    hooks.add_filter("filters.remove_priority", lambda value: (values.append("b") or f"{value}b"), priority=10)
    hooks.add_filter("filters.remove_priority", lambda value: (values.append("c") or f"{value}c"), priority=10)

    hooks.remove_all_filters("filters.remove_priority", priority=10)

    result = await hooks.apply_filters("filters.remove_priority", "x")

    assert values == ["a"]
    assert result == "xa"
    assert hooks.has_filter("filters.remove_priority") == 1


@pytest.mark.asyncio
async def test_remove_all_filters_all_priorities(hooks: AsyncHooks) -> None:
    hooks.add_filter("filters.remove_all", lambda value: value, priority=1)
    hooks.add_filter("filters.remove_all", lambda value: value, priority=2)
    assert hooks.remove_all_filters("filters.remove_all") is True
    assert hooks.has_filter("filters.remove_all") == 0

    result = await hooks.apply_filters("filters.remove_all", "ok")
    assert result == "ok"


@pytest.mark.asyncio
async def test_filter_chain_handles_non_ascii_values(hooks: AsyncHooks) -> None:
    hooks.add_filter("filters.unicode", lambda value: f"{value}-☃")
    result = await hooks.apply_filters("filters.unicode", "value")
    assert result == "value-☃"


@pytest.mark.asyncio
async def test_filter_chaining_supports_none_value(hooks: AsyncHooks) -> None:
    marker: list[str] = []

    hooks.add_filter("filters.none", lambda value: (marker.append("a") or "a"))
    hooks.add_filter("filters.none", lambda value: (marker.append("b") or None))
    hooks.add_filter("filters.none", lambda value: (marker.append("c") or value))

    result = await hooks.apply_filters("filters.none", None)

    assert marker == ["a", "b", "c"]
    assert result is None


@pytest.mark.asyncio
async def test_filter_add_invalid_hook_name(hooks: AsyncHooks) -> None:
    with pytest.raises(ValueError):
        hooks.add_filter("", lambda value: value)

    with pytest.raises(ValueError):
        hooks.add_filter(123, lambda value: value)  # type: ignore[arg-type]
