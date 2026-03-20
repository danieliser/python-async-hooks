"""AsyncHooks manager — WordPress-style async hooks/filters for Python."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections import defaultdict
from typing import Any, Callable, Coroutine, Literal, Optional
from uuid import uuid4

from async_hooks.scope import _current_scope
from async_hooks.types import (
    CallbackType,
    DuplicateCallbackError,
    HandlerInfo,
    HookPayloadError,
)


logger = logging.getLogger(__name__)

# Default listener timeout (30s for actions, no timeout for filters)
DEFAULT_ACTION_TIMEOUT_SECONDS = 30
DEFAULT_FILTER_TIMEOUT_SECONDS = None


CallbackCategory = Literal["action", "filter", "global"]


class AsyncHooks:
    """Manage async actions and filters with priority ordering and re-entrancy safety.

    Hook listeners can timeout. For actions, default is 30s. For filters, no timeout.
    If a listener times out, a WARNING is logged and execution continues to the
    next listener.

    Detached listeners (registered with detach=True) are fired as independent
    asyncio.Tasks. do_action does not await them — they run concurrently in the
    background. Each detached listener is fully independent: one failing or
    firing its own actions does not affect siblings or the caller.
    """

    def __init__(
        self,
        action_timeout_seconds: Optional[float] = DEFAULT_ACTION_TIMEOUT_SECONDS,
        filter_timeout_seconds: Optional[float] = DEFAULT_FILTER_TIMEOUT_SECONDS,
        validate_payloads: bool = False,
    ):
        """Initialize hook manager.

        Args:
            action_timeout_seconds: Default timeout per action listener (None = no timeout)
            filter_timeout_seconds: Default timeout per filter listener (None = no timeout)
            validate_payloads: If True, validate payloads against registered schemas at
                               emit time. Requires pydantic. Default False (production mode).
        """
        # Hook name → {priority: [(callback_id, callback), ...]}
        self._action_hooks: dict[str, dict[int, list[tuple[str, CallbackType]]]] = defaultdict(
            lambda: defaultdict(list)
        )
        self._filter_hooks: dict[str, dict[int, list[tuple[str, CallbackType]]]] = defaultdict(
            lambda: defaultdict(list)
        )

        # Hook name → set of callback_ids to remove after execution
        self._removed_actions: dict[str, set[str]] = defaultdict(set)
        self._removed_filters: dict[str, set[str]] = defaultdict(set)

        # Hook name → current nesting depth (for re-entrancy detection)
        self._action_nesting: dict[str, int] = defaultdict(int)
        self._filter_nesting: dict[str, int] = defaultdict(int)

        # Hook name → total invocation count
        self._action_call_count: dict[str, int] = defaultdict(int)
        self._filter_call_count: dict[str, int] = defaultdict(int)

        # Callback ID lookup helpers
        self._callback_registry: dict[str, CallbackType] = {}
        self._callback_hooks: dict[str, str] = {}
        self._callback_types: dict[str, CallbackCategory] = {}

        # Callback settings
        self._callback_timeouts: dict[str, Optional[float]] = {}
        self._filter_accepted_args: dict[str, int] = {}

        # Detached callback IDs — fired as independent asyncio.Tasks, not awaited
        self._detached_callbacks: set[str] = set()

        # Timeout configuration
        self._action_timeout = action_timeout_seconds
        self._filter_timeout = filter_timeout_seconds

        # Global wildcard hooks — fired for every event (issue #4)
        self._global_hooks: dict[int, list[tuple[str, CallbackType]]] = defaultdict(list)
        self._global_nesting: int = 0
        self._removed_globals: set[str] = set()

        # Schema registry for typed payload validation (issue #2)
        self._hook_schemas: dict[str, type] = {}
        self._validate_payloads: bool = validate_payloads

    # ─ Action Methods ────────────────────────────────────────────────────

    def add_action(
        self,
        hook_name: str,
        callback: CallbackType,
        priority: int = 10,
        timeout_seconds: Optional[float] = None,
        detach: bool = False,
    ) -> str:
        """Register an action callback.

        Args:
            hook_name: Name of the hook.
            callback: Async or sync callable. Signature: callback(*args, **kwargs).
            priority: Execution order (lower = higher priority). Default 10.
            timeout_seconds: Optional timeout for this specific callback (overrides default).
                             Ignored when detach=True — detached tasks are fire-and-forget.
            detach: If True, callback is fired as an independent asyncio.Task and not
                    awaited. do_action returns immediately without waiting for this
                    listener. Detached listeners run concurrently and independently —
                    one failing does not affect others or block the caller. Ideal for
                    long-running work (task dispatch, cleanup) that must not stall the
                    heartbeat. Detached listeners can themselves call do_action to chain
                    further actions without deadlocking.

        Returns:
            Unique callback_id for later removal.

        Raises:
            ValueError: If hook_name or callback is invalid.
            DuplicateCallbackError: If generated callback ID already exists.
        """
        if not hook_name or not isinstance(hook_name, str):
            raise ValueError("hook_name must be a non-empty string")
        if not callable(callback):
            raise ValueError("callback must be callable")
        if not isinstance(priority, int):
            raise ValueError("priority must be an integer")

        callback_id = str(uuid4())
        if callback_id in self._callback_registry:
            raise DuplicateCallbackError(
                f"duplicate callback id collision for hook action '{hook_name}': {callback_id}"
            )

        self._action_hooks[hook_name][priority].append((callback_id, callback))
        self._callback_registry[callback_id] = callback
        self._callback_hooks[callback_id] = hook_name
        self._callback_types[callback_id] = "action"

        if timeout_seconds is not None:
            self._callback_timeouts[callback_id] = timeout_seconds

        if detach:
            self._detached_callbacks.add(callback_id)

        logger.debug(
            "add_action hook=%s priority=%d callback_id=%s detach=%s",
            hook_name, priority, callback_id, detach,
        )
        return callback_id

    def on(
        self,
        hook_name: str,
        callback: CallbackType,
        priority: int = 10,
        timeout_seconds: Optional[float] = None,
        detach: bool = False,
    ) -> str:
        """Register an action callback. Ergonomic alias for add_action().

        Use this when you want to observe an event without transforming a value.
        Return values from the callback are ignored.
        """
        return self.add_action(
            hook_name, callback, priority=priority,
            timeout_seconds=timeout_seconds, detach=detach,
        )

    async def do_action(self, hook_name: str, *args, **kwargs) -> None:
        """Fire an action hook.

        Attached (default) callbacks are awaited in priority order (lower = first).
        Detached callbacks (registered with detach=True) are fired as independent
        asyncio.Tasks in priority order and not awaited — do_action returns without
        waiting for them.

        Callbacks can unhook themselves or others during execution (removal is deferred).
        If an attached callback times out or raises, it is logged and execution continues
        to the next callback. The hook chain is never broken.

        Global handlers registered via subscribe_all() fire after all name-specific
        callbacks, regardless of hook name.
        """
        has_specific = hook_name in self._action_hooks
        has_global = bool(self._global_hooks)

        if not has_specific and not has_global:
            logger.debug("do_action hook=%s no callbacks registered", hook_name)
            return

        if self._validate_payloads and hook_name in self._hook_schemas:
            payload = args[0] if args else kwargs
            self._validate_payload(hook_name, payload)

        self._action_call_count[hook_name] += 1
        self._action_nesting[hook_name] += 1

        scope = _current_scope.get(None)
        if scope is not None and hasattr(scope, "record_action"):
            try:
                scope.record_action(hook_name)
            except Exception:  # pragma: no cover - defensive
                logger.debug("do_action scope.record_action failed hook=%s", hook_name, exc_info=True)

        try:
            if has_specific:
                for priority in sorted(self._action_hooks[hook_name].keys()):
                    callbacks = self._action_hooks[hook_name][priority]
                    for callback_id, callback in list(callbacks):
                        if callback_id in self._removed_actions[hook_name]:
                            continue

                        if callback_id in self._detached_callbacks:
                            asyncio.create_task(
                                self._run_detached_listener(
                                    callback_id=callback_id,
                                    hook_name=hook_name,
                                    callback=callback,
                                    args=args,
                                    kwargs=kwargs,
                                ),
                                name=f"hook-{hook_name}-{callback_id[:8]}",
                            )
                        else:
                            timeout = self._callback_timeouts.get(callback_id, self._action_timeout)
                            try:
                                await self._run_action_listener(
                                    callback_id=callback_id,
                                    hook_name=hook_name,
                                    callback=callback,
                                    args=args,
                                    kwargs=kwargs,
                                    timeout=timeout,
                                )
                            except asyncio.TimeoutError:
                                logger.warning(
                                    "do_action timeout hook=%s callback=%s timeout_seconds=%s",
                                    hook_name,
                                    callback_id,
                                    timeout,
                                )
                            except Exception as error:
                                logger.error(
                                    "do_action exception hook=%s callback=%s error=%s",
                                    hook_name,
                                    callback_id,
                                    type(error).__name__,
                                    exc_info=True,
                                )

            if has_global:
                await self._run_global_hooks(hook_name, args, kwargs)

        finally:
            self._action_nesting[hook_name] -= 1
            if self._action_nesting[hook_name] == 0:
                self._cleanup_removals("action", hook_name)

    def remove_action(self, hook_name: str, callback_id: str) -> bool:
        """Remove an action callback.

        If called during execution, removal is deferred until after the hook completes.
        """
        if not callback_id:
            return False
        if (
            self._callback_types.get(callback_id) != "action"
            or self._callback_hooks.get(callback_id) != hook_name
        ):
            return False

        if self._action_nesting[hook_name] > 0:
            self._removed_actions[hook_name].add(callback_id)
            logger.debug(
                "remove_action deferred hook=%s callback_id=%s",
                hook_name,
                callback_id,
            )
            return True

        removed = self._remove_callback("action", hook_name, callback_id)
        if removed:
            logger.debug("remove_action hook=%s callback_id=%s", hook_name, callback_id)
        return removed

    def remove_all_actions(self, hook_name: str, priority: Optional[int] = None) -> bool:
        """Remove all actions from a hook, optionally limited by priority."""
        if hook_name not in self._action_hooks:
            logger.debug("remove_all_actions hook=%s nothing to remove", hook_name)
            return False

        if self._action_nesting[hook_name] > 0:
            ids = self._collect_callback_ids("action", hook_name, priority)
            if not ids:
                return False
            self._removed_actions[hook_name].update(ids)
            logger.debug(
                "remove_all_actions deferred hook=%s priority=%s count=%d",
                hook_name,
                str(priority),
                len(ids),
            )
            return True

        if priority is not None:
            callbacks = self._action_hooks[hook_name].get(priority)
            if not callbacks:
                return False
            for callback_id, _ in list(callbacks):
                self._remove_callback("action", hook_name, callback_id)
            if not self._action_hooks[hook_name]:
                self._action_hooks.pop(hook_name, None)
            logger.debug("remove_all_actions hook=%s priority=%s", hook_name, priority)
            return True

        for priority_value, callbacks in list(self._action_hooks[hook_name].items()):
            for callback_id, _ in list(callbacks):
                self._remove_callback("action", hook_name, callback_id)
            self._action_hooks[hook_name].pop(priority_value, None)

        self._action_hooks.pop(hook_name, None)
        logger.debug("remove_all_actions hook=%s removed all", hook_name)
        return True

    def has_action(self, hook_name: str, callback_id: Optional[str] = None) -> bool | int:
        """Check whether an action exists.

        If callback_id is provided, returns True/False for that callback.
        If omitted, returns count of callbacks on the hook.
        """
        if callback_id is not None:
            return (
                self._callback_types.get(callback_id) == "action"
                and self._callback_hooks.get(callback_id) == hook_name
                and callback_id in self._callback_registry
            )

        return (
            sum(len(cbs) for cbs in self._action_hooks[hook_name].values())
            if hook_name in self._action_hooks
            else 0
        )

    def is_detached(self, callback_id: str) -> bool:
        """Return True if the callback was registered with detach=True."""
        return callback_id in self._detached_callbacks

    def doing_action(self, hook_name: str) -> bool:
        """Check if an action is currently executing."""
        return self._action_nesting[hook_name] > 0

    def did_action(self, hook_name: str) -> int:
        """Get the number of times an action has executed."""
        return self._action_call_count.get(hook_name, 0)

    # ─ Filter Methods ────────────────────────────────────────────────────

    def add_filter(
        self,
        hook_name: str,
        callback: CallbackType,
        priority: int = 10,
        accepted_args: int = 1,
        timeout_seconds: Optional[float] = None,
    ) -> str:
        """Register a filter callback.

        callback(value, *args, **kwargs) -> filtered_value
        """
        if not hook_name or not isinstance(hook_name, str):
            raise ValueError("hook_name must be a non-empty string")
        if not callable(callback):
            raise ValueError("callback must be callable")
        if not isinstance(priority, int):
            raise ValueError("priority must be an integer")
        if not isinstance(accepted_args, int) or accepted_args < 0:
            raise ValueError("accepted_args must be a non-negative integer")

        callback_id = str(uuid4())
        if callback_id in self._callback_registry:
            raise DuplicateCallbackError(
                f"duplicate callback id collision for hook filter '{hook_name}': {callback_id}"
            )

        self._filter_hooks[hook_name][priority].append((callback_id, callback))
        self._callback_registry[callback_id] = callback
        self._callback_hooks[callback_id] = hook_name
        self._callback_types[callback_id] = "filter"
        self._filter_accepted_args[callback_id] = accepted_args

        if timeout_seconds is not None:
            self._callback_timeouts[callback_id] = timeout_seconds

        logger.debug(
            "add_filter hook=%s priority=%d callback_id=%s accepted_args=%d",
            hook_name,
            priority,
            callback_id,
            accepted_args,
        )
        return callback_id

    def intercept(
        self,
        hook_name: str,
        callback: CallbackType,
        priority: int = 10,
        accepted_args: int = 1,
        timeout_seconds: Optional[float] = None,
    ) -> str:
        """Register a filter callback. Ergonomic alias for add_filter().

        Use this when you want to transform a value passing through a hook.
        The callback must return the (possibly modified) value.
        """
        return self.add_filter(
            hook_name, callback, priority=priority,
            accepted_args=accepted_args, timeout_seconds=timeout_seconds,
        )

    async def apply_filters(self, hook_name: str, value: Any, *args, **kwargs) -> Any:
        """Apply a filter chain to a value.

        Global handlers registered via subscribe_all() fire after the filter chain
        with the final value. Their return values are ignored.
        """
        has_specific = hook_name in self._filter_hooks
        has_global = bool(self._global_hooks)

        if not has_specific and not has_global:
            logger.debug("apply_filters hook=%s no callbacks registered", hook_name)
            return value

        if self._validate_payloads and hook_name in self._hook_schemas:
            self._validate_payload(hook_name, value)

        self._filter_call_count[hook_name] += 1
        self._filter_nesting[hook_name] += 1

        scope = _current_scope.get(None)
        if scope is not None and hasattr(scope, "record_filter"):
            try:
                scope.record_filter(hook_name)
            except Exception:  # pragma: no cover - defensive
                logger.debug(
                    "apply_filters scope.record_filter failed hook=%s", hook_name, exc_info=True
                )

        try:
            current_value = value
            if has_specific:
                for priority in sorted(self._filter_hooks[hook_name].keys()):
                    callbacks = self._filter_hooks[hook_name][priority]
                    for callback_id, callback in list(callbacks):
                        if callback_id in self._removed_filters[hook_name]:
                            continue

                        timeout = self._callback_timeouts.get(callback_id, self._filter_timeout)
                        accepted_args = self._filter_accepted_args.get(callback_id, 1)
                        filtered_args = self._filter_args_for_callback(args=args, accepted_args=accepted_args)

                        try:
                            current_value = await self._run_filter_listener(
                                callback_id=callback_id,
                                hook_name=hook_name,
                                callback=callback,
                                current_value=current_value,
                                args=filtered_args,
                                kwargs=kwargs,
                                timeout=timeout,
                            )
                        except asyncio.TimeoutError:
                            logger.warning(
                                "apply_filters timeout hook=%s callback=%s timeout_seconds=%s",
                                hook_name,
                                callback_id,
                                timeout,
                            )
                        except Exception as error:
                            logger.error(
                                "apply_filters exception hook=%s callback=%s error=%s",
                                hook_name,
                                callback_id,
                                type(error).__name__,
                                exc_info=True,
                            )

            if has_global:
                await self._run_global_hooks(hook_name, (current_value,) + args, kwargs)

            return current_value
        finally:
            self._filter_nesting[hook_name] -= 1
            if self._filter_nesting[hook_name] == 0:
                self._cleanup_removals("filter", hook_name)

    def remove_filter(self, hook_name: str, callback_id: str) -> bool:
        """Remove a filter callback."""
        if not callback_id:
            return False
        if (
            self._callback_types.get(callback_id) != "filter"
            or self._callback_hooks.get(callback_id) != hook_name
        ):
            return False

        if self._filter_nesting[hook_name] > 0:
            self._removed_filters[hook_name].add(callback_id)
            logger.debug(
                "remove_filter deferred hook=%s callback_id=%s",
                hook_name,
                callback_id,
            )
            return True

        removed = self._remove_callback("filter", hook_name, callback_id)
        if removed:
            logger.debug("remove_filter hook=%s callback_id=%s", hook_name, callback_id)
        return removed

    def remove_all_filters(self, hook_name: str, priority: Optional[int] = None) -> bool:
        """Remove all filters from a hook, optionally filtered by priority."""
        if hook_name not in self._filter_hooks:
            logger.debug("remove_all_filters hook=%s nothing to remove", hook_name)
            return False

        if self._filter_nesting[hook_name] > 0:
            ids = self._collect_callback_ids("filter", hook_name, priority)
            if not ids:
                return False
            self._removed_filters[hook_name].update(ids)
            logger.debug(
                "remove_all_filters deferred hook=%s priority=%s count=%d",
                hook_name,
                str(priority),
                len(ids),
            )
            return True

        if priority is not None:
            callbacks = self._filter_hooks[hook_name].get(priority)
            if not callbacks:
                return False
            for callback_id, _ in list(callbacks):
                self._remove_callback("filter", hook_name, callback_id)
            if not self._filter_hooks[hook_name]:
                self._filter_hooks.pop(hook_name, None)
            logger.debug("remove_all_filters hook=%s priority=%s", hook_name, priority)
            return True

        for priority_value, callbacks in list(self._filter_hooks[hook_name].items()):
            for callback_id, _ in list(callbacks):
                self._remove_callback("filter", hook_name, callback_id)
            self._filter_hooks[hook_name].pop(priority_value, None)

        self._filter_hooks.pop(hook_name, None)
        logger.debug("remove_all_filters hook=%s removed all", hook_name)
        return True

    def has_filter(self, hook_name: str, callback_id: Optional[str] = None) -> bool | int:
        """Check whether a filter exists.

        If callback_id is provided, returns True/False for that callback.
        If omitted, returns count of callbacks on the hook.
        """
        if callback_id is not None:
            return (
                self._callback_types.get(callback_id) == "filter"
                and self._callback_hooks.get(callback_id) == hook_name
                and callback_id in self._callback_registry
            )

        return (
            sum(len(cbs) for cbs in self._filter_hooks[hook_name].values())
            if hook_name in self._filter_hooks
            else 0
        )

    def doing_filter(self, hook_name: str) -> bool:
        """Check if a filter is currently executing."""
        return self._filter_nesting[hook_name] > 0

    def did_filter(self, hook_name: str) -> int:
        """Get the number of times a filter has executed."""
        return self._filter_call_count.get(hook_name, 0)

    # ─ Universal Removal ─────────────────────────────────────────────────

    def off(self, hook_name: str, callback_id: str) -> bool:
        """Remove a callback by ID regardless of type. Counterpart to on().

        Routes to remove_action() or remove_filter() based on the registered type.
        Returns False for unknown or global callback IDs.
        """
        kind = self._callback_types.get(callback_id)
        if kind == "action":
            return self.remove_action(hook_name, callback_id)
        elif kind == "filter":
            return self.remove_filter(hook_name, callback_id)
        return False

    # ─ Global Wildcard Hooks ─────────────────────────────────────────────

    def subscribe_all(self, callback: CallbackType, priority: int = 90) -> str:
        """Register a handler that fires for every do_action() and apply_filters() call.

        The handler receives the event name as the first argument, followed by
        the original emission args:

            async def handler(event_name: str, *args, **kwargs) -> None: ...

        For filters, args[0] is the post-chain value. Return values are always ignored.
        Global handlers are observers — they cannot transform filter values.

        Args:
            callback: Async or sync callable.
            priority: Execution order among global handlers. Default 90 (runs after
                      most domain-specific handlers at priority 10).

        Returns:
            Unique callback_id for use with unsubscribe_all().
        """
        if not callable(callback):
            raise ValueError("callback must be callable")
        if not isinstance(priority, int):
            raise ValueError("priority must be an integer")

        callback_id = str(uuid4())
        self._global_hooks[priority].append((callback_id, callback))
        self._callback_registry[callback_id] = callback
        self._callback_types[callback_id] = "global"

        logger.debug("subscribe_all priority=%d callback_id=%s", priority, callback_id)
        return callback_id

    def unsubscribe_all(self, callback_id: str) -> bool:
        """Remove a global wildcard handler registered via subscribe_all().

        If called during global handler execution, removal is deferred.
        Returns False if callback_id is not a global handler.
        """
        if self._callback_types.get(callback_id) != "global":
            return False

        if self._global_nesting > 0:
            self._removed_globals.add(callback_id)
            logger.debug("unsubscribe_all deferred callback_id=%s", callback_id)
            return True

        return self._remove_global_callback(callback_id)

    def has_global(self, callback_id: str) -> bool:
        """Return True if callback_id is a registered global handler."""
        return (
            self._callback_types.get(callback_id) == "global"
            and callback_id in self._callback_registry
        )

    # ─ Introspection API ─────────────────────────────────────────────────

    def registered_events(self) -> set[str]:
        """Return the set of all hook names with at least one registered callback."""
        return set(self._action_hooks.keys()) | set(self._filter_hooks.keys())

    def describe(self, hook_name: str) -> list[HandlerInfo]:
        """Return ordered descriptors for all callbacks registered on hook_name.

        Results are sorted by priority (ascending), then registration order.
        Covers both action and filter registries. Excludes callbacks pending
        deferred removal.

        Returns an empty list if no callbacks are registered for hook_name.
        """
        result: list[HandlerInfo] = []
        pending_action_removal = self._removed_actions.get(hook_name, set())
        pending_filter_removal = self._removed_filters.get(hook_name, set())

        if hook_name in self._action_hooks:
            for priority in sorted(self._action_hooks[hook_name].keys()):
                for callback_id, callback in self._action_hooks[hook_name][priority]:
                    if callback_id in pending_action_removal:
                        continue
                    result.append(HandlerInfo(
                        callback_id=callback_id,
                        hook_name=hook_name,
                        hook_type="action",
                        priority=priority,
                        handler_name=self._resolve_handler_name(callback),
                        module=self._resolve_module(callback),
                        detached=callback_id in self._detached_callbacks,
                        accepted_args=1,
                    ))

        if hook_name in self._filter_hooks:
            for priority in sorted(self._filter_hooks[hook_name].keys()):
                for callback_id, callback in self._filter_hooks[hook_name][priority]:
                    if callback_id in pending_filter_removal:
                        continue
                    result.append(HandlerInfo(
                        callback_id=callback_id,
                        hook_name=hook_name,
                        hook_type="filter",
                        priority=priority,
                        handler_name=self._resolve_handler_name(callback),
                        module=self._resolve_module(callback),
                        detached=False,
                        accepted_args=self._filter_accepted_args.get(callback_id, 1),
                    ))

        return result

    def describe_all(self) -> list[HandlerInfo]:
        """Return descriptors for all callbacks across all hooks, sorted by hook name."""
        result: list[HandlerInfo] = []
        for hook_name in sorted(self.registered_events()):
            result.extend(self.describe(hook_name))
        return result

    # ─ Typed Payload Validation ──────────────────────────────────────────

    @property
    def validate_payloads(self) -> bool:
        """Whether payload schema validation is active. Toggle without recreating instance."""
        return self._validate_payloads

    @validate_payloads.setter
    def validate_payloads(self, value: bool) -> None:
        self._validate_payloads = value

    def register_schema(self, hook_name: str, schema: type) -> None:
        """Register a Pydantic model as the expected payload schema for hook_name.

        When validate_payloads=True, do_action() validates args[0] (or kwargs if no
        positional args) and apply_filters() validates the filter value against this
        schema before dispatching to any callbacks.

        Raises ImportError if Pydantic is not installed.
        Raises ValueError if hook_name is invalid.
        """
        try:
            import pydantic  # noqa: F401
        except ImportError:
            raise ImportError(
                "Pydantic is required for typed payloads: pip install pydantic"
            )
        if not hook_name or not isinstance(hook_name, str):
            raise ValueError("hook_name must be a non-empty string")

        self._hook_schemas[hook_name] = schema
        logger.debug("register_schema hook=%s schema=%s", hook_name, schema.__name__)

    def schema_for(self, hook_name: str) -> Optional[type]:
        """Return the registered Pydantic schema for hook_name, or None."""
        return self._hook_schemas.get(hook_name)

    # ─ Context Methods ───────────────────────────────────────────────────

    def scope(self, name: str = "", **metadata: Any) -> "HookScope":
        """Create an execution scope for implicit callback context."""
        from async_hooks.scope import HookScope

        return HookScope(self, name=name, **metadata)

    @property
    def current_scope(self) -> Optional["HookScope"]:
        """Get the active scope (if any) for the current async task."""
        from async_hooks.scope import HookScope

        return _current_scope.get()

    # ─ Internal Methods ──────────────────────────────────────────────────

    async def _run_global_hooks(self, hook_name: str, args: tuple, kwargs: dict) -> None:
        """Execute all global handlers for a given event. Return values are discarded."""
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
                    except asyncio.TimeoutError:
                        logger.warning(
                            "global_hook timeout hook=%s callback=%s",
                            hook_name, callback_id,
                        )
                    except Exception as error:
                        logger.error(
                            "global_hook exception hook=%s callback=%s error=%s",
                            hook_name, callback_id, type(error).__name__,
                            exc_info=True,
                        )
        finally:
            self._global_nesting -= 1
            if self._global_nesting == 0:
                self._cleanup_global_removals()

    def _remove_global_callback(self, callback_id: str) -> bool:
        removed = False
        for priority, callbacks in list(self._global_hooks.items()):
            before = len(callbacks)
            callbacks[:] = [(cid, cb) for cid, cb in callbacks if cid != callback_id]
            if len(callbacks) != before:
                removed = True
            if not callbacks:
                self._global_hooks.pop(priority, None)

        if removed:
            self._callback_registry.pop(callback_id, None)
            self._callback_types.pop(callback_id, None)
            self._removed_globals.discard(callback_id)
        return removed

    def _cleanup_global_removals(self) -> None:
        for callback_id in set(self._removed_globals):
            self._remove_global_callback(callback_id)
        self._removed_globals.clear()

    def _validate_payload(self, hook_name: str, payload: Any) -> None:
        """Validate payload against the registered schema. Raises HookPayloadError on mismatch."""
        schema = self._hook_schemas.get(hook_name)
        if schema is None:
            return
        try:
            schema.model_validate(payload)
        except Exception as exc:
            errors = exc.errors() if hasattr(exc, "errors") else [str(exc)]
            raise HookPayloadError(hook_name, schema, errors) from exc

    @staticmethod
    def _resolve_handler_name(callback: Callable) -> str:
        inner = getattr(callback, "func", callback)  # unwrap functools.partial
        return getattr(inner, "__qualname__", None) or getattr(inner, "__name__", "<unknown>")

    @staticmethod
    def _resolve_module(callback: Callable) -> str:
        inner = getattr(callback, "func", callback)
        return getattr(inner, "__module__", "<unknown>") or "<unknown>"

    async def _run_detached_listener(
        self,
        callback_id: str,
        hook_name: str,
        callback: Callable[..., Coroutine[Any, Any, Any] | Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> None:
        """Execute one detached listener as an independent task. Errors are logged, never raised."""
        start_ms = asyncio.get_running_loop().time() * 1000
        try:
            if inspect.iscoroutinefunction(callback):
                await callback(*args, **kwargs)
            else:
                result = callback(*args, **kwargs)
                if inspect.isawaitable(result):
                    await result
        except Exception:
            logger.error(
                "do_action detached exception hook=%s callback=%s",
                hook_name,
                callback_id,
                exc_info=True,
            )
        finally:
            duration_ms = (asyncio.get_running_loop().time() * 1000) - start_ms
            if duration_ms > 500:
                logger.debug(
                    "do_action detached slow hook=%s callback=%s duration_ms=%.1f",
                    hook_name,
                    callback_id,
                    duration_ms,
                )

    async def _run_action_listener(
        self,
        callback_id: str,
        hook_name: str,
        callback: Callable[..., Coroutine[Any, Any, Any] | Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        timeout: Optional[float],
    ) -> None:
        """Execute one attached action listener with optional timeout."""
        start_ms = asyncio.get_running_loop().time() * 1000
        try:
            if inspect.iscoroutinefunction(callback):
                if timeout is not None:
                    await asyncio.wait_for(callback(*args, **kwargs), timeout=timeout)
                else:
                    await callback(*args, **kwargs)
            else:
                result = callback(*args, **kwargs)
                if inspect.isawaitable(result):
                    if timeout is not None:
                        await asyncio.wait_for(result, timeout=timeout)
                    else:
                        await result
        finally:
            duration_ms = (asyncio.get_running_loop().time() * 1000) - start_ms
            if duration_ms > 100:
                logger.debug(
                    "do_action slow callback hook=%s callback=%s duration_ms=%.1f",
                    hook_name,
                    callback_id,
                    duration_ms,
                )

    async def _run_filter_listener(
        self,
        callback_id: str,
        hook_name: str,
        callback: Callable[..., Coroutine[Any, Any, Any] | Any],
        current_value: Any,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        timeout: Optional[float],
    ) -> Any:
        """Execute one filter listener and return transformed value."""
        start_ms = asyncio.get_running_loop().time() * 1000

        try:
            if inspect.iscoroutinefunction(callback):
                if timeout is not None:
                    result = await asyncio.wait_for(
                        callback(current_value, *args, **kwargs), timeout=timeout
                    )
                else:
                    result = await callback(current_value, *args, **kwargs)
            else:
                result = callback(current_value, *args, **kwargs)
                if inspect.isawaitable(result):
                    if timeout is not None:
                        result = await asyncio.wait_for(result, timeout=timeout)
                    else:
                        result = await result
            return result
        finally:
            duration_ms = (asyncio.get_running_loop().time() * 1000) - start_ms
            if duration_ms > 100:
                logger.debug(
                    "apply_filters slow callback hook=%s callback=%s duration_ms=%.1f",
                    hook_name,
                    callback_id,
                    duration_ms,
                )

    @staticmethod
    def _filter_args_for_callback(args: tuple[Any, ...], accepted_args: int) -> tuple[Any, ...]:
        """Respect accepted_args by trimming positional callback arguments.

        accepted_args includes the primary filtered value argument.
        """
        extra_allowed = max(accepted_args - 1, 0)
        return args[:extra_allowed]

    def _collect_callback_ids(
        self,
        kind: CallbackCategory,
        hook_name: str,
        priority: Optional[int] = None,
    ) -> list[str]:
        hooks = self._action_hooks if kind == "action" else self._filter_hooks
        if priority is None:
            ids: list[str] = []
            for callbacks in hooks[hook_name].values():
                ids.extend(callback_id for callback_id, _ in callbacks)
            return ids

        callbacks = hooks[hook_name].get(priority)
        if not callbacks:
            return []
        return [callback_id for callback_id, _ in callbacks]

    def _remove_callback(self, kind: CallbackCategory, hook_name: str, callback_id: str) -> bool:
        hooks = self._action_hooks if kind == "action" else self._filter_hooks
        callbacks_dict = hooks.get(hook_name)
        if not callbacks_dict:
            return False

        removed = False
        for priority, callbacks in list(callbacks_dict.items()):
            before = len(callbacks)
            callbacks[:] = [(cid, cb) for cid, cb in callbacks if cid != callback_id]
            if len(callbacks) != before:
                removed = True
            if not callbacks:
                callbacks_dict.pop(priority, None)

        if not callbacks_dict:
            hooks.pop(hook_name, None)

        if removed:
            self._callback_registry.pop(callback_id, None)
            self._callback_hooks.pop(callback_id, None)
            self._callback_types.pop(callback_id, None)
            self._callback_timeouts.pop(callback_id, None)
            self._filter_accepted_args.pop(callback_id, None)
            self._detached_callbacks.discard(callback_id)
            self._removed_actions[hook_name].discard(callback_id)
            self._removed_filters[hook_name].discard(callback_id)
        return removed

    def _cleanup_removals(self, kind: CallbackCategory, hook_name: str) -> None:
        if not hook_name:
            return

        removed_bucket = self._removed_actions if kind == "action" else self._removed_filters
        hooks = self._action_hooks if kind == "action" else self._filter_hooks
        removed_ids = removed_bucket.get(hook_name, set())

        if not removed_ids or hook_name not in hooks:
            removed_bucket.pop(hook_name, None)
            return

        for priority, callbacks in list(hooks[hook_name].items()):
            callbacks[:] = [(cid, cb) for cid, cb in callbacks if cid not in removed_ids]
            if not callbacks:
                hooks[hook_name].pop(priority, None)

        if not hooks[hook_name]:
            hooks.pop(hook_name, None)

        for callback_id in removed_ids:
            self._callback_registry.pop(callback_id, None)
            self._callback_hooks.pop(callback_id, None)
            self._callback_types.pop(callback_id, None)
            self._callback_timeouts.pop(callback_id, None)
            self._filter_accepted_args.pop(callback_id, None)
            self._detached_callbacks.discard(callback_id)

        removed_bucket.pop(hook_name, None)
