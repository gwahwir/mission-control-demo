"""Tests for the /agents endpoints."""

from __future__ import annotations

from tests.conftest import FAKE_AGENT_ID


def test_list_agents_returns_registered_agent(client):
    resp = client.get("/agents")
    assert resp.status_code == 200
    agents = resp.json()
    assert len(agents) == 1
    assert agents[0]["id"] == FAKE_AGENT_ID
    assert agents[0]["status"] == "online"


def test_get_agent_found(client):
    resp = client.get(f"/agents/{FAKE_AGENT_ID}")
    assert resp.status_code == 200
    assert resp.json()["id"] == FAKE_AGENT_ID


def test_get_agent_not_found(client):
    resp = client.get("/agents/nonexistent")
    assert resp.status_code == 404


def test_agent_has_skills(client):
    resp = client.get(f"/agents/{FAKE_AGENT_ID}")
    data = resp.json()
    assert len(data["skills"]) == 1
    assert data["skills"][0]["id"] == "echo"
