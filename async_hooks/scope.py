"""Hook execution context for async-hooks."""

from __future__ import annotations

import contextvars
from typing import TYPE_CHECKING, Any, Optional

from dataclasses import dataclass, field


if TYPE_CHECKING:
    from .manager import AsyncHooks


_current_scope: contextvars.ContextVar["HookScope | None"] = contextvars.ContextVar(
    "current_hook_scope", default=None
)


@dataclass
class HookContext:
    """Internal execution context — tracks what has fired within a scope."""

    metadata: dict[str, Any] = field(default_factory=dict)
    _actions_fired: dict[str, int] = field(default_factory=dict, repr=False)
    _filters_applied: dict[str, int] = field(default_factory=dict, repr=False)

    def __init__(self, **kwargs: Any):
        self.metadata = kwargs
        self._actions_fired = {}
        self._filters_applied = {}

    def record_action(self, hook_name: str) -> None:
        """Called internally when an action fires."""
        self._actions_fired[hook_name] = self._actions_fired.get(hook_name, 0) + 1

    def record_filter(self, hook_name: str) -> None:
        """Called internally when a filter is applied."""
        self._filters_applied[hook_name] = self._filters_applied.get(hook_name, 0) + 1

    def did_action(self, hook_name: str) -> int:
        """How many times has this action fired within this scope?"""
        return self._actions_fired.get(hook_name, 0)

    def did_filter(self, hook_name: str) -> int:
        """How many times has this filter been applied within this scope?"""
        return self._filters_applied.get(hook_name, 0)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return self.metadata[name]
        except KeyError:
            raise AttributeError(f"HookContext has no attribute '{name}'")


class HookScope:
    """Implicit execution scope — tracks hooks fired within an async with block."""

    def __init__(self, hooks_instance: "AsyncHooks", name: str = "", **metadata: Any):
        self.hooks = hooks_instance
        self.name = name
        self._ctx = HookContext(**metadata)
        self._token: Optional[contextvars.Token["HookScope | None"]] = None
        self._parent: Optional["HookScope"] = None

    async def __aenter__(self) -> "HookScope":
        self._parent = _current_scope.get(None)
        self._token = _current_scope.set(self)
        return self

    async def __aexit__(self, *exc_info) -> None:
        if self._token is not None:
            _current_scope.reset(self._token)

    @property
    def parent(self) -> Optional["HookScope"]:
        return self._parent

    def did_action(self, hook_name: str) -> int:
        return self._ctx.did_action(hook_name)

    def did_filter(self, hook_name: str) -> int:
        return self._ctx.did_filter(hook_name)

    def record_action(self, hook_name: str) -> None:
        self._ctx.record_action(hook_name)

    def record_filter(self, hook_name: str) -> None:
        self._ctx.record_filter(hook_name)

    def doing_action(self, hook_name: str) -> bool:
        """Whether the hook is currently executing globally."""
        return self.hooks.doing_action(hook_name)

    def doing_filter(self, hook_name: str) -> bool:
        """Whether the filter is currently executing globally."""
        return self.hooks.doing_filter(hook_name)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._ctx, name)
