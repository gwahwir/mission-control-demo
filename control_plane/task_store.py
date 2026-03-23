"""Task store for the Control Plane.

Defines the shared data model (TaskState, TaskRecord) and two
implementations:

* ``TaskStore``         — in-memory (default, no dependencies)
* ``PostgresTaskStore`` — asyncpg-backed (enabled via DATABASE_URL)

Both share the same async interface so routes.py is backend-agnostic.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Shared data model
# ---------------------------------------------------------------------------

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
    instance_url: str = ""          # exact agent instance that is running this task
    state: TaskState = TaskState.SUBMITTED
    input_text: str = ""
    baselines: str = ""             # optional: current baseline assessments
    key_questions: str = ""         # optional: specific questions to address
    output_text: str = ""
    error: str = ""                 # error message when state is failed
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    a2a_task: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "instance_url": self.instance_url,
            "state": self.state.value,
            "input_text": self.input_text,
            "baselines": self.baselines,
            "key_questions": self.key_questions,
            "output_text": self.output_text,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> TaskRecord:
        a2a_task = row.get("a2a_task", "{}")
        if isinstance(a2a_task, str):
            a2a_task = json.loads(a2a_task)
        return cls(
            task_id=row["task_id"],
            agent_id=row["agent_id"],
            instance_url=row.get("instance_url", ""),
            state=TaskState(row["state"]),
            input_text=row.get("input_text", ""),
            baselines=row.get("baselines", ""),
            key_questions=row.get("key_questions", ""),
            output_text=row.get("output_text", ""),
            error=row.get("error", ""),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            a2a_task=a2a_task,
        )


# ---------------------------------------------------------------------------
# In-memory implementation (default)
# ---------------------------------------------------------------------------

class TaskStore:
    """In-memory task store. No external dependencies.

    Used when DATABASE_URL is not set. All state is lost on restart.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, TaskRecord] = {}

    async def save(self, record: TaskRecord) -> None:
        record.updated_at = time.time()
        self._tasks[record.task_id] = record

    async def get(self, task_id: str) -> TaskRecord | None:
        return self._tasks.get(task_id)

    async def list_all(self) -> list[TaskRecord]:
        return sorted(self._tasks.values(), key=lambda t: t.created_at, reverse=True)

    async def list_by_agent(self, agent_id: str) -> list[TaskRecord]:
        return [t for t in await self.list_all() if t.agent_id == agent_id]

    async def delete(self, task_id: str) -> bool:
        if task_id in self._tasks:
            del self._tasks[task_id]
            return True
        return False

    async def delete_all(self) -> int:
        count = len(self._tasks)
        self._tasks.clear()
        return count


# ---------------------------------------------------------------------------
# PostgreSQL implementation
# ---------------------------------------------------------------------------

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id      TEXT PRIMARY KEY,
    agent_id     TEXT   NOT NULL,
    instance_url TEXT   NOT NULL DEFAULT '',
    state        TEXT   NOT NULL,
    input_text   TEXT   NOT NULL DEFAULT '',
    baselines    TEXT   NOT NULL DEFAULT '',
    key_questions TEXT  NOT NULL DEFAULT '',
    output_text  TEXT   NOT NULL DEFAULT '',
    error        TEXT   NOT NULL DEFAULT '',
    created_at   FLOAT8 NOT NULL,
    updated_at   FLOAT8 NOT NULL,
    a2a_task     TEXT   NOT NULL DEFAULT '{}'
);
"""

_ADD_ERROR_COLUMN = """
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS error TEXT NOT NULL DEFAULT '';
"""

_ADD_STRUCTURED_INPUT_COLUMNS = """
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS baselines TEXT NOT NULL DEFAULT '';
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS key_questions TEXT NOT NULL DEFAULT '';
"""

_UPSERT = """
INSERT INTO tasks
    (task_id, agent_id, instance_url, state, input_text, baselines, key_questions, output_text, error, created_at, updated_at, a2a_task)
VALUES
    ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
ON CONFLICT (task_id) DO UPDATE SET
    state        = EXCLUDED.state,
    output_text  = EXCLUDED.output_text,
    error        = EXCLUDED.error,
    updated_at   = EXCLUDED.updated_at,
    a2a_task     = EXCLUDED.a2a_task;
"""


class PostgresTaskStore:
    """asyncpg-backed task store.

    Activated when DATABASE_URL is set. Call ``await store.init(url)``
    during app startup and ``await store.close()`` during shutdown.
    """

    def __init__(self) -> None:
        self._pool = None

    async def init(self, database_url: str) -> None:
        import asyncpg
        self._pool = await asyncpg.create_pool(dsn=database_url, min_size=2, max_size=10)
        async with self._pool.acquire() as conn:
            await conn.execute(_CREATE_TABLE)
            await conn.execute(_ADD_ERROR_COLUMN)
            await conn.execute(_ADD_STRUCTURED_INPUT_COLUMNS)

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()

    async def save(self, record: TaskRecord) -> None:
        record.updated_at = time.time()
        async with self._pool.acquire() as conn:
            await conn.execute(
                _UPSERT,
                record.task_id,
                record.agent_id,
                record.instance_url,
                record.state.value,
                record.input_text,
                record.baselines,
                record.key_questions,
                record.output_text,
                record.error,
                record.created_at,
                record.updated_at,
                json.dumps(record.a2a_task),
            )

    async def get(self, task_id: str) -> TaskRecord | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM tasks WHERE task_id = $1", task_id)
        return TaskRecord.from_row(dict(row)) if row else None

    async def list_all(self) -> list[TaskRecord]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM tasks ORDER BY created_at DESC")
        return [TaskRecord.from_row(dict(r)) for r in rows]

    async def list_by_agent(self, agent_id: str) -> list[TaskRecord]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM tasks WHERE agent_id = $1 ORDER BY created_at DESC", agent_id
            )
        return [TaskRecord.from_row(dict(r)) for r in rows]

    async def delete(self, task_id: str) -> bool:
        async with self._pool.acquire() as conn:
            result = await conn.execute("DELETE FROM tasks WHERE task_id = $1", task_id)
        return result == "DELETE 1"

    async def delete_all(self) -> int:
        async with self._pool.acquire() as conn:
            result = await conn.execute("DELETE FROM tasks")
        # result is e.g. "DELETE 42"
        return int(result.split()[-1])
