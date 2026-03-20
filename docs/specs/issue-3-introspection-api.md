# Issue #3 — Event Catalog and Handler Introspection API

**Status:** Specced
**Date:** 2026-03-20
**Closes:** #3

---

## Problem

Debugging plugin wiring in PERSIST requires poking at `_action_hooks` and `_filter_hooks` internals. When an event doesn't fire as expected, there's no public way to answer: who's listening, in what order, from which module, with what priority?

---

## Proposed Design

### `registered_events()`

Returns the set of all hook names that have at least one registered callback, across both action and filter registries.

```python
hooks.registered_events() -> set[str]
# → {"task.created", "task.completed", "config.changed"}
```

Returns a set (not a list) — ordering isn't meaningful here. Callers can sort if needed.

### `describe(hook_name)`

Returns an ordered list of handler descriptors for a given hook name, across both action and filter registries, sorted by priority then registration order.

```python
hooks.describe("task.created") -> list[HandlerInfo]
```

`HandlerInfo` is a TypedDict (not a plain dict, not a dataclass — TypedDict is the right fit: structured, serializable, no behavior):

```python
class HandlerInfo(TypedDict):
    callback_id: str
    hook_name: str
    hook_type: Literal["action", "filter"]  # which registry
    priority: int
    handler_name: str    # callback.__qualname__ or "<lambda>" or "<unknown>"
    module: str          # callback.__module__ or "<unknown>"
    detached: bool       # True if registered with detach=True
    accepted_args: int   # filters only; always 1 for actions
```

If `hook_name` has no callbacks, returns `[]` (not raises).

If `hook_name` appears in both `_action_hooks` and `_filter_hooks` (possible via the `add_filter()` backward-compat path from issue #5), both registrations appear with their respective `hook_type`.

### `describe_all()`

Convenience method returning all descriptors across all hooks:

```python
hooks.describe_all() -> list[HandlerInfo]
# Sorted by hook_name, then priority, then registration order
```

---

## Implementation Notes

### Handler name resolution

```python
def _resolve_handler_name(callback: Callable) -> str:
    return getattr(callback, "__qualname__", None) or getattr(callback, "__name__", "<unknown>")

def _resolve_module(callback: Callable) -> str:
    return getattr(callback, "__module__", "<unknown>") or "<unknown>"
```

Lambdas: `__qualname__` returns `"<lambda>"` in Python — that's fine, surface it as-is.
Functools partials: `__qualname__` may be absent; fall back to `func.__qualname__`.

### Building descriptors

`describe()` walks `_action_hooks[hook_name]` and `_filter_hooks[hook_name]`, yielding one `HandlerInfo` per callback, in priority order (sorted keys), then registration order within each priority bucket.

No new per-callback metadata needs to be stored — everything needed (`priority`, `callback_id`, callback reference) is already in the existing data structures. Priority is recoverable by scanning the priority dict.

The one new thing to track: **registration order** within a priority bucket. Currently the list is insertion-ordered (Python list guarantees), so this is already implicit. No change needed.

### `registered_events()` implementation

```python
def registered_events(self) -> set[str]:
    return set(self._action_hooks.keys()) | set(self._filter_hooks.keys())
```

---

## Edge Cases

| Scenario | Behavior |
|---|---|
| Hook name with no callbacks | `describe()` returns `[]` |
| Callback registered with `detach=True` | `detached: True` in HandlerInfo |
| Lambda callback | `handler_name: "<lambda>"` |
| Hook in both action + filter registries | Both appear with distinct `hook_type` |
| Callback pending deferred removal | Still appears in `describe()` — it's still technically registered until nesting exits |

**Note on deferred removals:** Callbacks in `_removed_actions`/`_removed_filters` but not yet cleaned up are still in the hook lists. `describe()` should filter these out for accuracy — check against `self._removed_actions[hook_name]` and `self._removed_filters[hook_name]` before including a descriptor.

---

## Backwards Compatibility

Fully additive. No existing behavior changes.

---

## Test Plan

- `registered_events()` returns correct set after registrations
- `registered_events()` excludes hooks with all callbacks removed
- `describe()` returns empty list for unknown hook
- `describe()` returns correct priority ordering
- `describe()` includes correct `module` and `handler_name` for named functions
- `describe()` handles lambdas without error
- `describe()` marks detached callbacks correctly
- `describe()` excludes deferred-removal callbacks
- `describe_all()` covers all hooks
