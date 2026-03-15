"""Integration tests for the full task lifecycle.

All A2A HTTP calls are intercepted with pytest-httpx so no real agent
process is required.
"""

from __future__ import annotations

import json
import pytest
import httpx
from pytest_httpx import HTTPXMock

from tests.conftest import FAKE_AGENT_ID, FAKE_AGENT_URL, make_a2a_response

TASK_ID = "task-abc-123"


def _a2a_rpc_response(task_id: str, text: str, state: str = "completed") -> bytes:
    """Encode a JSON-RPC 2.0 response wrapping an A2A task result."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": make_a2a_response(task_id, text, state),
    }
    return json.dumps(payload).encode()


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def test_dispatch_task_success(client, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{FAKE_AGENT_URL}/",
        method="POST",
        content=_a2a_rpc_response(TASK_ID, "hello world"),
        headers={"Content-Type": "application/json"},
    )

    resp = client.post(
        f"/agents/{FAKE_AGENT_ID}/tasks",
        json={"text": "hello world"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["task_id"] == TASK_ID
    assert data["state"] == "completed"
    assert "HELLO WORLD" in data["output_text"]


def test_dispatch_task_agent_not_found(client):
    resp = client.post("/agents/ghost-agent/tasks", json={"text": "ping"})
    assert resp.status_code == 404


def test_dispatch_task_agent_offline(client, registry):
    from control_plane.registry import AgentStatus
    registry._agents[FAKE_AGENT_ID].status = AgentStatus.OFFLINE
    resp = client.post(f"/agents/{FAKE_AGENT_ID}/tasks", json={"text": "ping"})
    assert resp.status_code == 503
    # Restore for other tests
    registry._agents[FAKE_AGENT_ID].status = AgentStatus.ONLINE


def test_dispatch_task_agent_unreachable(client, httpx_mock: HTTPXMock):
    httpx_mock.add_exception(
        httpx.ConnectError("connection refused"),
        url=f"{FAKE_AGENT_URL}/",
        method="POST",
    )
    resp = client.post(f"/agents/{FAKE_AGENT_ID}/tasks", json={"text": "ping"})
    assert resp.status_code == 502


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def test_get_task_after_dispatch(client, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{FAKE_AGENT_URL}/",
        method="POST",
        content=_a2a_rpc_response(TASK_ID, "fetch me"),
        headers={"Content-Type": "application/json"},
    )
    client.post(f"/agents/{FAKE_AGENT_ID}/tasks", json={"text": "fetch me"})

    resp = client.get(f"/agents/{FAKE_AGENT_ID}/tasks/{TASK_ID}")
    assert resp.status_code == 200
    assert resp.json()["task_id"] == TASK_ID


def test_get_task_not_found(client):
    resp = client.get(f"/agents/{FAKE_AGENT_ID}/tasks/no-such-task")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------

def test_cancel_task(client, httpx_mock: HTTPXMock):
    # First dispatch to create the record
    httpx_mock.add_response(
        url=f"{FAKE_AGENT_URL}/",
        method="POST",
        content=_a2a_rpc_response(TASK_ID, "to cancel", state="working"),
        headers={"Content-Type": "application/json"},
    )
    client.post(f"/agents/{FAKE_AGENT_ID}/tasks", json={"text": "to cancel"})

    # Mock the cancel RPC call
    cancel_payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"id": TASK_ID, "status": {"state": "canceled"}},
    }).encode()
    httpx_mock.add_response(
        url=f"{FAKE_AGENT_URL}/",
        method="POST",
        content=cancel_payload,
        headers={"Content-Type": "application/json"},
    )

    resp = client.delete(f"/agents/{FAKE_AGENT_ID}/tasks/{TASK_ID}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"

    # Verify state updated in store
    get_resp = client.get(f"/agents/{FAKE_AGENT_ID}/tasks/{TASK_ID}")
    assert get_resp.json()["state"] == "canceled"


def test_cancel_task_not_found(client):
    resp = client.delete(f"/agents/{FAKE_AGENT_ID}/tasks/ghost-task")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Global history
# ---------------------------------------------------------------------------

def test_list_all_tasks(client, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{FAKE_AGENT_URL}/",
        method="POST",
        content=_a2a_rpc_response("t1", "first"),
        headers={"Content-Type": "application/json"},
    )
    httpx_mock.add_response(
        url=f"{FAKE_AGENT_URL}/",
        method="POST",
        content=_a2a_rpc_response("t2", "second"),
        headers={"Content-Type": "application/json"},
    )
    client.post(f"/agents/{FAKE_AGENT_ID}/tasks", json={"text": "first"})
    client.post(f"/agents/{FAKE_AGENT_ID}/tasks", json={"text": "second"})

    resp = client.get("/tasks")
    assert resp.status_code == 200
    ids = {t["task_id"] for t in resp.json()}
    assert {"t1", "t2"}.issubset(ids)


# ---------------------------------------------------------------------------
# Metrics endpoint
# ---------------------------------------------------------------------------

def test_metrics_endpoint(client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert b"mc_tasks_dispatched_total" in resp.content
