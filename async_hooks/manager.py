"""AsyncHooks manager — WordPress-style async hooks/filters for Python."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections import defaultdict
from typing import Any, Callable, Coroutine, Literal, Optional

from async_hooks.scope import _current_scope
from async_hooks.types import (
    CallbackType,
    DuplicateCallbackError,
)
from uuid import uuid4


logger = logging.getLogger(__name__)

# Default listener timeout (30s for actions, no timeout for filters)
DEFAULT_ACTION_TIMEOUT_SECONDS = 30
DEFAULT_FILTER_TIMEOUT_SECONDS = None


CallbackCategory = Literal["action", "filter"]


class AsyncHooks:
    """Manage async actions and filters with priority ordering and re-entrancy safety.

    Hook listeners can timeout. For actions, default is 30s. For filters, no timeout.
    If a listener times out, a WARNING is logged and execution continues to the
    next listener.
    """

    def __init__(
        self,
        action_timeout_seconds: Optional[float] = DEFAULT_ACTION_TIMEOUT_SECONDS,
        filter_timeout_seconds: Optional[float] = DEFAULT_FILTER_TIMEOUT_SECONDS,
    ):
        """Initialize hook manager.

        Args:
            action_timeout_seconds: Default timeout per action listener (None = no timeout)
            filter_timeout_seconds: Default timeout per filter listener (None = no timeout)
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

        # Timeout configuration
        self._action_timeout = action_timeout_seconds
        self._filter_timeout = filter_timeout_seconds

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

        logger.debug(
            "add_action hook=%s priority=%d callback_id=%s", hook_name, priority, callback_id
        )
        return callback_id

    async def do_action(self, hook_name: str, *args, **kwargs) -> None:
        """Fire an action hook.

        All registered callbacks are awaited in priority order (lower = first).
        Callbacks can unhook themselves or others during execution (removal is deferred).

        If a callback times out or raises an exception, it is logged and execution
        continues to the next callback. The hook chain is never broken.
        """
        if hook_name not in self._action_hooks:
            logger.debug("do_action hook=%s no callbacks registered", hook_name)
            return

        self._action_call_count[hook_name] += 1
        self._action_nesting[hook_name] += 1

        scope = _current_scope.get(None)
        if scope is not None and hasattr(scope, "record_action"):
            try:
                scope.record_action(hook_name)
            except Exception:  # pragma: no cover - defensive
                logger.debug("do_action scope.record_action failed hook=%s", hook_name, exc_info=True)

        try:
            for priority in sorted(self._action_hooks[hook_name].keys()):
                callbacks = self._action_hooks[hook_name][priority]
                for callback_id, callback in list(callbacks):
                    if callback_id in self._removed_actions[hook_name]:
                        continue

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

    async def apply_filters(self, hook_name: str, value: Any, *args, **kwargs) -> Any:
        """Apply a filter chain to a value."""
        if hook_name not in self._filter_hooks:
            logger.debug("apply_filters hook=%s no callbacks registered", hook_name)
            return value

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

    async def _run_action_listener(
        self,
        callback_id: str,
        hook_name: str,
        callback: Callable[..., Coroutine[Any, Any, Any] | Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        timeout: Optional[float],
    ) -> None:
        """Execute one action listener with optional timeout."""
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

        removed_bucket.pop(hook_name, None)
