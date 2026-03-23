"""Integration tests for the full task lifecycle.

All A2A HTTP calls are intercepted with pytest-httpx so no real agent
process is required. Dispatch is asynchronous (202) — tests use
wait_for_task() to poll until a terminal state is reached.
"""

from __future__ import annotations

import asyncio

import httpx
from pytest_httpx import HTTPXMock

from tests.conftest import (
    FAKE_AGENT_ID,
    FAKE_AGENT_URL,
    a2a_cancel_response,
    a2a_rpc_callback,
    wait_for_task,
)


# ---------------------------------------------------------------------------
# Dispatch — 202 Accepted
# ---------------------------------------------------------------------------

async def test_dispatch_returns_202_immediately(client, httpx_mock: HTTPXMock):
    httpx_mock.add_callback(
        a2a_rpc_callback("hello world"),
        url=f"{FAKE_AGENT_URL}/",
    )
    resp = await client.post(f"/agents/{FAKE_AGENT_ID}/tasks", json={"text": "hello world"})
    assert resp.status_code == 202
    data = resp.json()
    assert "task_id" in data
    assert data["state"] == "submitted"
    assert data["agent_id"] == FAKE_AGENT_ID
    assert data["instance_url"] == FAKE_AGENT_URL


async def test_dispatch_task_completes(client, httpx_mock: HTTPXMock):
    httpx_mock.add_callback(a2a_rpc_callback("hello world"), url=f"{FAKE_AGENT_URL}/")

    resp = await client.post(f"/agents/{FAKE_AGENT_ID}/tasks", json={"text": "hello world"})
    task_id = resp.json()["task_id"]

    result = await wait_for_task(client, FAKE_AGENT_ID, task_id)
    assert result["state"] == "completed"
    assert "HELLO WORLD" in result["output_text"]


async def test_dispatch_task_agent_not_found(client):
    resp = await client.post("/agents/ghost-agent/tasks", json={"text": "ping"})
    assert resp.status_code == 404


async def test_dispatch_task_no_online_instances(client, registry):
    from control_plane.registry import AgentStatus
    registry._types[FAKE_AGENT_ID].instances[0].status = AgentStatus.OFFLINE

    resp = await client.post(f"/agents/{FAKE_AGENT_ID}/tasks", json={"text": "ping"})
    assert resp.status_code == 503

    # Restore
    registry._types[FAKE_AGENT_ID].instances[0].status = AgentStatus.ONLINE


async def test_dispatch_task_agent_unreachable_marks_failed(client, httpx_mock: HTTPXMock):
    httpx_mock.add_exception(
        httpx.ConnectError("connection refused"),
        url=f"{FAKE_AGENT_URL}/",
    )
    resp = await client.post(f"/agents/{FAKE_AGENT_ID}/tasks", json={"text": "ping"})
    assert resp.status_code == 202
    task_id = resp.json()["task_id"]

    result = await wait_for_task(client, FAKE_AGENT_ID, task_id)
    assert result["state"] == "failed"


# ---------------------------------------------------------------------------
# Active-task counter on instance
# ---------------------------------------------------------------------------

async def test_active_tasks_decrements_after_completion(client, registry, httpx_mock: HTTPXMock):
    httpx_mock.add_callback(a2a_rpc_callback("counter test"), url=f"{FAKE_AGENT_URL}/")

    resp = await client.post(f"/agents/{FAKE_AGENT_ID}/tasks", json={"text": "counter test"})
    task_id = resp.json()["task_id"]
    await wait_for_task(client, FAKE_AGENT_ID, task_id)

    instance = registry._types[FAKE_AGENT_ID].instances[0]
    assert instance.active_tasks == 0


# ---------------------------------------------------------------------------
# Load balancing — round-robin across two instances
# ---------------------------------------------------------------------------

async def test_least_connections_dispatch(registry, task_store, broker, httpx_mock: HTTPXMock):
    """Two instances: the one with fewer active tasks should be picked."""
    from control_plane.registry import AgentInstance, AgentStatus

    inst_a = AgentInstance(url="http://echo-a:8001", status=AgentStatus.ONLINE, active_tasks=3)
    inst_b = AgentInstance(url="http://echo-b:8001", status=AgentStatus.ONLINE, active_tasks=0)
    registry._types[FAKE_AGENT_ID].instances = [inst_a, inst_b]

    picked = registry.pick_instance(FAKE_AGENT_ID)
    assert picked is inst_b


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

async def test_get_task_after_dispatch(client, httpx_mock: HTTPXMock):
    httpx_mock.add_callback(a2a_rpc_callback("fetch me"), url=f"{FAKE_AGENT_URL}/")
    resp = await client.post(f"/agents/{FAKE_AGENT_ID}/tasks", json={"text": "fetch me"})
    task_id = resp.json()["task_id"]
    await wait_for_task(client, FAKE_AGENT_ID, task_id)

    resp = await client.get(f"/agents/{FAKE_AGENT_ID}/tasks/{task_id}")
    assert resp.status_code == 200
    assert resp.json()["task_id"] == task_id


async def test_get_task_not_found(client):
    resp = await client.get(f"/agents/{FAKE_AGENT_ID}/tasks/no-such-task")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Cancel — routed to the owning instance
# ---------------------------------------------------------------------------

async def test_cancel_task(client, httpx_mock: HTTPXMock):
    # Dispatch — agent returns "working" so the record stays in a non-terminal state
    httpx_mock.add_callback(a2a_rpc_callback("to cancel", state="working"), url=f"{FAKE_AGENT_URL}/")
    resp = await client.post(f"/agents/{FAKE_AGENT_ID}/tasks", json={"text": "to cancel"})
    task_id = resp.json()["task_id"]

    # Wait until _run_task has stored the "working" state (not just "submitted")
    deadline = asyncio.get_event_loop().time() + 5.0
    while asyncio.get_event_loop().time() < deadline:
        r = await client.get(f"/agents/{FAKE_AGENT_ID}/tasks/{task_id}")
        if r.status_code == 200 and r.json()["state"] == "working":
            break
        await asyncio.sleep(0.05)

    # Cancel (second POST to the agent URL)
    def cancel_callback(request: httpx.Request) -> httpx.Response:
        return a2a_cancel_response(task_id)
    httpx_mock.add_callback(cancel_callback, url=f"{FAKE_AGENT_URL}/")

    resp = await client.delete(f"/agents/{FAKE_AGENT_ID}/tasks/{task_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"

    resp = await client.get(f"/agents/{FAKE_AGENT_ID}/tasks/{task_id}")
    assert resp.json()["state"] == "canceled"


async def test_cancel_routes_to_instance_url(client, task_store, httpx_mock: HTTPXMock):
    """Cancel must hit the instance_url stored on the task, not just any instance."""
    from control_plane.task_store import TaskRecord, TaskState

    task_id = "sticky-task-001"
    record = TaskRecord(
        task_id=task_id,
        agent_id=FAKE_AGENT_ID,
        instance_url=FAKE_AGENT_URL,
        state=TaskState.WORKING,
        input_text="sticky",
    )
    await task_store.save(record)

    def cancel_callback(request: httpx.Request) -> httpx.Response:
        return a2a_cancel_response(task_id)
    httpx_mock.add_callback(cancel_callback, url=f"{FAKE_AGENT_URL}/")

    resp = await client.delete(f"/agents/{FAKE_AGENT_ID}/tasks/{task_id}")
    assert resp.status_code == 200


async def test_cancel_task_not_found(client):
    resp = await client.delete(f"/agents/{FAKE_AGENT_ID}/tasks/ghost-task")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Global history
# ---------------------------------------------------------------------------

async def test_list_all_tasks(client, httpx_mock: HTTPXMock):
    httpx_mock.add_callback(a2a_rpc_callback("first"), url=f"{FAKE_AGENT_URL}/")
    httpx_mock.add_callback(a2a_rpc_callback("second"), url=f"{FAKE_AGENT_URL}/")

    r1 = await client.post(f"/agents/{FAKE_AGENT_ID}/tasks", json={"text": "first"})
    r2 = await client.post(f"/agents/{FAKE_AGENT_ID}/tasks", json={"text": "second"})

    await wait_for_task(client, FAKE_AGENT_ID, r1.json()["task_id"])
    await wait_for_task(client, FAKE_AGENT_ID, r2.json()["task_id"])

    resp = await client.get("/tasks")
    assert resp.status_code == 200
    ids = {t["task_id"] for t in resp.json()}
    assert r1.json()["task_id"] in ids
    assert r2.json()["task_id"] in ids


# ---------------------------------------------------------------------------
# Metrics endpoint
# ---------------------------------------------------------------------------

async def test_metrics_endpoint(client):
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert b"mc_tasks_dispatched_total" in resp.content
