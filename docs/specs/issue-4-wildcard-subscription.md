# Issue #4 — Wildcard Subscription: subscribe_all() / unsubscribe_all()

**Status:** Specced
**Date:** 2026-03-20
**Closes:** #4

---

## Problem

PERSIST v2's Audit plugin needs to capture every event for immutable logging. The current workaround is monkey-patching `emit`/`filter` — which breaks with multiple interceptors, has reentrancy risk, and isn't composable. There's no supported way to "listen to everything."

---

## Proposed Design

### API

```python
callback_id = hooks.subscribe_all(handler, priority: int = 90) -> str
hooks.unsubscribe_all(callback_id: str) -> bool
```

Global handlers are fired for every `do_action()` and every `apply_filters()` call, regardless of hook name.

**Global handler signature:**

```python
async def audit_handler(event_name: str, *args, **kwargs) -> None:
    ...
```

- First argument is always the event name (str).
- Remaining args/kwargs are the original emission args forwarded as-is.
- For filters, `args[0]` is the current value being filtered (same as the first positional to `apply_filters`).
- Return value from global handlers is **always ignored** — they're observers, not transformers. This applies even when called from `apply_filters`.

**Why priority 90 as default?** Global handlers should typically run after domain-specific handlers. Low priority (high number) achieves this.

### Execution Order

Global handlers run **after** all name-specific handlers for a given event:
1. Name-specific action/filter callbacks (priority-ordered)
2. Global handlers (priority-ordered among themselves)

This ordering means global handlers see the final post-processed value for filters, and run after all domain logic for actions.

### Internal Storage

```python
# In AsyncHooks.__init__:
self._global_hooks: dict[int, list[tuple[str, CallbackType]]] = defaultdict(list)
self._global_callback_ids: set[str] = set()  # for O(1) lookup in unsubscribe_all
```

Global callbacks are stored in their own registry, not in `_action_hooks` or `_filter_hooks`. This keeps the name-specific paths clean and avoids polluting `registered_events()`.

`_callback_registry`, `_callback_hooks`, `_callback_types` track global callbacks too — consistent with existing patterns. `_callback_types[callback_id]` is `"global"` (new literal in `CallbackCategory`).

### Removal

`unsubscribe_all(callback_id)` removes from `_global_hooks` and supporting dicts. Returns `False` if the callback_id isn't a global handler.

**Deferred removal:** If a global handler calls `unsubscribe_all` on itself during execution, removal must be deferred (same pattern as `_removed_actions`/`_removed_filters`). Add `_global_nesting: int` counter and `_removed_globals: set[str]`.

### Reentrancy

Global handlers can themselves call `do_action` or `apply_filters`. This is safe — global handlers are invoked *after* the nesting counter for the specific hook has already been incremented. No deadlock risk.

What to avoid: a global handler calling `subscribe_all` during execution. This is equivalent to adding a hook mid-iteration — handle via deferred registration is out of scope. Document that registration during global handler execution is unsupported.

---

## Implementation Notes

### `do_action` integration

After the existing name-specific callback loop:

```python
# After name-specific callbacks complete:
await self._run_global_hooks(hook_name, args, kwargs)
```

```python
async def _run_global_hooks(self, hook_name: str, args: tuple, kwargs: dict) -> None:
    if not self._global_hooks:
        return
    self._global_nesting += 1
    try:
        for priority in sorted(self._global_hooks.keys()):
            for callback_id, callback in list(self._global_hooks[priority]):
                if callback_id in self._removed_globals:
                    continue
                try:
                    await self._run_action_listener(
                        callback_id=callback_id,
                        hook_name=hook_name,
                        callback=callback,
                        args=(hook_name,) + args,
                        kwargs=kwargs,
                        timeout=self._action_timeout,
                    )
                except Exception:
                    # logged inside _run_action_listener
                    pass
    finally:
        self._global_nesting -= 1
        if self._global_nesting == 0:
            self._cleanup_global_removals()
```

Same pattern for `apply_filters` — the return value from global handlers is discarded.

### `has_global(callback_id)` (optional helper)

```python
def has_global(self, callback_id: str) -> bool:
    return callback_id in self._global_callback_ids
```

---

## Edge Cases

| Scenario | Behavior |
|---|---|
| No global handlers registered | Early return — zero overhead on hot path |
| Global handler raises | Logged, execution continues to next global handler |
| Global handler times out | Logged via `_run_action_listener` timeout path |
| Global handler calls `do_action` | Safe — no deadlock, reentrant |
| Global handler calls `unsubscribe_all(self_id)` during execution | Deferred removal, cleaned up after `_global_nesting` reaches 0 |
| `apply_filters` with global handler returning a value | Return value discarded, filter chain unchanged |
| `subscribe_all` with `detach=True` | Not supported in v1 — global handlers are always awaited. Document this. |

---

## What Doesn't Belong Here

- Global handlers should not intercept `subscribe_all` itself (meta-hooks are out of scope).
- No wildcard pattern matching (e.g., `subscribe_all("task.*")`) — that's a separate feature.
- No `subscribe_actions_only()` / `subscribe_filters_only()` variants — the use case (audit logging) needs both; add later if needed.

---

## Backwards Compatibility

Fully additive. Existing behavior unchanged. Zero overhead when no global handlers are registered (early return on empty `_global_hooks`).

---

## Test Plan

- `subscribe_all` handler fires on `do_action`
- `subscribe_all` handler fires on `apply_filters`
- Handler receives `event_name` as first arg
- Handler return value is ignored in filter chain
- Multiple global handlers execute in priority order
- Global handler runs after name-specific handlers
- `unsubscribe_all` removes the handler
- `unsubscribe_all` during execution defers removal
- Global handler exception is logged, doesn't break chain
- Zero overhead path: no global hooks → fast return
