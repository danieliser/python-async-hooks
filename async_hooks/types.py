"""Type definitions and exceptions for async_hooks."""

from typing import Callable, Coroutine, Any


class HookError(Exception):
    """Base exception for hook system errors."""

    pass


class HookNotFoundError(HookError):
    """Raised when trying to remove a callback that doesn't exist."""

    pass


class DuplicateCallbackError(HookError):
    """Raised when attempting to register the same callback ID twice."""

    pass


class HookTimeoutError(HookError):
    """Raised when a hook listener exceeds its timeout."""

    pass


# Callback signature for actions: async def callback(*args, **kwargs)
# Callback signature for filters: async def callback(value, *args, **kwargs) -> Any

CallbackType = Callable[..., Coroutine[Any, Any, Any] | Any]
"""Union type for sync or async callables. Both supported."""
