"""Tests for issue #2 — typed payload validation via Pydantic schemas."""

from __future__ import annotations

import pytest

from async_hooks import AsyncHooks, HookPayloadError


# ─ Helpers ───────────────────────────────────────────────────────────────────

def make_hooks(validate: bool = True) -> AsyncHooks:
    return AsyncHooks(validate_payloads=validate)


# Import pydantic lazily so tests are skipped cleanly if not installed
pydantic = pytest.importorskip("pydantic")


class TaskPayload(pydantic.BaseModel):
    task_id: str
    priority: int = 10


class OtherPayload(pydantic.BaseModel):
    name: str


# ─ register_schema() / schema_for() ──────────────────────────────────────────

def test_register_schema_stores_schema() -> None:
    hooks = make_hooks()
    hooks.register_schema("task.created", TaskPayload)
    assert hooks.schema_for("task.created") is TaskPayload


def test_schema_for_returns_none_for_unknown() -> None:
    hooks = make_hooks()
    assert hooks.schema_for("no.such.hook") is None


def test_register_schema_raises_on_empty_hook_name() -> None:
    hooks = make_hooks()
    with pytest.raises(ValueError):
        hooks.register_schema("", TaskPayload)


# ─ validate_payloads property ────────────────────────────────────────────────

def test_validate_payloads_default_false() -> None:
    hooks = AsyncHooks()
    assert hooks.validate_payloads is False


def test_validate_payloads_constructor_true() -> None:
    hooks = AsyncHooks(validate_payloads=True)
    assert hooks.validate_payloads is True


def test_validate_payloads_settable() -> None:
    hooks = AsyncHooks()
    hooks.validate_payloads = True
    assert hooks.validate_payloads is True
    hooks.validate_payloads = False
    assert hooks.validate_payloads is False


# ─ do_action validation ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_do_action_valid_dict_payload_passes() -> None:
    hooks = make_hooks(validate=True)
    hooks.register_schema("task.created", TaskPayload)

    fired: list[bool] = []
    hooks.add_action("task.created", lambda payload: fired.append(True))

    await hooks.do_action("task.created", {"task_id": "t1", "priority": 5})
    assert fired == [True]


@pytest.mark.asyncio
async def test_do_action_valid_model_payload_passes() -> None:
    hooks = make_hooks(validate=True)
    hooks.register_schema("task.created", TaskPayload)

    await hooks.do_action("task.created", TaskPayload(task_id="t1"))


@pytest.mark.asyncio
async def test_do_action_invalid_payload_raises_hook_payload_error() -> None:
    hooks = make_hooks(validate=True)
    hooks.register_schema("task.created", TaskPayload)

    with pytest.raises(HookPayloadError) as exc_info:
        await hooks.do_action("task.created", {"priority": "not-an-int-either", "task_id": 123})

    err = exc_info.value
    assert err.hook_name == "task.created"
    assert err.schema is TaskPayload
    assert isinstance(err.errors, list)


@pytest.mark.asyncio
async def test_do_action_no_schema_no_validation() -> None:
    hooks = make_hooks(validate=True)
    # No schema registered — anything passes
    fired: list[bool] = []
    hooks.add_action("evt.untyped", lambda: fired.append(True))
    await hooks.do_action("evt.untyped", {"garbage": True})
    assert fired == [True]


@pytest.mark.asyncio
async def test_do_action_validate_false_skips_validation() -> None:
    hooks = make_hooks(validate=False)
    hooks.register_schema("task.created", TaskPayload)

    # Invalid payload, but validate_payloads=False — should not raise
    fired: list[bool] = []
    hooks.add_action("task.created", lambda p: fired.append(True))
    await hooks.do_action("task.created", {"wrong_field": "value"})
    assert fired == [True]


@pytest.mark.asyncio
async def test_do_action_validation_toggle_runtime() -> None:
    hooks = AsyncHooks()
    hooks.register_schema("task.created", TaskPayload)

    invalid = {"wrong": "data"}

    # Off by default — no error
    hooks.add_action("task.created", lambda p: None)
    await hooks.do_action("task.created", invalid)

    # Enable at runtime
    hooks.validate_payloads = True
    with pytest.raises(HookPayloadError):
        await hooks.do_action("task.created", invalid)


# ─ apply_filters validation ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_apply_filters_valid_value_passes() -> None:
    hooks = make_hooks(validate=True)
    hooks.register_schema("task.payload", TaskPayload)

    payload = TaskPayload(task_id="t1")
    result = await hooks.apply_filters("task.payload", payload)
    assert result is payload


@pytest.mark.asyncio
async def test_apply_filters_invalid_value_raises() -> None:
    hooks = make_hooks(validate=True)
    hooks.register_schema("task.payload", TaskPayload)

    with pytest.raises(HookPayloadError) as exc_info:
        await hooks.apply_filters("task.payload", {"missing_task_id": True})

    assert exc_info.value.hook_name == "task.payload"


@pytest.mark.asyncio
async def test_apply_filters_validate_false_skips() -> None:
    hooks = make_hooks(validate=False)
    hooks.register_schema("task.payload", TaskPayload)

    result = await hooks.apply_filters("task.payload", {"garbage": 1})
    assert result == {"garbage": 1}


# ─ HookPayloadError fields ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_hook_payload_error_has_expected_fields() -> None:
    hooks = make_hooks(validate=True)
    hooks.register_schema("task.created", TaskPayload)

    try:
        await hooks.do_action("task.created", {"bad": "data"})
    except HookPayloadError as err:
        assert err.hook_name == "task.created"
        assert err.schema is TaskPayload
        assert isinstance(err.errors, list)
        assert len(err.errors) > 0
        assert "task.created" in str(err)
    else:
        pytest.fail("HookPayloadError not raised")
