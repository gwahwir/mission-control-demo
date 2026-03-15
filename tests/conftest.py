"""Shared pytest fixtures for Mission Control integration tests.

Uses FastAPI's TestClient (sync) and httpx AsyncClient so tests run without
spinning up a real server process.  The A2A agent calls are mocked via
pytest-httpx so tests are fully hermetic.
"""

from __future__ import annotations

import json
import pytest
from fastapi.testclient import TestClient

from control_plane.registry import AgentRegistry, AgentStatus, RegisteredAgent
from control_plane.routes import init_routes, router, _ws_subscribers
from control_plane.server import create_app
from control_plane.task_store import TaskStore


# ---------------------------------------------------------------------------
# Minimal echo-agent A2A response factory
# ---------------------------------------------------------------------------

def make_a2a_response(task_id: str, text: str, state: str = "completed") -> dict:
    return {
        "id": task_id,
        "status": {
            "state": state,
            "message": {
                "parts": [{"text": f"ECHO: {text.upper()}"}]
            },
        },
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FAKE_AGENT_ID = "echo-agent"
FAKE_AGENT_URL = "http://echo-agent:8001"


@pytest.fixture()
def task_store() -> TaskStore:
    return TaskStore()


@pytest.fixture()
def registry(task_store) -> AgentRegistry:
    reg = AgentRegistry.__new__(AgentRegistry)
    reg._agents = {}
    reg._poll_interval = 30
    reg._poll_task = None

    import httpx
    reg._client = httpx.AsyncClient()

    # Pre-register a fake online agent
    agent = RegisteredAgent(
        id=FAKE_AGENT_ID,
        url=FAKE_AGENT_URL,
        status=AgentStatus.ONLINE,
        card={
            "name": "Echo Agent",
            "description": "Test echo agent",
            "skills": [{"id": "echo", "name": "Echo"}],
            "capabilities": {"streaming": True},
        },
    )
    reg._agents[FAKE_AGENT_ID] = agent
    return reg


@pytest.fixture()
def app(registry, task_store):
    """Return a fully wired FastAPI test app."""
    # Clear any stale WS subscriber state between tests
    _ws_subscribers.clear()
    init_routes(registry, task_store)

    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from control_plane.metrics import instrument_app

    test_app = FastAPI()
    test_app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
    )
    test_app.include_router(router)
    instrument_app(test_app)
    return test_app


@pytest.fixture()
def client(app) -> TestClient:
    return TestClient(app)
