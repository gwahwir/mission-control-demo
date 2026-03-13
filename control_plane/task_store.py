"""In-memory task store for the Control Plane.

Tracks tasks dispatched through the control plane, their state, and history.
Designed to be swapped for a Redis or Postgres-backed store later.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskState(str, Enum):
    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    CANCELED = "canceled"
    FAILED = "failed"


@dataclass
class TaskRecord:
    """A task tracked by the control plane."""

    task_id: str
    agent_id: str
    state: TaskState = TaskState.SUBMITTED
    input_text: str = ""
    output_text: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    a2a_task: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "state": self.state.value,
            "input_text": self.input_text,
            "output_text": self.output_text,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class TaskStore:
    """In-memory task store. Swap for Redis/Postgres later."""

    def __init__(self) -> None:
        self._tasks: dict[str, TaskRecord] = {}

    def save(self, record: TaskRecord) -> None:
        record.updated_at = time.time()
        self._tasks[record.task_id] = record

    def get(self, task_id: str) -> TaskRecord | None:
        return self._tasks.get(task_id)

    def list_all(self) -> list[TaskRecord]:
        return sorted(
            self._tasks.values(), key=lambda t: t.created_at, reverse=True
        )

    def list_by_agent(self, agent_id: str) -> list[TaskRecord]:
        return [
            t
            for t in self.list_all()
            if t.agent_id == agent_id
        ]
