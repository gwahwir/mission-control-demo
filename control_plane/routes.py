"""REST API routes for the Control Plane."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

import httpx

from control_plane.a2a_client import A2AClient, A2AError
from control_plane.log import get_logger
from control_plane.metrics import (
    task_duration,
    tasks_cancelled,
    tasks_completed,
    tasks_dispatched,
    tasks_failed,
)
from control_plane.pubsub import InMemoryBroker, RedisBroker
from control_plane.registry import AgentInstance, AgentRegistry
from control_plane.task_store import PostgresTaskStore, TaskRecord, TaskState, TaskStore

logger = get_logger(__name__)

router = APIRouter()

# Injected by the app factory via init_routes()
_registry: AgentRegistry | None = None
_task_store: TaskStore | PostgresTaskStore | None = None
_broker: InMemoryBroker | RedisBroker | None = None


def init_routes(
    registry: AgentRegistry,
    task_store: TaskStore | PostgresTaskStore,
    broker: InMemoryBroker | RedisBroker,
) -> None:
    global _registry, _task_store, _broker
    _registry = registry
    _task_store = task_store
    _broker = broker


# ------------------------------------------------------------------
# Request / Response models
# ------------------------------------------------------------------

class TaskRequest(BaseModel):
    text: str
    baselines: str = ""  # Optional: current baseline assessments
    key_questions: str = ""  # Optional: specific questions to address


class RegisterRequest(BaseModel):
    type_name: str
    agent_url: str


# ------------------------------------------------------------------
# Agent endpoints
# ------------------------------------------------------------------

@router.get("/agents")
async def list_agents() -> list[dict[str, Any]]:
    assert _registry is not None
    return [t.to_dict() for t in _registry.agents.values()]


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str) -> dict[str, Any]:
    assert _registry is not None
    agent_type = _registry.get(agent_id)
    if not agent_type:
        raise HTTPException(404, f"Agent '{agent_id}' not found")
    return agent_type.to_dict()


@router.post("/register")
async def register_agent(req: RegisterRequest) -> dict[str, Any]:
    """Allow agents to self-register with the control plane."""
    assert _registry is not None
    instance = await _registry.register_instance(req.type_name, req.agent_url)
    logger.info("agent_registered", type_name=req.type_name, url=req.agent_url, status=instance.status.value)
    return {
        "status": "registered",
        "type_name": req.type_name,
        "agent_url": req.agent_url,
        "agent_status": instance.status.value,
    }


@router.post("/deregister")
async def deregister_agent(req: RegisterRequest) -> dict[str, Any]:
    """Allow agents to deregister on shutdown."""
    assert _registry is not None
    removed = await _registry.remove_instance(req.type_name, req.agent_url)
    if removed:
        logger.info("agent_deregistered", type_name=req.type_name, url=req.agent_url)
    return {
        "status": "deregistered" if removed else "not_found",
        "type_name": req.type_name,
        "agent_url": req.agent_url,
    }


# ------------------------------------------------------------------
# Task endpoints
# ------------------------------------------------------------------

@router.post("/agents/{agent_id}/tasks", status_code=202)
async def dispatch_task(agent_id: str, req: TaskRequest) -> dict[str, Any]:
    """Accept a task immediately (202) and run it asynchronously in the background."""
    assert _registry is not None and _task_store is not None

    agent_type = _registry.get(agent_id)
    if not agent_type:
        raise HTTPException(404, f"Agent '{agent_id}' not found")

    instance = agent_type.pick()
    if not instance:
        raise HTTPException(503, f"No online instances available for agent '{agent_id}'")

    task_id = str(uuid.uuid4())
    record = TaskRecord(
        task_id=task_id,
        agent_id=agent_id,
        instance_url=instance.url,
        state=TaskState.SUBMITTED,
        input_text=req.text,
        baselines=req.baselines,
        key_questions=req.key_questions,
    )
    await _task_store.save(record)

    tasks_dispatched.labels(agent_id=agent_id).inc()
    instance.active_tasks += 1

    logger.info("task_accepted", agent_id=agent_id, task_id=task_id, instance=instance.url)
    asyncio.create_task(_run_task(task_id, agent_id, instance, req.text))

    return record.to_dict()


async def _run_task(
    task_id: str,
    agent_id: str,
    instance: AgentInstance,
    text: str,
) -> None:
    """Background coroutine: call the agent, then update task state."""
    assert _task_store is not None and _broker is not None

    record = await _task_store.get(task_id)
    record.state = TaskState.WORKING
    await _task_store.save(record)
    await _broker.publish(task_id, record.to_dict())

    started_at = time.time()
    client = A2AClient(instance.url, timeout=300)
    try:
        result = await client.send_message(
            text,
            baselines=record.baselines,
            key_questions=record.key_questions,
        )

        status = result.get("status", {})
        state_str = status.get("state", "failed")
        output = ""
        msg = status.get("message", {})
        if msg:
            parts = msg.get("parts", [])
            if parts:
                output = parts[0].get("text", "")

        record.state = TaskState(state_str)
        record.output_text = output
        record.a2a_task = result

        # Extract error detail from the agent's response when task failed
        if record.state == TaskState.FAILED:
            record.error = output or "Agent returned failed state with no details"

        elapsed = time.time() - started_at
        task_duration.labels(agent_id=agent_id).observe(elapsed)

        if record.state == TaskState.COMPLETED:
            tasks_completed.labels(agent_id=agent_id).inc()
        elif record.state == TaskState.FAILED:
            tasks_failed.labels(agent_id=agent_id).inc()

        logger.info(
            "task_complete",
            agent_id=agent_id,
            task_id=task_id,
            state=state_str,
            duration_s=round(elapsed, 3),
            instance=instance.url,
        )

    except A2AError as exc:
        tasks_failed.labels(agent_id=agent_id).inc()
        logger.error("task_a2a_error", task_id=task_id, error=str(exc))
        record.state = TaskState.FAILED
        record.error = f"A2A protocol error: {exc}"

    except httpx.HTTPStatusError as exc:
        tasks_failed.labels(agent_id=agent_id).inc()
        logger.error("task_http_error", task_id=task_id, status=exc.response.status_code, error=str(exc))
        record.state = TaskState.FAILED
        record.error = f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"

    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        tasks_failed.labels(agent_id=agent_id).inc()
        logger.error("task_connection_error", task_id=task_id, error=str(exc))
        record.state = TaskState.FAILED
        record.error = f"Connection failed: {type(exc).__name__} — {exc}"

    except Exception as exc:
        tasks_failed.labels(agent_id=agent_id).inc()
        logger.error("task_error", task_id=task_id, error=str(exc))
        record.state = TaskState.FAILED
        record.error = f"{type(exc).__name__}: {exc}"

    finally:
        await client.close()
        instance.active_tasks = max(0, instance.active_tasks - 1)

    await _task_store.save(record)
    await _broker.publish(task_id, record.to_dict())


@router.get("/agents/{agent_id}/tasks/{task_id}")
async def get_task(agent_id: str, task_id: str) -> dict[str, Any]:
    assert _task_store is not None
    record = await _task_store.get(task_id)
    if not record or record.agent_id != agent_id:
        raise HTTPException(404, "Task not found")
    return record.to_dict()


@router.delete("/agents/{agent_id}/tasks/{task_id}")
async def cancel_task_endpoint(agent_id: str, task_id: str) -> dict[str, Any]:
    """Cancel a task, routing the cancel to the specific instance that owns it."""
    assert _registry is not None and _task_store is not None

    if not _registry.get(agent_id):
        raise HTTPException(404, f"Agent '{agent_id}' not found")

    record = await _task_store.get(task_id)
    if not record or record.agent_id != agent_id:
        raise HTTPException(404, "Task not found")

    logger.info("task_cancel", agent_id=agent_id, task_id=task_id, instance=record.instance_url)

    client = A2AClient(record.instance_url,timeout=300)
    try:
        await client.cancel_task(task_id)
    except A2AError as e:
        logger.warning("task_cancel_a2a_error", task_id=task_id, error=str(e))
        raise HTTPException(502, str(e))
    finally:
        await client.close()

    record.state = TaskState.CANCELED
    await _task_store.save(record)
    tasks_cancelled.labels(agent_id=agent_id).inc()
    await _broker.publish(task_id, record.to_dict())

    logger.info("task_cancelled", agent_id=agent_id, task_id=task_id)
    return {"status": "cancelled", "task_id": task_id}


@router.get("/tasks")
async def list_all_tasks() -> list[dict[str, Any]]:
    assert _task_store is not None
    return [t.to_dict() for t in await _task_store.list_all()]


@router.delete("/tasks")
async def delete_all_tasks() -> dict[str, Any]:
    """Delete all task history."""
    assert _task_store is not None
    count = await _task_store.delete_all()
    logger.info("tasks_cleared", count=count)
    return {"status": "cleared", "deleted": count}


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str) -> dict[str, Any]:
    """Delete a single task from history."""
    assert _task_store is not None
    deleted = await _task_store.delete(task_id)
    if not deleted:
        raise HTTPException(404, "Task not found")
    logger.info("task_deleted", task_id=task_id)
    return {"status": "deleted", "task_id": task_id}


# ------------------------------------------------------------------
# Graph topology — aggregated from all agents
# ------------------------------------------------------------------

@router.get("/graph")
async def get_graph() -> dict[str, Any]:
    """Fetch graph topology from all agents and resolve cross-agent edges."""
    assert _registry is not None

    agents_data: list[dict[str, Any]] = []
    cross_agent_edges: list[dict[str, str]] = []
    pending_downstream: list[tuple[str, dict]] = []  # (source_type_id, downstream_info)

    # Build lookup tables for resolving downstream references
    # Map exact URL and also port number to agent type ID
    url_to_type: dict[str, str] = {}
    port_to_type: dict[str, str] = {}
    for type_id, agent_type in _registry.agents.items():
        for inst in agent_type.instances:
            url_to_type[inst.url.rstrip("/")] = type_id
            # Extract port from URL for fuzzy matching
            try:
                from urllib.parse import urlparse
                port = urlparse(inst.url).port
                if port:
                    port_to_type[str(port)] = type_id
            except Exception:
                pass

    async with httpx.AsyncClient(timeout=5) as client:
        for type_id, agent_type in _registry.agents.items():
            instance = agent_type.pick()
            if not instance:
                continue
            try:
                r = await client.get(f"{instance.url}/graph")
                r.raise_for_status()
                topology = r.json()
            except Exception:
                logger.warning("graph_fetch_failed", type_id=type_id, url=instance.url)
                continue

            agents_data.append({
                "id": type_id,
                "name": agent_type.name,
                "status": agent_type.status,
                "nodes": topology.get("nodes", []),
                "edges": topology.get("edges", []),
                "entry_node": topology.get("entry_node"),
                "input_fields": topology.get("input_fields", []),
            })

            downstream = topology.get("downstream")
            if downstream:
                pending_downstream.append((type_id, downstream))

    # Resolve all cross-agent edges after all agents are loaded
    for source_type_id, downstream in pending_downstream:
        target_url = downstream["agent_url"].rstrip("/")

        # Try exact URL match first
        target_type = url_to_type.get(target_url)

        # Fall back to port-based matching
        if not target_type:
            try:
                from urllib.parse import urlparse
                port = urlparse(target_url).port
                if port:
                    target_type = port_to_type.get(str(port))
            except Exception:
                pass

        if target_type:
            target_entry = None
            for ad in agents_data:
                if ad["id"] == target_type:
                    target_entry = ad.get("entry_node")
                    break
            cross_agent_edges.append({
                "source_agent": source_type_id,
                "source_node": downstream["from_node"],
                "target_agent": target_type,
                "target_node": target_entry or "unknown",
            })
        else:
            logger.warning(
                "downstream_unresolved",
                source=source_type_id,
                target_url=target_url,
            )

    return {"agents": agents_data, "cross_agent_edges": cross_agent_edges}


# ------------------------------------------------------------------
# WebSocket — live task updates via pub/sub broker
# ------------------------------------------------------------------

@router.websocket("/ws/tasks/{task_id}")
async def ws_task_updates(websocket: WebSocket, task_id: str) -> None:
    assert _task_store is not None and _broker is not None
    await websocket.accept()

    record = await _task_store.get(task_id)
    if record:
        await websocket.send_json(record.to_dict())

    queue: asyncio.Queue = asyncio.Queue()
    _broker.subscribe(task_id, queue)
    logger.debug("ws_connected", task_id=task_id)

    try:
        while True:
            try:
                data = await asyncio.wait_for(queue.get(), timeout=30.0)
                await websocket.send_json(data)
            except asyncio.TimeoutError:
                continue
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        _broker.unsubscribe(task_id, queue)
        logger.debug("ws_disconnected", task_id=task_id)
