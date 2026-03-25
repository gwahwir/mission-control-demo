# tests/test_baseline_store.py
from __future__ import annotations
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, MagicMock, patch
import asyncpg


# ── Shared helpers ──────────────────────────────────────────────────────────

def make_pool(conn):
    """Wrap a mock connection in a pool whose acquire() is an async context manager."""
    pool = MagicMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=cm)
    return pool


@pytest.fixture
def app(monkeypatch):
    # Patch the pool singleton before importing server to prevent lifespan init
    mock_pool = MagicMock()
    monkeypatch.setattr("baseline_store.stores._pool", mock_pool)
    from baseline_store.server import create_app
    return create_app()


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


# ── POST /topics ─────────────────────────────────────────────────────────────

async def test_post_topics_happy_path(client):
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={
        "id": "aaaaaaaa-0000-0000-0000-000000000001",
        "topic_path": "climate_change",
        "display_name": "Climate Change",
        "created_at": "2026-03-25T08:00:00+00:00",
    })
    pool = make_pool(conn)

    with patch("baseline_store.routes.get_pgvector_pool", AsyncMock(return_value=pool)):
        resp = await client.post("/topics", json={
            "topic_path": "climate_change",
            "display_name": "Climate Change",
        })

    assert resp.status_code == 201
    body = resp.json()
    assert body["topic_path"] == "climate_change"
    assert body["display_name"] == "Climate Change"
    assert "id" in body
    assert "created_at" in body


async def test_post_topics_duplicate_returns_409(client):
    conn = MagicMock()
    conn.fetchrow = AsyncMock(
        side_effect=asyncpg.UniqueViolationError("duplicate key")
    )
    pool = make_pool(conn)

    with patch("baseline_store.routes.get_pgvector_pool", AsyncMock(return_value=pool)):
        resp = await client.post("/topics", json={
            "topic_path": "climate_change",
            "display_name": "Climate Change",
        })

    assert resp.status_code == 409
    assert "already registered" in resp.json()["detail"]


# ── GET /topics ───────────────────────────────────────────────────────────────

async def test_get_topics_returns_list(client):
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[
        {"id": "aaa", "topic_path": "climate_change", "display_name": "Climate Change", "created_at": "2026-03-25T00:00:00+00:00"},
        {"id": "bbb", "topic_path": "climate_change.energy", "display_name": "Energy", "created_at": "2026-03-25T00:00:00+00:00"},
    ])
    pool = make_pool(conn)

    with patch("baseline_store.routes.get_pgvector_pool", AsyncMock(return_value=pool)):
        resp = await client.get("/topics")

    assert resp.status_code == 200
    assert len(resp.json()["topics"]) == 2
    assert resp.json()["topics"][0]["topic_path"] == "climate_change"
