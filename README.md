# python-async-hooks

WordPress-style async hooks and filters for Python — `add_action`, `do_action`, `add_filter`, `apply_filters` — with priority ordering, re-entrancy safety, mixed sync/async callbacks, execution scopes, wildcard subscriptions, namespace support, introspection, and optional typed payload validation.

## Why

If you've built anything in WordPress, you know the power of its hooks system: components register interest in events without knowing about each other, and everything composes cleanly. This package brings that pattern to async Python.

Instead of monolithic `if/elif` dispatch blocks, each module registers its own handlers at startup. The core never needs to know what's listening.

## Install

```bash
pip install python-async-hooks
```

Requires Python 3.10+. Zero required dependencies. Install `pydantic` to use typed payload validation.

## Quick Start

```python
import asyncio
from async_hooks import AsyncHooks

hooks = AsyncHooks()

# Actions — fire-and-forget observers
hooks.on("request.received", lambda req: print(f"Got request: {req}"))

async def log_request(req):
    await asyncio.sleep(0)
    print(f"Logged: {req}")

hooks.on("request.received", log_request, priority=5)  # runs first

await hooks.do_action("request.received", {"path": "/api/tools"})


# Filters — transform a value through a chain
hooks.intercept("active_tools", lambda tools: [t for t in tools if t.get("enabled")])

async def inject_context_tools(tools, context):
    if context.get("role") == "admin":
        tools.append({"name": "debug", "enabled": True})
    return tools

hooks.intercept("active_tools", inject_context_tools, accepted_args=2)

tools = await hooks.apply_filters("active_tools", all_tools, {"role": "admin"})
```

## API

### Actions

```python
# Register — two equivalent spellings
callback_id = hooks.on(hook_name, callback, priority=10, timeout_seconds=None, detach=False)
callback_id = hooks.add_action(hook_name, callback, priority=10, timeout_seconds=None, detach=False)

# Fire
await hooks.do_action(hook_name, *args, **kwargs)

# Remove
hooks.off(hook_name, callback_id)             # universal removal (works for actions and filters)
hooks.remove_action(hook_name, callback_id)
hooks.remove_all_actions(hook_name, priority=None)

# Inspect
hooks.has_action(hook_name)           # → count of callbacks
hooks.has_action(hook_name, cb_id)    # → bool
hooks.doing_action(hook_name)         # → bool (currently executing)
hooks.did_action(hook_name)           # → int (total executions)
```

### Filters

```python
# Register — two equivalent spellings
callback_id = hooks.intercept(hook_name, callback, priority=10, accepted_args=1, timeout_seconds=None)
callback_id = hooks.add_filter(hook_name, callback, priority=10, accepted_args=1, timeout_seconds=None)

# Apply
result = await hooks.apply_filters(hook_name, value, *args, **kwargs)

# Remove
hooks.off(hook_name, callback_id)             # universal removal
hooks.remove_filter(hook_name, callback_id)
hooks.remove_all_filters(hook_name, priority=None)

# Inspect
hooks.has_filter(hook_name)
hooks.has_filter(hook_name, cb_id)
hooks.doing_filter(hook_name)
hooks.did_filter(hook_name)
```

`on()` / `intercept()` are ergonomic aliases that make intent explicit: `on()` = observer (return value ignored), `intercept()` = transformer (must return the value).

### Wildcard Subscriptions

Subscribe to every event, or every event within a namespace:

```python
# Fires for every do_action() and apply_filters() call
callback_id = hooks.subscribe_all(handler, priority=90)

# Fires only for hooks matching "task" or "task.*"
callback_id = hooks.subscribe_all(handler, namespace="task")

hooks.unsubscribe_all(callback_id)
hooks.has_global(callback_id)  # → bool
```

Global handler signature — event name is always the first argument:

```python
async def audit_handler(event_name: str, *args, **kwargs) -> None:
    ...
```

For filter events, `args[0]` is the post-chain value. Return values from global handlers are always ignored — they're observers, not transformers.

Global handlers fire **after** all name-specific callbacks for a given event.

### Namespaces

Hook names are dot-delimited by convention (`task.created`, `task.lifecycle.start`). The prefix before the first dot is the namespace. Several APIs accept a `namespace` argument for scoped operations:

```python
# Scoped wildcard — fires for task.*, not config.*
hooks.subscribe_all(handler, namespace="task")

# Filter event catalog to a namespace
hooks.registered_events(namespace="task")
# → {"task.created", "task.completed", "task.dispatch"}

# Filter introspection to a namespace
hooks.describe_all(namespace="task")

# Remove all callbacks across every hook in a namespace — useful for plugin teardown
hooks.remove_namespace("task")  # → int (number of hooks cleared)
```

Namespace matching is exact-prefix only: `"task"` matches `"task"` and `"task.created"` but **not** `"taskrunner.start"`.

**Plugin pattern:**

```python
class MyPlugin:
    NAMESPACE = "my_plugin"

    def register(self, hooks):
        hooks.on(f"{self.NAMESPACE}.task.created", self.handle_task)
        hooks.subscribe_all(self.audit, namespace=self.NAMESPACE)

    def unregister(self, hooks):
        hooks.remove_namespace(self.NAMESPACE)
```

### Introspection

```python
# All registered hook names
hooks.registered_events()                    # → set[str]
hooks.registered_events(namespace="task")   # → set[str], filtered

# Ordered list of HandlerInfo dicts for a hook
hooks.describe("task.created")
# → [{"callback_id": "...", "hook_type": "action", "priority": 10,
#      "handler_name": "handle_task", "module": "my_plugin.tasks",
#      "detached": False, "accepted_args": 1}, ...]

# All hooks combined, sorted by hook name
hooks.describe_all()
hooks.describe_all(namespace="task")
```

`HandlerInfo` is a `TypedDict` exported from the package root.

### Typed Payload Validation

Register a Pydantic model as the expected payload contract for a hook. Validation runs at emit time, before any callbacks are called:

```python
from pydantic import BaseModel

class TaskPayload(BaseModel):
    task_id: str
    priority: int = 10

# Opt-in at construction or toggle at runtime
hooks = AsyncHooks(validate_payloads=True)
hooks.register_schema("task.created", TaskPayload)
hooks.schema_for("task.created")   # → TaskPayload

# Valid payload — passes through
await hooks.do_action("task.created", {"task_id": "t1", "priority": 5})

# Invalid payload — raises HookPayloadError before any callback fires
await hooks.do_action("task.created", {"wrong_field": "x"})
```

`validate_payloads` defaults to `False` (zero overhead in production). Toggle at runtime: `hooks.validate_payloads = True`. Pydantic is an optional dependency — only required if you call `register_schema()`.

`HookPayloadError` is exported from the package root and carries `hook_name`, `schema`, and `errors` attributes.

### Scopes

Scopes provide execution context that callbacks can read without explicit argument passing. Backed by `contextvars` — safe across concurrent tasks:

```python
async with hooks.scope("request", user_id=42, tenant="acme") as scope:
    await hooks.do_action("request.start")
    # callbacks can call hooks.current_scope to read metadata
```

Scopes nest — inner scopes expose their parent.

### Detached Listeners

Fire a callback as an independent `asyncio.Task` without blocking the caller:

```python
hooks.on("task.dispatched", heavy_work, detach=True)
# do_action returns immediately; heavy_work runs in the background
```

Detached listeners are fully isolated — one failing doesn't affect others.

## Behavior

- **Priority**: lower number = higher priority. Default is 10. Same-priority callbacks run in registration order.
- **Mixed sync/async**: sync and async callbacks coexist on the same hook.
- **Re-entrancy**: hooks can fire themselves recursively. Removals during execution are deferred until the hook completes.
- **Timeouts**: actions default to 30s per callback. Filters have no default timeout. Both are configurable per-manager or per-callback. Timed-out callbacks log a warning and the chain continues.
- **Exceptions**: a failing callback logs an error and the chain continues. Hooks never propagate listener exceptions to the caller.
- **`accepted_args`**: filter callbacks declare how many positional args they accept (including the filtered value). Extra args are trimmed automatically.

## Use Case: Plugin Architecture

```python
# core.py — knows nothing about specific plugins
tools = await hooks.apply_filters("active_tools", [], context)

# search_plugin.py
class SearchPlugin:
    NAMESPACE = "search"

    def register(self, hooks):
        hooks.intercept("active_tools", self.inject_tools, accepted_args=2)
        hooks.subscribe_all(self.trace, namespace=self.NAMESPACE)

    def unregister(self, hooks):
        hooks.remove_namespace(self.NAMESPACE)

    async def inject_tools(self, tools, ctx):
        return tools + SEARCH_TOOLS

    async def trace(self, event_name, *args, **kwargs):
        logger.debug("search plugin event: %s", event_name)
```

Each plugin owns its namespace. Teardown is one call.

## License

MIT
