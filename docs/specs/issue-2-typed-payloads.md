# Issue #2 — Typed Event Payloads (Pydantic Schema per Hook)

**Status:** Specced
**Date:** 2026-03-20
**Closes:** #2

---

## Problem

Inter-module events in PERSIST v2 use Pydantic models. When a handler receives a malformed payload, the failure surfaces deep inside the handler rather than at the emission boundary — exactly where it's most expensive to debug. There's no way to declare "this hook always carries a TaskCreatedEvent" and catch violations early.

---

## Proposed Design

### Schema Registration (separate from subscription)

Schemas are registered per hook name, not per callback. One schema per hook. This is the right scope: the schema describes the event contract, not a subscriber's preference.

```python
hooks.register_schema("task.created", TaskCreatedEvent)
hooks.schema_for("task.created")  # → TaskCreatedEvent or None
```

**Why not `add_filter(..., schema=...)`?**
Attaching schema to a callback registration creates an impossible conflict: two subscribers could register different schemas for the same event. The schema belongs to the emitter's contract, not the subscriber's.

### Validation at Emit Time

Validation runs in `do_action` and `apply_filters` before any callbacks are called.

For **actions**, the first positional argument is treated as the payload if it's a dict or Pydantic model. If no positional args are present, kwargs is validated.

For **filters**, the `value` argument is validated against the schema.

```python
# Validation is opt-in via constructor flag
hooks = AsyncHooks(validate_payloads=True)   # dev/test mode
hooks = AsyncHooks(validate_payloads=False)  # production default
```

`validate_payloads` defaults to `False` — zero cost in production. Can also be toggled at runtime via `hooks.validate_payloads = True`.

### Validation Behavior

- Schema present + `validate_payloads=True`: calls `Schema.model_validate(payload)` before dispatching. Raises `HookPayloadError` (new exception) on mismatch.
- Schema present + `validate_payloads=False`: schema is stored, callable via `schema_for()`, but validation is skipped.
- No schema registered: always passes through unchanged, regardless of `validate_payloads`.

### New Exception

```python
class HookPayloadError(HookError):
    """Raised when a hook payload fails schema validation."""
    def __init__(self, hook_name: str, schema: type, errors: list):
        ...
```

### New Public API

```python
hooks.register_schema(hook_name: str, schema: type) -> None
hooks.schema_for(hook_name: str) -> type | None
hooks.validate_payloads: bool  # readable/writable property
```

---

## Implementation Notes

- Pydantic is an **optional** dependency. Import it lazily inside `register_schema`. If Pydantic isn't installed and `register_schema` is called, raise `ImportError` with a clear message: `"Pydantic is required for typed payloads: pip install pydantic"`.
- Store schemas in `self._hook_schemas: dict[str, type] = {}` on `AsyncHooks`.
- `model_validate` handles both dict and Pydantic model input — no special-casing needed.
- Validation should not swallow errors. `HookPayloadError` is never caught internally; it propagates to the caller of `do_action`/`apply_filters`.

---

## Edge Cases

| Scenario | Behavior |
|---|---|
| Schema registered, payload is already the correct Pydantic type | `model_validate` is idempotent for valid instances — passes through |
| Schema registered, validate_payloads=False | Silent skip. `schema_for()` still returns the type. |
| No schema, validate_payloads=True | No validation, no error |
| Pydantic not installed, register_schema called | `ImportError` with install hint |
| Schema registered for a hook that never fires | Benign — no overhead |

---

## Backwards Compatibility

Fully backwards compatible. `AsyncHooks()` with no new args behaves identically to current. All new behavior is opt-in.

---

## Test Plan

- `register_schema` stores schema, `schema_for` retrieves it
- `validate_payloads=True` + valid payload → no exception
- `validate_payloads=True` + invalid payload → `HookPayloadError`
- `validate_payloads=False` + invalid payload → no exception
- No schema registered → no validation regardless of flag
- Pydantic not installed → `ImportError` on `register_schema`
- Filter chain: validated value passes through to first callback unchanged
