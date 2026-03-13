"""Cancellation mixin for LangGraph nodes.

Provides a shared cancellation signal that LangGraph nodes can check
at each step to enable clean mid-run stops via the A2A cancel protocol.
"""

from __future__ import annotations

import asyncio


class CancellableMixin:
    """Mixin that adds per-task cancellation tracking.

    Usage in a LangGraph node function::

        async def my_node(state, config):
            executor = config["configurable"]["executor"]
            executor.check_cancelled(task_id)
            # ... do work ...
    """

    def __init__(self) -> None:
        self._cancel_events: dict[str, asyncio.Event] = {}

    def register_task(self, task_id: str) -> None:
        """Register a new task for cancellation tracking."""
        self._cancel_events[task_id] = asyncio.Event()

    def request_cancel(self, task_id: str) -> None:
        """Signal that a task should be cancelled."""
        if event := self._cancel_events.get(task_id):
            event.set()

    def is_cancelled(self, task_id: str) -> bool:
        """Check whether a task has been cancelled."""
        if event := self._cancel_events.get(task_id):
            return event.is_set()
        return False

    def check_cancelled(self, task_id: str) -> None:
        """Raise ``asyncio.CancelledError`` if the task was cancelled.

        Call this at the top of every LangGraph node to ensure
        prompt cancellation.
        """
        if self.is_cancelled(task_id):
            raise asyncio.CancelledError(f"Task {task_id} was cancelled")

    def cleanup_task(self, task_id: str) -> None:
        """Remove cancellation tracking for a finished task."""
        self._cancel_events.pop(task_id, None)
