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


# ── POST /baselines/{topic_path}/versions ────────────────────────────────────

async def test_post_versions_happy_path(client):
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(side_effect=[
        {"topic_path": "us_iran_conflict"},          # topic exists
        {"max": None},                                # no existing versions → version 1
        {                                             # INSERT result
            "id": "cccccccc-0000-0000-0000-000000000001",
            "version_number": 1,
            "created_at": "2026-03-25T09:00:00+00:00",
        },
    ])
    pool = make_pool(conn)
    mock_embed = AsyncMock(return_value=[0.1] * 10)

    with patch("baseline_store.routes.get_pgvector_pool", AsyncMock(return_value=pool)), \
         patch("baseline_store.routes.get_embedder", return_value=mock_embed):
        resp = await client.post(
            "/baselines/us_iran_conflict/versions",
            json={
                "narrative": "Iran tensions elevated as of March 2026.",
                "citations": [{"article_id": "art1", "title": "Iran Update", "url": "https://example.com", "source": "Reuters", "published_at": "2026-03-25T00:00:00Z", "excerpt": "..."}],
            }
        )

    assert resp.status_code == 201
    body = resp.json()
    assert body["version_number"] == 1
    assert "id" in body
    assert "created_at" in body
    mock_embed.assert_called_once_with("Iran tensions elevated as of March 2026.")


async def test_post_versions_topic_not_registered(client):
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)  # topic not found
    pool = make_pool(conn)

    with patch("baseline_store.routes.get_pgvector_pool", AsyncMock(return_value=pool)), \
         patch("baseline_store.routes.get_embedder", return_value=AsyncMock()):
        resp = await client.post(
            "/baselines/nonexistent/versions",
            json={"narrative": "test", "citations": []},
        )

    assert resp.status_code == 404
    assert "not registered" in resp.json()["detail"]


async def test_post_versions_conflict_returns_409(client):
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(side_effect=[
        {"topic_path": "us_iran_conflict"},   # topic exists
        {"max": 3},                            # existing max version
        asyncpg.UniqueViolationError("conflict"),
    ])
    pool = make_pool(conn)
    mock_embed = AsyncMock(return_value=[0.1] * 10)

    with patch("baseline_store.routes.get_pgvector_pool", AsyncMock(return_value=pool)), \
         patch("baseline_store.routes.get_embedder", return_value=mock_embed):
        resp = await client.post(
            "/baselines/us_iran_conflict/versions",
            json={"narrative": "updated.", "citations": []},
        )

    assert resp.status_code == 409


# ── POST /baselines/{topic_path}/deltas ──────────────────────────────────────

async def test_post_deltas_happy_path(client):
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(side_effect=[
        {"version_number": 8},   # to_version exists
        {                         # INSERT result
            "id": "dddddddd-0000-0000-0000-000000000001",
            "created_at": "2026-03-25T09:00:01+00:00",
        },
    ])
    pool = make_pool(conn)

    with patch("baseline_store.routes.get_pgvector_pool", AsyncMock(return_value=pool)):
        resp = await client.post(
            "/baselines/us_iran_conflict/deltas",
            json={
                "from_version": 7,
                "to_version": 8,
                "article_metadata": {"article_id": "art1", "title": "Iran Update", "url": "https://example.com", "source": "AP", "published_at": "2026-03-25T00:00:00Z"},
                "delta_summary": "Iran crossed 60% enrichment threshold.",
                "claims_added": ["Iran crossed 60% enrichment"],
                "claims_superseded": ["Iran below 60% enrichment"],
            }
        )

    assert resp.status_code == 201
    body = resp.json()
    assert "id" in body
    assert "created_at" in body


async def test_post_deltas_to_version_not_found(client):
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)   # to_version does not exist
    pool = make_pool(conn)

    with patch("baseline_store.routes.get_pgvector_pool", AsyncMock(return_value=pool)):
        resp = await client.post(
            "/baselines/us_iran_conflict/deltas",
            json={
                "from_version": 7,
                "to_version": 99,
                "article_metadata": {},
                "delta_summary": "test",
                "claims_added": [],
                "claims_superseded": [],
            }
        )

    assert resp.status_code == 422
    assert "to_version" in resp.json()["detail"]


# ── GET /baselines/{topic_path}/current ──────────────────────────────────────

async def test_get_current_happy_path(client):
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(side_effect=[
        {"topic_path": "us_iran_conflict"},   # topic exists
        {                                      # current version
            "topic_path": "us_iran_conflict",
            "version_number": 4,
            "narrative": "Iran tensions remain elevated.",
            "citations": '[{"article_id":"art1","title":"...","url":"...","source":"AP","published_at":"...","excerpt":"..."}]',
            "created_at": "2026-03-25T09:00:00+00:00",
        },
    ])
    pool = make_pool(conn)

    with patch("baseline_store.routes.get_pgvector_pool", AsyncMock(return_value=pool)):
        resp = await client.get("/baselines/us_iran_conflict/current")

    assert resp.status_code == 200
    body = resp.json()
    assert body["topic_path"] == "us_iran_conflict"
    assert body["version_number"] == 4
    assert body["narrative"] == "Iran tensions remain elevated."
    assert isinstance(body["citations"], list)


async def test_get_current_topic_not_registered(client):
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)   # no topic row
    pool = make_pool(conn)

    with patch("baseline_store.routes.get_pgvector_pool", AsyncMock(return_value=pool)):
        resp = await client.get("/baselines/nonexistent/current")

    assert resp.status_code == 404
    assert "not registered" in resp.json()["detail"]


async def test_get_current_registered_but_no_versions(client):
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(side_effect=[
        {"topic_path": "us_iran_conflict"},   # topic exists
        None,                                  # no version row
    ])
    pool = make_pool(conn)

    with patch("baseline_store.routes.get_pgvector_pool", AsyncMock(return_value=pool)):
        resp = await client.get("/baselines/us_iran_conflict/current")

    assert resp.status_code == 404
    assert "No versions" in resp.json()["detail"]


# ── GET /baselines/{topic_path}/history ──────────────────────────────────────

async def test_get_history_happy_path(client):
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"topic_path": "us_iran_conflict"})
    conn.fetch = AsyncMock(side_effect=[
        [   # versions (newest first)
            {"version_number": 2, "narrative": "v2", "citations": "[]", "created_at": "2026-03-25T10:00:00+00:00"},
            {"version_number": 1, "narrative": "v1", "citations": "[]", "created_at": "2026-03-25T09:00:00+00:00"},
        ],
        [   # deltas
            {"from_version": 1, "to_version": 2, "delta_summary": "new info",
             "claims_added": "[]", "claims_superseded": "[]",
             "article_metadata": "{}", "created_at": "2026-03-25T10:00:00+00:00"},
        ],
    ])
    pool = make_pool(conn)

    with patch("baseline_store.routes.get_pgvector_pool", AsyncMock(return_value=pool)):
        resp = await client.get("/baselines/us_iran_conflict/history")

    assert resp.status_code == 200
    body = resp.json()
    assert body["topic_path"] == "us_iran_conflict"
    assert len(body["versions"]) == 2
    assert body["versions"][0]["version_number"] == 2   # newest first
    assert len(body["deltas"]) == 1


async def test_get_history_topic_not_registered(client):
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    pool = make_pool(conn)

    with patch("baseline_store.routes.get_pgvector_pool", AsyncMock(return_value=pool)):
        resp = await client.get("/baselines/nonexistent/history")

    assert resp.status_code == 404


async def test_get_history_registered_no_versions(client):
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"topic_path": "us_iran_conflict"})
    conn.fetch = AsyncMock(side_effect=[[], []])   # no versions, no deltas
    pool = make_pool(conn)

    with patch("baseline_store.routes.get_pgvector_pool", AsyncMock(return_value=pool)):
        resp = await client.get("/baselines/us_iran_conflict/history")

    assert resp.status_code == 200
    body = resp.json()
    assert body["versions"] == []
    assert body["deltas"] == []


# ── GET /baselines/{topic_path}/rollup ───────────────────────────────────────

async def test_get_rollup_returns_descendants(client):
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"topic_path": "climate_change"})
    conn.fetch = AsyncMock(return_value=[
        {
            "topic_path": "climate_change.energy",
            "version_number": 3,
            "narrative": "Energy baseline.",
            "citations": "[]",
            "created_at": "2026-03-25T09:00:00+00:00",
        },
    ])
    pool = make_pool(conn)

    with patch("baseline_store.routes.get_pgvector_pool", AsyncMock(return_value=pool)):
        resp = await client.get("/baselines/climate_change/rollup")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ancestor"] == "climate_change"
    assert len(body["descendants"]) == 1
    # Ancestor itself must not be in descendants
    assert all(d["topic_path"] != "climate_change" for d in body["descendants"])


async def test_get_rollup_no_descendants(client):
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"topic_path": "climate_change"})
    conn.fetch = AsyncMock(return_value=[])
    pool = make_pool(conn)

    with patch("baseline_store.routes.get_pgvector_pool", AsyncMock(return_value=pool)):
        resp = await client.get("/baselines/climate_change/rollup")

    assert resp.status_code == 200
    assert resp.json()["descendants"] == []


async def test_get_rollup_topic_not_registered(client):
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    pool = make_pool(conn)

    with patch("baseline_store.routes.get_pgvector_pool", AsyncMock(return_value=pool)):
        resp = await client.get("/baselines/nonexistent/rollup")

    assert resp.status_code == 404


# ── GET /baselines/similar ────────────────────────────────────────────────────

async def test_get_similar_returns_ranked_results(client):
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[
        {
            "topic_path": "us_iran_conflict",
            "version_number": 4,
            "narrative": "Iran tensions elevated.",
            "citations": "[]",
            "score": 0.91,
            "created_at": "2026-03-25T09:00:00+00:00",
        },
    ])
    pool = make_pool(conn)
    mock_embed = AsyncMock(return_value=[0.1] * 10)

    with patch("baseline_store.routes.get_pgvector_pool", AsyncMock(return_value=pool)), \
         patch("baseline_store.routes.get_embedder", return_value=mock_embed):
        resp = await client.get("/baselines/similar", params={"query": "Iran nuclear", "limit": 3})

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["results"]) == 1
    assert body["results"][0]["score"] == 0.91
    assert 0.0 <= body["results"][0]["score"] <= 1.0
    mock_embed.assert_called_once_with("Iran nuclear")


async def test_get_similar_embedder_called_once(client):
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    pool = make_pool(conn)
    mock_embed = AsyncMock(return_value=[0.1] * 10)

    with patch("baseline_store.routes.get_pgvector_pool", AsyncMock(return_value=pool)), \
         patch("baseline_store.routes.get_embedder", return_value=mock_embed):
        await client.get("/baselines/similar", params={"query": "test query"})

    mock_embed.assert_called_once_with("test query")
