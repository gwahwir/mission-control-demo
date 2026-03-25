"""Tests for the memory agent."""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from a2a.types import TaskState


# ---------------------------------------------------------------------------
# Async generator helper (used by executor write dispatch test)
# ---------------------------------------------------------------------------

async def _async_gen(items):
    """Yield items from a list as an async generator (for mocking astream)."""
    for item in items:
        yield item


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_config(task_id: str = "test-task-id") -> dict[str, Any]:
    executor = MagicMock()
    executor.check_cancelled = MagicMock()
    return {
        "configurable": {
            "executor": executor,
            "task_id": task_id,
            "context_id": "test-context-id",
        }
    }


SAMPLE_EXTRACTION = {
    "entities": [
        {"name": "Apple", "type": "organization", "attributes": {"sector": "technology"}},
        {"name": "Beats", "type": "organization", "attributes": {}},
    ],
    "relationships": [
        {"subject": "Apple", "predicate": "acquired", "object": "Beats"},
    ],
    "summary": "Apple acquired Beats Electronics in 2014 for $3 billion.",
}


# ---------------------------------------------------------------------------
# stores.py — env var validation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stores_pgvector_missing_dsn(monkeypatch):
    """get_pgvector_pool() raises EnvironmentError when MEMORY_PG_DSN is not set."""
    import agents.memory_agent.stores as stores
    stores._pool = None  # reset singleton
    monkeypatch.delenv("MEMORY_PG_DSN", raising=False)
    monkeypatch.delenv("MEMORY_EMBEDDING_DIMS", raising=False)
    with pytest.raises(EnvironmentError, match="MEMORY_PG_DSN"):
        await stores.get_pgvector_pool()


@pytest.mark.asyncio
async def test_stores_pgvector_missing_dims(monkeypatch):
    """get_pgvector_pool() raises EnvironmentError when MEMORY_EMBEDDING_DIMS is not set."""
    import agents.memory_agent.stores as stores
    stores._pool = None
    monkeypatch.setenv("MEMORY_PG_DSN", "postgresql://x:y@localhost/db")
    monkeypatch.delenv("MEMORY_EMBEDDING_DIMS", raising=False)
    with pytest.raises(EnvironmentError, match="MEMORY_EMBEDDING_DIMS"):
        await stores.get_pgvector_pool()


def test_stores_neo4j_missing_env(monkeypatch):
    """get_neo4j_graph() raises EnvironmentError when Neo4j env vars are missing."""
    import agents.memory_agent.stores as stores
    stores._neo4j = None
    monkeypatch.delenv("MEMORY_NEO4J_URL", raising=False)
    monkeypatch.delenv("MEMORY_NEO4J_USER", raising=False)
    monkeypatch.delenv("MEMORY_NEO4J_PASSWORD", raising=False)
    with pytest.raises(EnvironmentError, match="MEMORY_NEO4J"):
        stores.get_neo4j_graph()


def test_stores_embedder_missing_model(monkeypatch):
    """get_embedder() raises EnvironmentError when MEMORY_EMBEDDING_MODEL is not set."""
    import agents.memory_agent.stores as stores
    stores._embedder = None
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("MEMORY_EMBEDDING_MODEL", raising=False)
    with pytest.raises(EnvironmentError, match="MEMORY_EMBEDDING_MODEL"):
        stores.get_embedder()


def test_stores_embedder_missing_api_key(monkeypatch):
    """get_embedder() raises EnvironmentError when OPENAI_API_KEY is not set."""
    import agents.memory_agent.stores as stores
    stores._embedder = None
    monkeypatch.setenv("MEMORY_EMBEDDING_MODEL", "text-embedding-3-small")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(EnvironmentError, match="OPENAI_API_KEY"):
        stores.get_embedder()


# ---------------------------------------------------------------------------
# graph.py — extract_entities node
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extract_entities_happy_path():
    """Valid JSON on first attempt sets extracted, retry_count stays 0."""
    from agents.memory_agent.graph import extract_entities

    state = {
        "input": "Apple acquired Beats in 2014 for $3 billion.",
        "namespace": "test_ns",
        "extracted": None,
        "retry_count": 0,
        "last_raw": "",
        "last_error": "",
        "stored": False,
        "entities_added": 0,
        "relationships_added": 0,
    }
    mock_response = MagicMock()
    mock_response.choices[0].message.content = json.dumps(SAMPLE_EXTRACTION)

    with patch("agents.memory_agent.graph._get_openai_client") as mock_fn:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_fn.return_value = mock_client
        result = await extract_entities(state, make_config())

    assert result["extracted"] == SAMPLE_EXTRACTION
    assert result["retry_count"] == 0
    assert result["last_error"] == ""


@pytest.mark.asyncio
async def test_extract_entities_retry_on_bad_json():
    """Bad JSON increments retry_count and sets extracted=None."""
    from agents.memory_agent.graph import extract_entities

    state = {
        "input": "Some text.",
        "namespace": "test_ns",
        "extracted": None,
        "retry_count": 0,
        "last_raw": "",
        "last_error": "",
        "stored": False,
        "entities_added": 0,
        "relationships_added": 0,
    }
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "not valid json {{{"

    with patch("agents.memory_agent.graph._get_openai_client") as mock_fn:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_fn.return_value = mock_client
        result = await extract_entities(state, make_config())

    assert result["extracted"] is None
    assert result["retry_count"] == 1
    assert result["last_raw"] == "not valid json {{{"
    assert result["last_error"] != ""


@pytest.mark.asyncio
async def test_extract_entities_retry_injects_error_context():
    """On retry, the previous error and raw output are injected into the LLM prompt."""
    from agents.memory_agent.graph import extract_entities

    captured_messages = []

    async def mock_create(**kwargs):
        captured_messages.append(kwargs["messages"])
        resp = MagicMock()
        resp.choices[0].message.content = json.dumps(SAMPLE_EXTRACTION)
        return resp

    state = {
        "input": "Some text.",
        "namespace": "test_ns",
        "extracted": None,
        "retry_count": 1,
        "last_raw": "bad output here",
        "last_error": "Expecting value",
        "stored": False,
        "entities_added": 0,
        "relationships_added": 0,
    }
    with patch("agents.memory_agent.graph._get_openai_client") as mock_fn:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = mock_create
        mock_fn.return_value = mock_client
        await extract_entities(state, make_config())

    user_msg = captured_messages[0][-1]["content"]
    assert "bad output here" in user_msg or "Expecting value" in user_msg


@pytest.mark.asyncio
async def test_extract_entities_exhausted_returns_empty():
    """When retry_count >= 3, returns empty extraction without calling LLM."""
    from agents.memory_agent.graph import extract_entities

    state = {
        "input": "Some text.",
        "namespace": "test_ns",
        "extracted": None,
        "retry_count": 3,
        "last_raw": "",
        "last_error": "",
        "stored": False,
        "entities_added": 0,
        "relationships_added": 0,
    }
    with patch("agents.memory_agent.graph._get_openai_client") as mock_fn:
        mock_fn.return_value = AsyncMock()  # should never be called
        result = await extract_entities(state, make_config())

    assert result["extracted"] == {"entities": [], "relationships": [], "summary": ""}
    mock_fn.return_value.chat.completions.create.assert_not_called()


# ---------------------------------------------------------------------------
# graph.py — resolve_conflicts node
# ---------------------------------------------------------------------------

CONFLICT_BASE_STATE = {
    "input": "Apple acquired Beats in 2014.",
    "namespace": "test_ns",
    "extracted": dict(SAMPLE_EXTRACTION),  # copy so tests don't share mutable state
    "retry_count": 0,
    "last_raw": "",
    "last_error": "",
    "stored": False,
    "entities_added": 0,
    "relationships_added": 0,
    "memories_updated": 0,
    "memories_deleted": 0,
}


@pytest.mark.asyncio
async def test_resolve_conflicts_no_existing_memories():
    """When no existing memories match, extracted passes through unchanged — no LLM call."""
    from agents.memory_agent.graph import resolve_conflicts

    mock_pool = AsyncMock()
    mock_conn = AsyncMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    mock_conn.fetch = AsyncMock(return_value=[])  # no existing memories

    with patch("agents.memory_agent.graph.get_pgvector_pool", return_value=mock_pool), \
         patch("agents.memory_agent.graph.embed_text", AsyncMock(return_value=[0.1] * 10)), \
         patch("agents.memory_agent.graph._get_openai_client") as mock_llm_fn:
        result = await resolve_conflicts(dict(CONFLICT_BASE_STATE), make_config())

    # No conflict LLM call when no existing memories
    mock_llm_fn.assert_not_called()
    assert result["extracted"] == SAMPLE_EXTRACTION
    assert result["memories_updated"] == 0
    assert result["memories_deleted"] == 0


@pytest.mark.asyncio
async def test_resolve_conflicts_update_resolution():
    """UPDATE resolution: existing memory row is overwritten with new content."""
    from agents.memory_agent.graph import resolve_conflicts

    existing_row = {
        "id": "row-uuid-1",
        "content": "Organization: Apple (old)",
        "score": 0.92,
        "metadata": json.dumps({"entities": ["Apple"], "namespace": "test_ns", "source": "write"}),
    }

    mock_pool = AsyncMock()
    mock_conn = AsyncMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    mock_conn.fetch = AsyncMock(return_value=[existing_row])
    mock_conn.execute = AsyncMock()

    resolution_response = MagicMock()
    resolution_response.choices[0].message.content = json.dumps([
        {"action": "UPDATE", "id": "row-uuid-1", "new_content": "Organization: Apple (updated)"}
    ])

    with patch("agents.memory_agent.graph.get_pgvector_pool", return_value=mock_pool), \
         patch("agents.memory_agent.graph.embed_text", AsyncMock(return_value=[0.2] * 10)), \
         patch("agents.memory_agent.graph._get_openai_client") as mock_llm_fn:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=resolution_response)
        mock_llm_fn.return_value = mock_client
        result = await resolve_conflicts(dict(CONFLICT_BASE_STATE), make_config())

    assert result["memories_updated"] == 1
    assert result["memories_deleted"] == 0
    # UPDATE issues an asyncpg execute call for the row
    mock_conn.execute.assert_called()
    update_call_args = str(mock_conn.execute.call_args_list)
    assert "UPDATE" in update_call_args.upper() or "row-uuid-1" in update_call_args


@pytest.mark.asyncio
async def test_resolve_conflicts_delete_resolution():
    """DELETE resolution: existing memory row is removed from pgvector and Neo4j."""
    from agents.memory_agent.graph import resolve_conflicts

    existing_row = {
        "id": "row-uuid-2",
        "content": "Organization: Beats (stale)",
        "score": 0.88,
        "metadata": json.dumps({"entities": ["Beats"], "namespace": "test_ns", "source": "write"}),
    }

    mock_pool = AsyncMock()
    mock_conn = AsyncMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    mock_conn.fetch = AsyncMock(return_value=[existing_row])
    mock_conn.execute = AsyncMock()

    mock_neo4j = MagicMock()
    mock_neo4j.query = MagicMock(return_value=[])

    resolution_response = MagicMock()
    resolution_response.choices[0].message.content = json.dumps([
        {"action": "DELETE", "id": "row-uuid-2"}
    ])

    with patch("agents.memory_agent.graph.get_pgvector_pool", return_value=mock_pool), \
         patch("agents.memory_agent.graph.get_neo4j_graph", return_value=mock_neo4j), \
         patch("agents.memory_agent.graph.embed_text", AsyncMock(return_value=[0.1] * 10)), \
         patch("agents.memory_agent.graph._get_openai_client") as mock_llm_fn:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=resolution_response)
        mock_llm_fn.return_value = mock_client
        result = await resolve_conflicts(dict(CONFLICT_BASE_STATE), make_config())

    assert result["memories_deleted"] == 1
    assert result["memories_updated"] == 0
    # DELETE issues a pgvector DELETE call
    delete_call_args = str(mock_conn.execute.call_args_list)
    assert "DELETE" in delete_call_args.upper() or "row-uuid-2" in delete_call_args


@pytest.mark.asyncio
async def test_resolve_conflicts_llm_failure_fallback():
    """If conflict LLM returns invalid JSON, log warning and pass extracted through unchanged."""
    from agents.memory_agent.graph import resolve_conflicts

    existing_row = {
        "id": "row-uuid-3",
        "content": "Organization: Apple (existing)",
        "score": 0.90,
        "metadata": json.dumps({"entities": ["Apple"], "namespace": "test_ns", "source": "write"}),
    }

    mock_pool = AsyncMock()
    mock_conn = AsyncMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    mock_conn.fetch = AsyncMock(return_value=[existing_row])

    bad_response = MagicMock()
    bad_response.choices[0].message.content = "not valid json {{{"

    with patch("agents.memory_agent.graph.get_pgvector_pool", return_value=mock_pool), \
         patch("agents.memory_agent.graph.embed_text", AsyncMock(return_value=[0.1] * 10)), \
         patch("agents.memory_agent.graph._get_openai_client") as mock_llm_fn:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=bad_response)
        mock_llm_fn.return_value = mock_client
        result = await resolve_conflicts(dict(CONFLICT_BASE_STATE), make_config())

    # Fallback: extracted unchanged, no updates or deletes
    assert result["extracted"] == SAMPLE_EXTRACTION
    assert result["memories_updated"] == 0
    assert result["memories_deleted"] == 0


# ---------------------------------------------------------------------------
# graph.py — store_memories node
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_store_memories_happy_path():
    """Both backends called; entities_added and relationships_added match extraction."""
    from agents.memory_agent.graph import store_memories

    state = {
        "input": "Apple acquired Beats.",
        "namespace": "test_ns",
        "extracted": SAMPLE_EXTRACTION,
        "retry_count": 0,
        "last_raw": "",
        "last_error": "",
        "stored": False,
        "entities_added": 0,
        "relationships_added": 0,
    }

    mock_pool = AsyncMock()
    mock_conn = AsyncMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    mock_conn.execute = AsyncMock()

    mock_neo4j = MagicMock()
    mock_neo4j.query = MagicMock(return_value=[])

    mock_embed = AsyncMock(return_value=[0.1] * 10)

    with patch("agents.memory_agent.graph.get_pgvector_pool", return_value=mock_pool), \
         patch("agents.memory_agent.graph.get_neo4j_graph", return_value=mock_neo4j), \
         patch("agents.memory_agent.graph.embed_text", mock_embed):
        result = await store_memories(state, make_config())

    # 2 entities + 1 summary row → 3 embed calls
    assert mock_embed.call_count == 3
    # 2 entities + 1 summary → 3 INSERT calls
    assert mock_conn.execute.call_count == 3
    # 2 entities + 1 relationship → 3 Neo4j queries
    assert mock_neo4j.query.call_count == 3
    assert result["entities_added"] == 2
    assert result["relationships_added"] == 1
    assert result["stored"] is True


@pytest.mark.asyncio
async def test_store_memories_partial_failure_continues():
    """A single embed failure logs a warning but does not abort the whole store."""
    from agents.memory_agent.graph import store_memories

    state = {
        "input": "Apple acquired Beats.",
        "namespace": "test_ns",
        "extracted": SAMPLE_EXTRACTION,
        "retry_count": 0,
        "last_raw": "",
        "last_error": "",
        "stored": False,
        "entities_added": 0,
        "relationships_added": 0,
    }

    call_count = 0

    async def embed_side_effect(text):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("embedding failed")
        return [0.1] * 10

    mock_pool = AsyncMock()
    mock_conn = AsyncMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    mock_conn.execute = AsyncMock()
    mock_neo4j = MagicMock()
    mock_neo4j.query = MagicMock(return_value=[])

    with patch("agents.memory_agent.graph.get_pgvector_pool", return_value=mock_pool), \
         patch("agents.memory_agent.graph.get_neo4j_graph", return_value=mock_neo4j), \
         patch("agents.memory_agent.graph.embed_text", side_effect=embed_side_effect):
        result = await store_memories(state, make_config())

    # First entity failed but second entity + summary succeeded → stored=True
    assert result["stored"] is True
    assert result["entities_added"] == 1  # one succeeded


@pytest.mark.asyncio
async def test_store_memories_all_fail_stored_false():
    """If all embed+insert calls fail, stored is False (task still completes)."""
    from agents.memory_agent.graph import store_memories

    state = {
        "input": "Apple acquired Beats.",
        "namespace": "test_ns",
        "extracted": SAMPLE_EXTRACTION,
        "retry_count": 0,
        "last_raw": "",
        "last_error": "",
        "stored": False,
        "entities_added": 0,
        "relationships_added": 0,
    }

    async def always_fail(text):
        raise Exception("always fails")

    mock_pool = AsyncMock()
    mock_neo4j = MagicMock()
    mock_neo4j.query = MagicMock(side_effect=Exception("neo4j also fails"))

    with patch("agents.memory_agent.graph.get_pgvector_pool", return_value=mock_pool), \
         patch("agents.memory_agent.graph.get_neo4j_graph", return_value=mock_neo4j), \
         patch("agents.memory_agent.graph.embed_text", side_effect=always_fail):
        result = await store_memories(state, make_config())

    assert result["stored"] is False
    assert result["entities_added"] == 0
    assert result["relationships_added"] == 0


# ---------------------------------------------------------------------------
# Full WriteGraph — end-to-end including retry loop
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_write_graph_full_pipeline():
    """Full WriteGraph: raw text in, stored=True and correct counts out."""
    from agents.memory_agent.graph import build_memory_write_graph

    graph = build_memory_write_graph()

    mock_llm_response = MagicMock()
    mock_llm_response.choices[0].message.content = json.dumps(SAMPLE_EXTRACTION)

    mock_pool = AsyncMock()
    mock_conn = AsyncMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    mock_conn.execute = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[])  # no existing memories → resolve_conflicts skips LLM

    mock_neo4j = MagicMock()
    mock_neo4j.query = MagicMock(return_value=[])
    executor = MagicMock()
    executor.check_cancelled = MagicMock()

    with patch("agents.memory_agent.graph._get_openai_client") as mock_fn, \
         patch("agents.memory_agent.graph.get_pgvector_pool", return_value=mock_pool), \
         patch("agents.memory_agent.graph.get_neo4j_graph", return_value=mock_neo4j), \
         patch("agents.memory_agent.graph.embed_text", AsyncMock(return_value=[0.1] * 10)):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_llm_response)
        mock_fn.return_value = mock_client

        result = await graph.ainvoke(
            {
                "input": "Apple acquired Beats in 2014.",
                "namespace": "test_ns",
                "extracted": None,
                "retry_count": 0,
                "last_raw": "",
                "last_error": "",
                "stored": False,
                "entities_added": 0,
                "relationships_added": 0,
                "memories_updated": 0,
                "memories_deleted": 0,
            },
            config={"configurable": {"executor": executor, "task_id": "t1", "context_id": "c1"}},
        )

    assert result["stored"] is True
    assert result["entities_added"] == 2
    assert result["relationships_added"] == 1
    assert result["memories_updated"] == 0
    assert result["memories_deleted"] == 0


@pytest.mark.asyncio
async def test_write_graph_retry_succeeds_on_second_attempt():
    """Graph retries on bad JSON and succeeds on the second LLM call."""
    from agents.memory_agent.graph import build_memory_write_graph

    graph = build_memory_write_graph()
    call_count = 0

    async def mock_create(**kwargs):
        nonlocal call_count
        call_count += 1
        resp = MagicMock()
        if call_count == 1:
            resp.choices[0].message.content = "not valid json"
        else:
            resp.choices[0].message.content = json.dumps(SAMPLE_EXTRACTION)
        return resp

    mock_pool = AsyncMock()
    mock_conn = AsyncMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    mock_conn.execute = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[])  # no existing memories
    mock_neo4j = MagicMock()
    mock_neo4j.query = MagicMock(return_value=[])
    executor = MagicMock()
    executor.check_cancelled = MagicMock()

    with patch("agents.memory_agent.graph._get_openai_client") as mock_fn, \
         patch("agents.memory_agent.graph.get_pgvector_pool", return_value=mock_pool), \
         patch("agents.memory_agent.graph.get_neo4j_graph", return_value=mock_neo4j), \
         patch("agents.memory_agent.graph.embed_text", AsyncMock(return_value=[0.1] * 10)):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = mock_create
        mock_fn.return_value = mock_client

        result = await graph.ainvoke(
            {
                "input": "Apple acquired Beats.",
                "namespace": "test_ns",
                "extracted": None,
                "retry_count": 0,
                "last_raw": "",
                "last_error": "",
                "stored": False,
                "entities_added": 0,
                "relationships_added": 0,
                "memories_updated": 0,
                "memories_deleted": 0,
            },
            config={"configurable": {"executor": executor, "task_id": "t2", "context_id": "c2"}},
        )

    assert call_count == 2
    assert result["stored"] is True


@pytest.mark.asyncio
async def test_write_graph_cancellation():
    """check_cancelled() is called in each node — raises CancelledError immediately."""
    import asyncio
    from agents.memory_agent.graph import extract_entities, resolve_conflicts, store_memories
    from agents.base.cancellation import CancellableMixin

    class CancellingExecutor(CancellableMixin):
        def __init__(self):
            super().__init__()
        def check_cancelled(self, task_id):
            raise asyncio.CancelledError("cancelled")

    executor = CancellingExecutor()
    config = {"configurable": {"executor": executor, "task_id": "t", "context_id": "c"}}
    state = {
        "input": "text", "namespace": "ns",
        "extracted": None, "retry_count": 0, "last_raw": "", "last_error": "",
        "stored": False, "entities_added": 0, "relationships_added": 0,
        "memories_updated": 0, "memories_deleted": 0,
    }
    with pytest.raises(asyncio.CancelledError):
        await extract_entities(state, config)

    state2 = {**state, "extracted": SAMPLE_EXTRACTION}
    with pytest.raises(asyncio.CancelledError):
        await resolve_conflicts(state2, config)

    with pytest.raises(asyncio.CancelledError):
        await store_memories(state2, config)


# ---------------------------------------------------------------------------
# executor.py — _search_memories
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_memories_returns_ranked_results():
    """search_memories returns results sorted by score with raw metadata."""
    from agents.memory_agent.executor import _search_memories

    executor = MagicMock()
    executor.check_cancelled = MagicMock()

    fake_rows = [
        {"content": "Organization: Apple", "score": 0.95,
         "metadata": {"entities": ["Apple"], "namespace": "test_ns", "source": "write"}},
        {"content": "Organization: Beats", "score": 0.82,
         "metadata": {"entities": ["Beats"], "namespace": "test_ns", "source": "write"}},
    ]

    mock_pool = AsyncMock()
    mock_conn = AsyncMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    mock_conn.fetch = AsyncMock(return_value=[
        {"content": r["content"], "score": r["score"], "metadata": json.dumps(r["metadata"])}
        for r in fake_rows
    ])

    with patch("agents.memory_agent.executor.get_pgvector_pool", return_value=mock_pool), \
         patch("agents.memory_agent.executor.embed_text", AsyncMock(return_value=[0.1] * 10)):
        result = await _search_memories(executor, "task-1", {"query": "Apple", "namespace": "test_ns", "limit": 5})

    assert len(result["results"]) == 2
    assert result["results"][0]["content"] == "Organization: Apple"
    assert result["results"][0]["score"] == 0.95
    assert result["results"][0]["metadata"]["entities"] == ["Apple"]


@pytest.mark.asyncio
async def test_search_memories_namespace_isolation():
    """Search only returns rows matching the requested namespace."""
    from agents.memory_agent.executor import _search_memories

    executor = MagicMock()
    executor.check_cancelled = MagicMock()

    mock_pool = AsyncMock()
    mock_conn = AsyncMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    # DB returns no rows (namespace mismatch simulated by empty result)
    mock_conn.fetch = AsyncMock(return_value=[])

    with patch("agents.memory_agent.executor.get_pgvector_pool", return_value=mock_pool), \
         patch("agents.memory_agent.executor.embed_text", AsyncMock(return_value=[0.1] * 10)):
        result = await _search_memories(executor, "task-1", {"query": "Apple", "namespace": "other_ns"})

    assert result["results"] == []
    # Verify the query included the namespace filter
    call_args = mock_conn.fetch.call_args
    assert "other_ns" in str(call_args)


@pytest.mark.asyncio
async def test_search_memories_cancellation():
    """check_cancelled is called before the DB query — raises CancelledError."""
    import asyncio
    from agents.memory_agent.executor import _search_memories
    from agents.base.cancellation import CancellableMixin

    class CancellingExecutor(CancellableMixin):
        def __init__(self):
            super().__init__()
        def check_cancelled(self, task_id):
            raise asyncio.CancelledError("cancelled")

    with patch("agents.memory_agent.executor.embed_text", AsyncMock(return_value=[0.1] * 10)):
        with pytest.raises(asyncio.CancelledError):
            await _search_memories(
                CancellingExecutor(), "t1",
                {"query": "Apple", "namespace": "ns"},
            )


# ---------------------------------------------------------------------------
# executor.py — _traverse_graph
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_traverse_graph_returns_nodes_and_edges():
    """traverse_graph returns correctly shaped nodes and edges."""
    from agents.memory_agent.executor import _traverse_graph

    executor = MagicMock()
    executor.check_cancelled = MagicMock()

    fake_query_result = [
        {
            "n": {"name": "Apple", "type": "organization", "namespace": "test_ns"},
            "r": [{"predicate": "acquired", "namespace": "test_ns"}],
            "m": {"name": "Beats", "type": "organization", "namespace": "test_ns"},
        }
    ]

    mock_neo4j = MagicMock()
    mock_neo4j.query = MagicMock(return_value=fake_query_result)

    with patch("agents.memory_agent.executor.get_neo4j_graph", return_value=mock_neo4j), \
         patch("agents.memory_agent.executor.asyncio.to_thread", new=AsyncMock(return_value=fake_query_result)):
        result = await _traverse_graph(
            executor, "task-1",
            {"entity": "Apple", "namespace": "test_ns", "depth": 2},
        )

    assert "nodes" in result
    assert "edges" in result
    assert len(result["nodes"]) == 2
    assert len(result["edges"]) == 1
    node_names = {n["name"] for n in result["nodes"]}
    assert "Apple" in node_names
    assert "Beats" in node_names
    assert result["edges"][0]["predicate"] == "acquired"


@pytest.mark.asyncio
async def test_traverse_graph_cancellation():
    """check_cancelled is called before the Cypher query."""
    import asyncio
    from agents.memory_agent.executor import _traverse_graph
    from agents.base.cancellation import CancellableMixin

    class CancellingExecutor(CancellableMixin):
        def __init__(self):
            super().__init__()
        def check_cancelled(self, task_id):
            raise asyncio.CancelledError("cancelled")

    with pytest.raises(asyncio.CancelledError):
        await _traverse_graph(
            CancellingExecutor(), "t1",
            {"entity": "Apple", "namespace": "ns", "depth": 2},
        )


# ---------------------------------------------------------------------------
# MemoryAgentExecutor — dispatch + error handling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_executor_unknown_operation_emits_failed():
    """Unknown operation emits TaskState.failed with a descriptive message."""
    from agents.memory_agent.executor import MemoryAgentExecutor

    executor = MemoryAgentExecutor()
    context = MagicMock()
    context.message = MagicMock()
    context.message.metadata = {}
    context.task_id = "t1"
    context.context_id = "c1"
    context.get_user_input = MagicMock(return_value=json.dumps({"operation": "banana"}))

    emitted = []

    class CollectingQueue:
        async def enqueue_event(self, event):
            emitted.append(event)

    await executor.execute(context, CollectingQueue())

    final_event = emitted[-1]
    assert final_event.status.state == TaskState.failed
    assert "Unknown operation" in final_event.status.message.parts[0].root.text


@pytest.mark.asyncio
async def test_executor_invalid_json_emits_failed():
    """Non-JSON input emits TaskState.failed."""
    from agents.memory_agent.executor import MemoryAgentExecutor

    executor = MemoryAgentExecutor()
    context = MagicMock()
    context.message = MagicMock()
    context.message.metadata = {}
    context.task_id = "t2"
    context.context_id = "c2"
    context.get_user_input = MagicMock(return_value="not json at all")

    emitted = []

    class CollectingQueue:
        async def enqueue_event(self, event):
            emitted.append(event)

    await executor.execute(context, CollectingQueue())

    final_event = emitted[-1]
    assert final_event.status.state == TaskState.failed


@pytest.mark.asyncio
async def test_executor_write_dispatches_graph_and_emits_completed():
    """Write operation runs the WriteGraph and emits TaskState.completed with stored output."""
    from agents.memory_agent.executor import MemoryAgentExecutor

    executor = MemoryAgentExecutor()
    context = MagicMock()
    context.message = MagicMock()
    context.message.metadata = {}
    context.task_id = "t3"
    context.context_id = "c3"
    context.get_user_input = MagicMock(
        return_value=json.dumps({"operation": "write", "text": "Apple acquired Beats.", "namespace": "test_ns"})
    )

    emitted = []

    class CollectingQueue:
        async def enqueue_event(self, event):
            emitted.append(event)

    # Mock the WriteGraph so we don't need real stores
    mock_graph = MagicMock()
    mock_graph.astream = MagicMock(return_value=_async_gen([
        {"extract_entities": {"extracted": SAMPLE_EXTRACTION, "retry_count": 0}},
        {"store_memories": {"stored": True, "entities_added": 2, "relationships_added": 1}},
    ]))

    with patch.object(executor, "_graph", mock_graph):
        await executor.execute(context, CollectingQueue())

    final_event = emitted[-1]
    assert final_event.status.state == TaskState.completed
    output = json.loads(final_event.status.message.parts[0].root.text)
    assert output["stored"] is True
    assert output["entities_added"] == 2
    assert output["relationships_added"] == 1
