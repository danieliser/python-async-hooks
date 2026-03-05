"""Hook execution context for async-hooks.

The scope is tracked via :mod:`contextvars` so it is safe across asyncio tasks.
"""

from __future__ import annotations

import contextvars
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from .manager import AsyncHooks

_current_scope: contextvars.ContextVar[Optional["HookScope"]] = contextvars.ContextVar(
    "current_hook_scope", default=None
)


class HookScope:
    """Async context manager that tracks implicit execution scope."""

    def __init__(self, hooks: AsyncHooks, name: str = "", **metadata: Any):
        self.hooks = hooks
        self.name = name
        self.metadata = metadata
        self._token: Optional[contextvars.Token[Optional["HookScope"]]] = None
        self._parent: Optional["HookScope"] = None

    async def __aenter__(self) -> "HookScope":
        self._parent = _current_scope.get(None)
        self._token = _current_scope.set(self)
        return self

    async def __aexit__(self, *exc_info) -> None:
        if self._token is not None:
            _current_scope.reset(self._token)

    @staticmethod
    def current() -> Optional["HookScope"]:
        return _current_scope.get(None)

    @property
    def parent(self) -> Optional["HookScope"]:
        return self._parent
