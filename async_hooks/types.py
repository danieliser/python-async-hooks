"""Type definitions and exceptions for async_hooks."""

from typing import Any, Callable, Coroutine, Literal, TypedDict


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


class HookPayloadError(HookError):
    """Raised when a hook payload fails schema validation (validate_payloads=True)."""

    def __init__(self, hook_name: str, schema: type, errors: list) -> None:
        self.hook_name = hook_name
        self.schema = schema
        self.errors = errors
        super().__init__(
            f"Payload validation failed for hook '{hook_name}' "
            f"against {schema.__name__}: {errors}"
        )


class HandlerInfo(TypedDict):
    """Descriptor for a single registered callback, returned by describe()."""

    callback_id: str
    hook_name: str
    hook_type: Literal["action", "filter"]
    priority: int
    handler_name: str   # callback.__qualname__ or "<lambda>" / "<unknown>"
    module: str         # callback.__module__ or "<unknown>"
    detached: bool      # True if registered with detach=True (actions only)
    accepted_args: int  # always 1 for actions; respects accepted_args for filters


# Callback signature for actions: async def callback(*args, **kwargs)
# Callback signature for filters: async def callback(value, *args, **kwargs) -> Any

CallbackType = Callable[..., Coroutine[Any, Any, Any] | Any]
"""Union type for sync or async callables. Both supported."""
