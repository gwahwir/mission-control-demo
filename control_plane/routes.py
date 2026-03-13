"""REST API routes for the Control Plane."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from control_plane.a2a_client import A2AClient, A2AError
from control_plane.registry import AgentRegistry, AgentStatus
from control_plane.task_store import TaskRecord, TaskState, TaskStore

logger = logging.getLogger(__name__)

router = APIRouter()

# These are injected by the app factory (see server.py)
_registry: AgentRegistry | None = None
_task_store: TaskStore | None = None
_ws_subscribers: dict[str, list[WebSocket]] = {}


def init_routes(registry: AgentRegistry, task_store: TaskStore) -> None:
    """Wire up shared state. Called once at startup."""
    global _registry, _task_store
    _registry = registry
    _task_store = task_store


# ------------------------------------------------------------------
# Request / Response models
# ------------------------------------------------------------------

class TaskRequest(BaseModel):
    text: str


class TaskResponse(BaseModel):
    task_id: str
    agent_id: str
    state: str
    input_text: str
    output_text: str


# ------------------------------------------------------------------
# Agent endpoints
# ------------------------------------------------------------------

@router.get("/agents")
async def list_agents() -> list[dict[str, Any]]:
    """List all registered agents and their status."""
    assert _registry is not None
    return [a.to_dict() for a in _registry.agents.values()]


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str) -> dict[str, Any]:
    """Get a single agent's details."""
    assert _registry is not None
    agent = _registry.get(agent_id)
    if not agent:
        raise HTTPException(404, f"Agent '{agent_id}' not found")
    return agent.to_dict()


# ------------------------------------------------------------------
# Task endpoints
# ------------------------------------------------------------------

@router.post("/agents/{agent_id}/tasks", response_model=TaskResponse)
async def dispatch_task(agent_id: str, req: TaskRequest) -> TaskResponse:
    """Dispatch a new task to an agent."""
    assert _registry is not None and _task_store is not None

    agent = _registry.get(agent_id)
    if not agent:
        raise HTTPException(404, f"Agent '{agent_id}' not found")
    if agent.status != AgentStatus.ONLINE:
        raise HTTPException(503, f"Agent '{agent_id}' is offline")

    client = A2AClient(agent.url)
    try:
        result = await client.send_message(req.text)
    except A2AError as e:
        raise HTTPException(502, str(e))
    except Exception as e:
        raise HTTPException(502, f"Failed to reach agent: {e}")
    finally:
        await client.close()

    # Extract task info from A2A response
    task_id = result.get("id", "")
    status = result.get("status", {})
    state_str = status.get("state", "failed")
    output = ""
    msg = status.get("message", {})
    if msg:
        parts = msg.get("parts", [])
        if parts:
            output = parts[0].get("text", "")

    record = TaskRecord(
        task_id=task_id,
        agent_id=agent_id,
        state=TaskState(state_str),
        input_text=req.text,
        output_text=output,
        a2a_task=result,
    )
    _task_store.save(record)

    # Notify WebSocket subscribers
    await _notify_ws(task_id, record.to_dict())

    return TaskResponse(
        task_id=task_id,
        agent_id=agent_id,
        state=state_str,
        input_text=req.text,
        output_text=output,
    )


@router.get("/agents/{agent_id}/tasks/{task_id}")
async def get_task(agent_id: str, task_id: str) -> dict[str, Any]:
    """Get a specific task's current state."""
    assert _task_store is not None
    record = _task_store.get(task_id)
    if not record or record.agent_id != agent_id:
        raise HTTPException(404, "Task not found")
    return record.to_dict()


@router.delete("/agents/{agent_id}/tasks/{task_id}")
async def cancel_task(agent_id: str, task_id: str) -> dict[str, Any]:
    """Cancel a running task."""
    assert _registry is not None and _task_store is not None

    agent = _registry.get(agent_id)
    if not agent:
        raise HTTPException(404, f"Agent '{agent_id}' not found")

    record = _task_store.get(task_id)
    if not record or record.agent_id != agent_id:
        raise HTTPException(404, "Task not found")

    client = A2AClient(agent.url)
    try:
        await client.cancel_task(task_id)
    except A2AError as e:
        raise HTTPException(502, str(e))
    finally:
        await client.close()

    record.state = TaskState.CANCELED
    _task_store.save(record)
    await _notify_ws(task_id, record.to_dict())

    return {"status": "cancelled", "task_id": task_id}


@router.get("/tasks")
async def list_all_tasks() -> list[dict[str, Any]]:
    """Global task history across all agents."""
    assert _task_store is not None
    return [t.to_dict() for t in _task_store.list_all()]


# ------------------------------------------------------------------
# WebSocket — live task updates
# ------------------------------------------------------------------

@router.websocket("/ws/tasks/{task_id}")
async def ws_task_updates(websocket: WebSocket, task_id: str) -> None:
    """Stream live task state updates over WebSocket."""
    await websocket.accept()

    if task_id not in _ws_subscribers:
        _ws_subscribers[task_id] = []
    _ws_subscribers[task_id].append(websocket)

    try:
        # Send current state immediately
        assert _task_store is not None
        record = _task_store.get(task_id)
        if record:
            await websocket.send_json(record.to_dict())

        # Keep alive until client disconnects
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _ws_subscribers.get(task_id, []).remove(websocket) if websocket in _ws_subscribers.get(task_id, []) else None


async def _notify_ws(task_id: str, data: dict[str, Any]) -> None:
    """Push an update to all WebSocket subscribers for a task."""
    subscribers = _ws_subscribers.get(task_id, [])
    closed: list[WebSocket] = []
    for ws in subscribers:
        try:
            await ws.send_json(data)
        except Exception:
            closed.append(ws)
    for ws in closed:
        subscribers.remove(ws)
