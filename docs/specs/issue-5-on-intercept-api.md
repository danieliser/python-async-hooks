# Issue #5 — Separate Action vs Filter Subscription (on / intercept)

**Status:** Specced
**Date:** 2026-03-20
**Closes:** #5

---

## Problem

The existing API has `add_action()` (fires on `do_action`) and `add_filter()` (fires on `apply_filters`). These are already separate — so what's the issue?

The problem is naming and intent clarity. `add_action` and `add_filter` are jargon inherited from WordPress. For developers new to the library, the distinction between "I want to observe an event" vs "I want to transform a value" is not obvious from those names. Additionally, when the same logical event name is used for both action and filter dispatch, subscribing via `add_filter` to observe (without transforming) risks accidentally returning `None` and corrupting the filter chain.

The proposal introduces ergonomic aliases with clearer intent semantics.

---

## Proposed Design

### New Methods

```python
# Action-only: fire-and-forget observer. Return value is ignored.
callback_id = hooks.on("task.created", handler, priority=10, timeout_seconds=None, detach=False) -> str

# Filter-only: value transformer. Must return the (possibly modified) value.
callback_id = hooks.intercept("task.dispatch", handler, priority=10, accepted_args=1, timeout_seconds=None) -> str
```

These are **thin aliases** with clear behavioral contracts:

| Method | Stored in | Called by | Return value |
|---|---|---|---|
| `add_action()` | `_action_hooks` | `do_action()` | ignored |
| `on()` | `_action_hooks` | `do_action()` | ignored |
| `add_filter()` | `_filter_hooks` | `apply_filters()` | used to advance filter chain |
| `intercept()` | `_filter_hooks` | `apply_filters()` | used to advance filter chain |

**`on()` is a direct alias for `add_action()`.**
**`intercept()` is a direct alias for `add_filter()`.**

No new storage structures. No behavior changes. Purely ergonomic.

### Removal

`remove_filter()` and `remove_action()` remain the removal methods. Their behavior depends on `_callback_types[callback_id]`, which is set correctly regardless of whether registration used the old or new name.

Consider adding `hooks.off(hook_name, callback_id)` as a universal removal method — routes to `remove_action` or `remove_filter` based on the registered type:

```python
def off(self, hook_name: str, callback_id: str) -> bool:
    kind = self._callback_types.get(callback_id)
    if kind == "action":
        return self.remove_action(hook_name, callback_id)
    elif kind == "filter":
        return self.remove_filter(hook_name, callback_id)
    return False
```

`off()` is the natural counterpart to `on()`.

---

## Design Decisions

### Why not make `add_filter()` register in both registries?

The issue sketch proposed `add_filter()` → both registries as a "backward compat" move. This is **rejected**:

1. It changes existing behavior — `add_filter` currently only fires on `apply_filters`, and code that relies on this would break silently.
2. "Backward compat" should mean "old code still works the same," not "old method does something new."
3. The right migration path is: use `on()` for observe-only, `intercept()` for transform. `add_filter()` stays as-is.

### Why aliases instead of distinct implementations?

The underlying semantics are already correct — actions go to `_action_hooks`, filters go to `_filter_hooks`. The only missing piece is discoverable naming. Aliases achieve this without adding complexity or diverging code paths to maintain.

### The "accidental None return" concern

When a developer writes:

```python
hooks.add_filter("task.created", observer)
```

...and `observer` doesn't return anything (returns `None`), the filter chain is broken if the same event is ever used with `apply_filters`. With `on()`, the intent is explicit — it's an action observer, and the return value is structurally ignored by `do_action`.

This is the core ergonomic win: **the subscription method communicates intent**.

---

## Implementation Notes

`on()` and `intercept()` delegate entirely to `add_action()` and `add_filter()` respectively:

```python
def on(self, hook_name, callback, priority=10, timeout_seconds=None, detach=False) -> str:
    return self.add_action(hook_name, callback, priority=priority,
                           timeout_seconds=timeout_seconds, detach=detach)

def intercept(self, hook_name, callback, priority=10, accepted_args=1, timeout_seconds=None) -> str:
    return self.add_filter(hook_name, callback, priority=priority,
                           accepted_args=accepted_args, timeout_seconds=timeout_seconds)
```

`off()` delegates to `remove_action` or `remove_filter` based on `_callback_types`.

No changes to `_action_hooks`, `_filter_hooks`, or any execution paths.

---

## Backwards Compatibility

Fully backwards compatible. `add_action`, `add_filter`, `remove_action`, `remove_filter` are unchanged. `on`, `intercept`, `off` are new additions.

---

## Test Plan

- `on()` registers in action registry — `has_action()` returns True
- `on()` fires on `do_action()`, not on `apply_filters()`
- `intercept()` registers in filter registry — `has_filter()` returns True
- `intercept()` fires on `apply_filters()`, not on `do_action()`
- `off()` removes action callbacks registered via `on()`
- `off()` removes filter callbacks registered via `intercept()`
- `off()` removes callbacks registered via `add_action()` / `add_filter()`
- `off()` returns False for unknown callback_id
- Deferred removal works correctly for `off()` during execution
- All existing `add_action`/`add_filter` tests pass unchanged
