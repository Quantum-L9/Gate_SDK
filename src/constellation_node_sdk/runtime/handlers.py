from __future__ import annotations

from collections.abc import Callable
from typing import Any

_HANDLER_REGISTRY: dict[str, Callable[..., Any]] = {}


def register_handler(action: str, fn: Callable[..., Any] | None = None) -> Callable[..., Any]:
    """
    Register a runtime handler for a transport action.
    """
    normalized_action = action.strip().lower()
    if not normalized_action:
        raise ValueError("action must not be empty")

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        _HANDLER_REGISTRY[normalized_action] = func
        return func

    if fn is not None:
        return decorator(fn)
    return decorator


def get_handler(action: str) -> Callable[..., Any] | None:
    """
    Resolve a handler by exact action, falling back to '*' if present.
    """
    normalized_action = action.strip().lower()
    return _HANDLER_REGISTRY.get(normalized_action) or _HANDLER_REGISTRY.get("*")


def clear_handlers() -> None:
    """
    Clear all registered handlers.
    """
    _HANDLER_REGISTRY.clear()


def registered_actions() -> tuple[str, ...]:
    """
    Return the registered action names in sorted order.
    """
    return tuple(sorted(_HANDLER_REGISTRY.keys()))
