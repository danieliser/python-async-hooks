# python-async-hooks

WordPress-style async hooks and filters for Python — `add_action`, `do_action`, `add_filter`, `apply_filters` — with priority ordering, re-entrancy safety, mixed sync/async callbacks, and execution scopes.

## Why

If you've built anything in WordPress, you know the power of its hooks system: components register interest in events without knowing about each other, and everything composes cleanly. This package brings that pattern to async Python.

Instead of monolithic `if/elif` dispatch blocks, each module registers its own handlers at startup. The core never needs to know what's listening.

## Install

```bash
pip install python-async-hooks
```

Requires Python 3.10+. Zero dependencies.

## Quick Start

```python
import asyncio
from async_hooks import AsyncHooks

hooks = AsyncHooks()

# Actions — fire-and-forget notifications
hooks.add_action("request.received", lambda req: print(f"Got request: {req}"))

async def log_request(req):
    await asyncio.sleep(0)  # async handlers work too
    print(f"Logged: {req}")

hooks.add_action("request.received", log_request, priority=5)  # runs first

await hooks.do_action("request.received", {"path": "/api/tools"})


# Filters — transform a value through a chain
hooks.add_filter("active_tools", lambda tools: [t for t in tools if t.get("enabled")])

async def inject_context_tools(tools, context):
    if context.get("role") == "admin":
        tools.append({"name": "debug", "enabled": True})
    return tools

hooks.add_filter("active_tools", inject_context_tools, accepted_args=2)

tools = await hooks.apply_filters("active_tools", all_tools, {"role": "admin"})
```

## API

### Actions

```python
# Register
callback_id = hooks.add_action(hook_name, callback, priority=10, timeout_seconds=None)

# Fire
await hooks.do_action(hook_name, *args, **kwargs)

# Remove
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
# Register
callback_id = hooks.add_filter(hook_name, callback, priority=10, accepted_args=1, timeout_seconds=None)

# Apply
result = await hooks.apply_filters(hook_name, value, *args, **kwargs)

# Remove
hooks.remove_filter(hook_name, callback_id)
hooks.remove_all_filters(hook_name, priority=None)

# Inspect
hooks.has_filter(hook_name)
hooks.has_filter(hook_name, cb_id)
hooks.doing_filter(hook_name)
hooks.did_filter(hook_name)
```

### Scopes

Scopes provide execution context that callbacks can read without explicit argument passing. They're async context managers backed by `contextvars` — safe across concurrent tasks.

```python
async with hooks.scope("request", user_id=42, tenant="acme") as scope:
    await hooks.do_action("request.start")
    # callbacks can call hooks.current_scope to read metadata
```

Scopes nest — inner scopes expose their parent.

## Behavior

- **Priority**: lower number = higher priority. Default is 10. Callbacks at the same priority run in registration order.
- **Mixed sync/async**: sync and async callbacks can coexist on the same hook.
- **Re-entrancy**: hooks can fire themselves recursively. Removals during execution are deferred until the hook completes.
- **Timeouts**: actions default to 30s per callback. Filters have no default timeout. Both are configurable per-hook-manager or per-callback. Timed-out callbacks log a warning and the chain continues.
- **Exceptions**: a failing callback logs an error and the chain continues. Hooks never propagate listener exceptions.
- **`accepted_args`**: filter callbacks declare how many positional args they accept (including the filtered value). Extra args are trimmed automatically.

## Use Case: Context-Aware Tool Loading

```python
# core.py — knows nothing about specific tools
tools = await hooks.apply_filters("active_tools", [], context)

# search_tools.py — registers itself
def register(hooks):
    hooks.add_filter("active_tools", lambda tools, ctx: tools + SEARCH_TOOLS, accepted_args=2)

# admin_tools.py — conditional registration
def register(hooks):
    async def maybe_add(tools, ctx):
        if ctx.get("role") == "admin":
            return tools + ADMIN_TOOLS
        return tools
    hooks.add_filter("active_tools", maybe_add, accepted_args=2, priority=20)
```

Each tool group owns its registration. The core stays clean.

## License

MIT
