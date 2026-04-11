from __future__ import annotations

from abc import ABC, abstractmethod


class LifecycleHook(ABC):
    """
    Minimal lifecycle interface for node startup and shutdown hooks.
    """

    @abstractmethod
    async def startup(self) -> None:
        """Run node-specific startup logic."""

    @abstractmethod
    async def shutdown(self) -> None:
        """Run node-specific shutdown logic."""


class NoOpLifecycle(LifecycleHook):
    """
    Default lifecycle hook when a node has no custom startup or shutdown behavior.
    """

    async def startup(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None
