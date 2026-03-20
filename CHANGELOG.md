# Changelog

## [0.2.0] - 2026-03-20

### Added

- **`on()` / `intercept()` / `off()`** — ergonomic aliases for `add_action()`, `add_filter()`, and a new universal removal method. `on()` signals observer intent; `intercept()` signals value transformation intent; `off()` removes either type by callback ID. ([#5])
- **`subscribe_all()` / `unsubscribe_all()` / `has_global()`** — wildcard subscriptions that fire for every `do_action()` and `apply_filters()` call, regardless of hook name. Global handlers receive the event name as their first argument; return values are always ignored (observer-only). Useful for audit logging, tracing, and monitoring. ([#4])
- **`registered_events()`** — returns the set of all hook names with at least one registered callback, across both action and filter registries. ([#3])
- **`describe(hook_name)`** — returns an ordered list of `HandlerInfo` TypedDicts describing every registered callback on a hook: callback ID, priority, handler name, module, hook type, detached flag, and accepted args. ([#3])
- **`describe_all()`** — convenience wrapper returning descriptors for all hooks, sorted by hook name. ([#3])
- **`register_schema(hook_name, schema)` / `schema_for(hook_name)`** — register a Pydantic model as the expected payload contract for a hook. ([#2])
- **`validate_payloads`** constructor flag and runtime-settable property — when `True`, `do_action()` and `apply_filters()` validate payloads against registered schemas before dispatch. Raises `HookPayloadError` on mismatch. Defaults to `False` (zero overhead in production). Pydantic is an optional dependency. ([#2])
- **`HookPayloadError`** — new exception raised on schema validation failures. Carries `hook_name`, `schema`, and `errors` attributes. ([#2])
- **`HandlerInfo`** — new `TypedDict` exported from the package, returned by `describe()`. ([#3])

### Changed

- `do_action()` and `apply_filters()` now invoke global wildcard handlers after name-specific handlers. No behavior change when no global handlers are registered.

### Backwards Compatible

All changes are additive. No existing public API has been modified or removed.

## [0.1.0] - 2026-03-05

Initial release — WordPress-style async hooks/filters for Python.

- `add_action()` / `do_action()` / `remove_action()` / `remove_all_actions()`
- `add_filter()` / `apply_filters()` / `remove_filter()` / `remove_all_filters()`
- Priority ordering, re-entrancy safety, deferred removal
- Detached (fire-and-forget) action listeners
- Per-callback and global timeouts
- `HookScope` / `HookContext` for async execution scoping via `contextvars`
