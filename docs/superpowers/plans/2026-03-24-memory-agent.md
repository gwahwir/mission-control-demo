# Memory Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a dual-store memory agent (pgvector + Neo4j, no mem0) that exposes write/search/traverse skills so any other agent in Mission Control can persist and retrieve memories.

**Architecture:** A LangGraph WriteGraph handles the multi-step write path (LLM extraction → dual-store); search and traverse are direct async functions called from a custom `execute()` override. All three operations are exposed as A2A skills on port 8009.

**Tech Stack:** `asyncpg` (pgvector), `langchain_neo4j` (Neo4j), `openai` (embeddings + LLM extraction), `langgraph`, `a2a-sdk[http-server]`

**Spec:** `docs/superpowers/specs/2026-03-24-memory-agent-design.md`

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `agents/memory_agent/__init__.py` | Create | Package marker |
| `agents/memory_agent/stores.py` | Create | Lazy singletons: pgvector pool, Neo4j graph, embedder |
| `agents/memory_agent/graph.py` | Create | WriteGraph state, extract_entities node, store_memories node |
| `agents/memory_agent/executor.py` | Create | MemoryAgentExecutor with execute() override; _search_memories(), _traverse_graph() |
| `agents/memory_agent/server.py` | Create | A2A FastAPI app, port 8009, 3 skills, /graph endpoint |
| `agents/memory_agent/README.md` | Create | Docs |
| `agents/memory_agent/__init__.py` | Create | Empty package marker |
| `tests/test_memory_agent.py` | Create | All 8 test cases |
| `Dockerfile.memory-agent` | Create | Container build |
| `docker-compose.yml` | Modify | Add memory-agent service |
| `run-local.sh` | Modify | Add memory-agent startup step |
| `CLAUDE.md` | Modify | Add agent row + env vars |

---

## Task 1: `stores.py` — Lazy singletons

**Files:**
- Create: `agents/memory_agent/stores.py`
- Create: `agents/memory_agent/__init__.py`
- Test: `tests/test_memory_agent.py` (first batch of tests)

- [ ] **Step 1.1: Create the package marker**

```python
# agents/memory_agent/__init__.py
# (empty)
```

- [ ] **Step 1.2: Write the failing tests for `stores.py`**

Create `tests/test_memory_agent.py`:

```python
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
```

- [ ] **Step 1.3: Run tests — expect FAIL (module not found)**

```bash
pytest tests/test_memory_agent.py::test_stores_pgvector_missing_dsn tests/test_memory_agent.py::test_stores_neo4j_missing_env tests/test_memory_agent.py::test_stores_embedder_missing_model -v
```

Expected: `ModuleNotFoundError: No module named 'agents.memory_agent.stores'`

- [ ] **Step 1.4: Implement `stores.py`**

```python
"""Lazy singletons for pgvector (asyncpg), Neo4j (langchain_neo4j), and the OpenAI embedder."""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

_pool = None
_neo4j = None
_embedder = None  # tuple: (AsyncOpenAI, model_name)


async def get_pgvector_pool():
    """Return an asyncpg connection pool, creating the memories table on first call."""
    global _pool
    if _pool is None:
        dsn = os.getenv("MEMORY_PG_DSN")
        if not dsn:
            raise EnvironmentError("MEMORY_PG_DSN is required for the memory agent")
        dims = os.getenv("MEMORY_EMBEDDING_DIMS")
        if not dims:
            raise EnvironmentError("MEMORY_EMBEDDING_DIMS is required for the memory agent")
        dims = int(dims)

        import asyncpg
        pool = await asyncpg.create_pool(dsn)
        async with pool.acquire() as conn:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            await conn.execute(f"""
                CREATE TABLE IF NOT EXISTS memories (
                    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    namespace   TEXT NOT NULL,
                    content     TEXT NOT NULL,
                    embedding   vector({dims}),
                    metadata    JSONB DEFAULT '{{}}',
                    created_at  TIMESTAMPTZ DEFAULT now()
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS memories_namespace_idx ON memories (namespace)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS memories_embedding_idx ON memories "
                "USING hnsw (embedding vector_cosine_ops)"
            )
        _pool = pool
    return _pool


def get_neo4j_graph():
    """Return a langchain_neo4j Neo4jGraph instance."""
    global _neo4j
    if _neo4j is None:
        url = os.getenv("MEMORY_NEO4J_URL")
        user = os.getenv("MEMORY_NEO4J_USER")
        password = os.getenv("MEMORY_NEO4J_PASSWORD")
        missing = [
            k for k, v in {
                "MEMORY_NEO4J_URL": url,
                "MEMORY_NEO4J_USER": user,
                "MEMORY_NEO4J_PASSWORD": password,
            }.items()
            if not v
        ]
        if missing:
            raise EnvironmentError(
                f"Required env vars missing for memory agent: {', '.join(missing)}"
            )
        from langchain_neo4j import Neo4jGraph
        _neo4j = Neo4jGraph(url=url, username=user, password=password)
    return _neo4j


def get_embedder():
    """Return (AsyncOpenAI client, model_name) for generating embeddings."""
    global _embedder
    if _embedder is None:
        model = os.getenv("MEMORY_EMBEDDING_MODEL")
        api_key = os.getenv("OPENAI_API_KEY")
        if not model:
            raise EnvironmentError("MEMORY_EMBEDDING_MODEL is required for the memory agent")
        if not api_key:
            raise EnvironmentError("OPENAI_API_KEY is required for the memory agent")
        from openai import AsyncOpenAI
        kwargs: dict[str, Any] = {"api_key": api_key}
        base_url = os.getenv("OPENAI_BASE_URL")
        if base_url:
            kwargs["base_url"] = base_url
        _embedder = (AsyncOpenAI(**kwargs), model)
    return _embedder


async def embed_text(text: str) -> list[float]:
    """Embed a single string and return a list of floats."""
    client, model = get_embedder()
    response = await client.embeddings.create(input=text, model=model)
    return response.data[0].embedding
```

- [ ] **Step 1.5: Run the stores tests — expect PASS**

```bash
pytest tests/test_memory_agent.py::test_stores_pgvector_missing_dsn tests/test_memory_agent.py::test_stores_pgvector_missing_dims tests/test_memory_agent.py::test_stores_neo4j_missing_env tests/test_memory_agent.py::test_stores_embedder_missing_model tests/test_memory_agent.py::test_stores_embedder_missing_api_key -v
```

Expected: 5 passed.

- [ ] **Step 1.6: Commit**

```bash
git add agents/memory_agent/__init__.py agents/memory_agent/stores.py tests/test_memory_agent.py
git commit -m "feat(memory-agent): add stores.py with lazy pgvector, Neo4j, and embedder singletons"
```

---

## Task 2: `graph.py` — `extract_entities` node

**Files:**
- Create: `agents/memory_agent/graph.py` (partial — state + extract node only)
- Test: `tests/test_memory_agent.py` (append)

- [ ] **Step 2.1: Append failing tests for the extract node**

Append to `tests/test_memory_agent.py`:

```python
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
```

- [ ] **Step 2.2: Run — expect FAIL (module not found)**

```bash
pytest tests/test_memory_agent.py::test_extract_entities_happy_path -v
```

Expected: `ModuleNotFoundError: No module named 'agents.memory_agent.graph'`

- [ ] **Step 2.3: Implement `graph.py` with state + extract_entities node**

```python
"""WriteGraph for the memory agent: extract_entities → store_memories."""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

logger = logging.getLogger(__name__)

MAX_RETRIES = 3

_openai_client = None


def _get_openai_client():
    global _openai_client
    if _openai_client is None:
        from openai import AsyncOpenAI
        kwargs: dict[str, Any] = {}
        api_key = os.getenv("OPENAI_API_KEY")
        base_url = os.getenv("OPENAI_BASE_URL")
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        _openai_client = AsyncOpenAI(**kwargs)
    return _openai_client


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class MemoryWriteState(TypedDict):
    input: str
    namespace: str
    extracted: Optional[dict]
    retry_count: int
    last_raw: str
    last_error: str
    stored: bool
    entities_added: int
    relationships_added: int


# ---------------------------------------------------------------------------
# Extraction prompt
# ---------------------------------------------------------------------------

_EXTRACTION_SYSTEM_PROMPT = """\
You are a memory extraction engine. Given a blob of text, extract all key information
and return ONLY a valid JSON object with this exact schema:

{
  "entities": [
    {"name": "Full Name", "type": "person|organization|location|product|concept", "attributes": {}}
  ],
  "relationships": [
    {"subject": "Entity Name", "predicate": "verb e.g. acquired|leads|involves", "object": "Entity Name"}
  ],
  "summary": "2-3 sentence summary of the text"
}

Rules:
- Return ONLY valid JSON — no markdown fences, no commentary.
- Normalize entity names (e.g. "President Biden" and "Joe Biden" → one entry).
- Omit empty arrays (use [] not null).
- Extract implicit relationships where clearly implied.
"""


# ---------------------------------------------------------------------------
# Node 1: extract_entities
# ---------------------------------------------------------------------------

async def extract_entities(
    state: MemoryWriteState, config: RunnableConfig
) -> dict[str, Any]:
    """LLM extraction with self-correcting retry via conditional edge."""
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    executor.check_cancelled(task_id)

    retry_count = state.get("retry_count", 0)

    if retry_count >= MAX_RETRIES:
        logger.warning("[%s] extract_entities: retries exhausted, returning empty extraction", task_id)
        return {
            "extracted": {"entities": [], "relationships": [], "summary": ""},
            "retry_count": retry_count,
        }

    client = _get_openai_client()
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    messages: list[dict] = [{"role": "system", "content": _EXTRACTION_SYSTEM_PROMPT}]

    if retry_count > 0 and state.get("last_raw"):
        corrective = (
            f"Your previous response failed to parse as valid JSON.\n"
            f"Parse error: {state['last_error']}\n"
            f"Your response was:\n{state['last_raw']}\n\n"
            f"Please correct and return ONLY valid JSON matching the schema above.\n\n"
            f"Text to extract from:\n"
        )
        messages.append({"role": "user", "content": corrective + state["input"]})
    else:
        messages.append({"role": "user", "content": f"Text:\n{state['input']}"})

    raw = ""
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.1,
            max_completion_tokens=4096,
            timeout=60,
        )
        raw = response.choices[0].message.content or ""
        parsed = json.loads(raw)
        logger.info(
            "[%s] extract_entities: extracted %d entities, %d relationships",
            task_id,
            len(parsed.get("entities", [])),
            len(parsed.get("relationships", [])),
        )
        return {"extracted": parsed, "retry_count": retry_count, "last_error": ""}

    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("[%s] extract_entities: parse error attempt %d — %s", task_id, retry_count + 1, e)
        return {
            "extracted": None,
            "retry_count": retry_count + 1,
            "last_raw": raw,
            "last_error": str(e),
        }
```

- [ ] **Step 2.4: Run extract tests — expect PASS**

```bash
pytest tests/test_memory_agent.py::test_extract_entities_happy_path tests/test_memory_agent.py::test_extract_entities_retry_on_bad_json tests/test_memory_agent.py::test_extract_entities_retry_injects_error_context tests/test_memory_agent.py::test_extract_entities_exhausted_returns_empty -v
```

Expected: 4 passed.

- [ ] **Step 2.5: Commit**

```bash
git add agents/memory_agent/graph.py tests/test_memory_agent.py
git commit -m "feat(memory-agent): add extract_entities node with self-correcting retry"
```

---

## Task 3: `graph.py` — `store_memories` node + compiled graph

**Files:**
- Modify: `agents/memory_agent/graph.py` (append store node + graph builder)
- Test: `tests/test_memory_agent.py` (append)

- [ ] **Step 3.1: Append failing tests for the store node and full graph**

Append to `tests/test_memory_agent.py`:

```python
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
            },
            config={"configurable": {"executor": executor, "task_id": "t1", "context_id": "c1"}},
        )

    assert result["stored"] is True
    assert result["entities_added"] == 2
    assert result["relationships_added"] == 1


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
            },
            config={"configurable": {"executor": executor, "task_id": "t2", "context_id": "c2"}},
        )

    assert call_count == 2
    assert result["stored"] is True


@pytest.mark.asyncio
async def test_write_graph_cancellation():
    """check_cancelled() is called in each node — raises CancelledError immediately."""
    import asyncio
    from agents.memory_agent.graph import extract_entities, store_memories
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
    }
    with pytest.raises(asyncio.CancelledError):
        await extract_entities(state, config)

    state2 = {**state, "extracted": SAMPLE_EXTRACTION}
    with pytest.raises(asyncio.CancelledError):
        await store_memories(state2, config)
```

- [ ] **Step 3.2: Run — expect FAIL**

```bash
pytest tests/test_memory_agent.py::test_store_memories_happy_path tests/test_memory_agent.py::test_write_graph_full_pipeline -v
```

Expected: `ImportError` — `store_memories` and `build_memory_write_graph` not yet defined.

- [ ] **Step 3.3: Append `store_memories` node and graph builder to `graph.py`**

Add the following imports at the top of `graph.py` (after the existing imports):

```python
from agents.memory_agent.stores import embed_text, get_neo4j_graph, get_pgvector_pool
```

Then append to `graph.py`:

```python
# ---------------------------------------------------------------------------
# Routing helper
# ---------------------------------------------------------------------------

def _route_after_extract(state: MemoryWriteState) -> str:
    if state.get("extracted") is not None:
        return "store_memories"
    if state.get("retry_count", 0) >= MAX_RETRIES:
        return "store_memories"
    return "extract_entities"


# ---------------------------------------------------------------------------
# Node 2: store_memories
# ---------------------------------------------------------------------------

async def store_memories(
    state: MemoryWriteState, config: RunnableConfig
) -> dict[str, Any]:
    """Write extracted entities and relationships to pgvector and Neo4j."""
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    executor.check_cancelled(task_id)

    extracted = state.get("extracted") or {"entities": [], "relationships": [], "summary": ""}
    namespace = state.get("namespace", "")

    pool = await get_pgvector_pool()
    neo4j = get_neo4j_graph()

    entities_added = 0
    relationships_added = 0
    any_stored = False

    # Store each entity in pgvector
    for entity in extracted.get("entities", []):
        executor.check_cancelled(task_id)
        name = entity.get("name", "")
        if not name:
            continue
        attrs = entity.get("attributes", {})
        content = f"{entity.get('type', 'entity').capitalize()}: {name}"
        if attrs:
            content += ", " + ", ".join(f"{k}: {v}" for k, v in attrs.items())
        metadata = {
            "entities": [name],
            "namespace": namespace,
            "source": "write",
        }
        try:
            embedding = await embed_text(content)
            embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"
            async with pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO memories (namespace, content, embedding, metadata) "
                    "VALUES ($1, $2, $3::vector, $4::jsonb)",
                    namespace, content, embedding_str, json.dumps(metadata),
                )
            entities_added += 1
            any_stored = True
        except Exception as e:
            logger.warning("[%s] store_memories: failed to store entity '%s' — %s", task_id, name, e)

    # Store each entity as a Neo4j node
    for entity in extracted.get("entities", []):
        name = entity.get("name", "")
        if not name:
            continue
        try:
            await asyncio.to_thread(
                neo4j.query,
                "MERGE (e:Entity {name: $name, namespace: $ns}) "
                "ON CREATE SET e.type = $type",
                {"name": name, "ns": namespace, "type": entity.get("type", "unknown")},
            )
        except Exception as e:
            logger.warning("[%s] store_memories: Neo4j entity merge failed '%s' — %s", task_id, name, e)

    # Store each relationship in Neo4j
    for rel in extracted.get("relationships", []):
        executor.check_cancelled(task_id)
        subj = rel.get("subject", "")
        pred = rel.get("predicate", "")
        obj = rel.get("object", "")
        if not all([subj, pred, obj]):
            continue
        try:
            await asyncio.to_thread(
                neo4j.query,
                "MATCH (a:Entity {name: $subj, namespace: $ns}) "
                "MATCH (b:Entity {name: $obj, namespace: $ns}) "
                "MERGE (a)-[:RELATES {predicate: $pred, namespace: $ns}]->(b)",
                {"subj": subj, "obj": obj, "pred": pred, "ns": namespace},
            )
            relationships_added += 1
            any_stored = True
        except Exception as e:
            logger.warning("[%s] store_memories: Neo4j relationship failed '%s %s %s' — %s", task_id, subj, pred, obj, e)

    # Store summary in pgvector
    summary = extracted.get("summary", "")
    if summary:
        entity_names = [e.get("name", "") for e in extracted.get("entities", []) if e.get("name")]
        metadata = {"entities": entity_names, "namespace": namespace, "source": "write"}
        try:
            embedding = await embed_text(summary)
            embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"
            async with pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO memories (namespace, content, embedding, metadata) "
                    "VALUES ($1, $2, $3::vector, $4::jsonb)",
                    namespace, summary, embedding_str, json.dumps(metadata),
                )
            any_stored = True
        except Exception as e:
            logger.warning("[%s] store_memories: failed to store summary — %s", task_id, e)

    logger.info(
        "[%s] store_memories: complete — %d entities, %d relationships, stored=%s",
        task_id, entities_added, relationships_added, any_stored,
    )
    return {
        "stored": any_stored,
        "entities_added": entities_added,
        "relationships_added": relationships_added,
    }


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_memory_write_graph() -> CompiledStateGraph:
    graph = StateGraph(MemoryWriteState)
    graph.add_node("extract_entities", extract_entities)
    graph.add_node("store_memories", store_memories)
    graph.set_entry_point("extract_entities")
    graph.add_conditional_edges(
        "extract_entities",
        _route_after_extract,
        {
            "extract_entities": "extract_entities",
            "store_memories": "store_memories",
        },
    )
    graph.add_edge("store_memories", END)
    return graph.compile()
```

Also add `import asyncio` and `import json` at the top of `graph.py` if not already present (they should be after Task 2's implementation).

- [ ] **Step 3.4: Run all graph tests — expect PASS**

```bash
pytest tests/test_memory_agent.py -k "extract or store or write_graph" -v
```

Expected: All graph-related tests pass.

- [ ] **Step 3.5: Commit**

```bash
git add agents/memory_agent/graph.py tests/test_memory_agent.py
git commit -m "feat(memory-agent): add store_memories node and compile WriteGraph"
```

---

## Task 4: `executor.py` — `_search_memories` and `_traverse_graph` helpers only

**Files:**
- Create: `agents/memory_agent/executor.py` (helpers only — `MemoryAgentExecutor` added in Task 5)
- Test: `tests/test_memory_agent.py` (append)

- [ ] **Step 4.1: Append failing tests for search and traverse**

Append to `tests/test_memory_agent.py`:

```python
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
```

- [ ] **Step 4.2: Run — expect FAIL**

```bash
pytest tests/test_memory_agent.py::test_search_memories_returns_ranked_results -v
```

Expected: `ModuleNotFoundError: No module named 'agents.memory_agent.executor'`

- [ ] **Step 4.3: Create `executor.py` with ONLY the search and traverse helpers (no `MemoryAgentExecutor` yet)**

```python
"""Executor for the memory agent, with multi-skill dispatch."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from agents.base.executor import LangGraphA2AExecutor
from agents.memory_agent.stores import embed_text, get_neo4j_graph, get_pgvector_pool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Direct async helpers (no LangGraph)
# ---------------------------------------------------------------------------

async def _search_memories(
    executor: LangGraphA2AExecutor,
    task_id: str,
    input_json: dict[str, Any],
) -> dict[str, Any]:
    """Semantic search over the memories table."""
    query = input_json.get("query", "")
    namespace = input_json.get("namespace", "")
    limit = int(input_json.get("limit", 5))

    executor.check_cancelled(task_id)
    query_vec = await embed_text(query)
    executor.check_cancelled(task_id)

    vec_str = "[" + ",".join(str(x) for x in query_vec) + "]"
    pool = await get_pgvector_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT content, metadata, "
            "1 - (embedding <=> $1::vector) AS score "
            "FROM memories "
            "WHERE namespace = $2 "
            "ORDER BY embedding <=> $1::vector "
            "LIMIT $3",
            vec_str, namespace, limit,
        )

    results = []
    for row in rows:
        raw_meta = row["metadata"]
        metadata = json.loads(raw_meta) if isinstance(raw_meta, str) else raw_meta
        results.append({
            "content": row["content"],
            "score": float(row["score"]),
            "metadata": metadata,
        })
    return {"results": results}


async def _traverse_graph(
    executor: LangGraphA2AExecutor,
    task_id: str,
    input_json: dict[str, Any],
) -> dict[str, Any]:
    """Graph traversal from a named entity up to a given depth."""
    entity = input_json.get("entity", "")
    namespace = input_json.get("namespace", "")
    depth = int(input_json.get("depth", 2))

    executor.check_cancelled(task_id)

    neo4j = get_neo4j_graph()
    cypher = (
        "MATCH (n:Entity {name: $entity, namespace: $ns})"
        f"-[r*1..{depth}]-(m:Entity {{namespace: $ns}}) "
        "RETURN n, r, m"
    )
    rows = await asyncio.to_thread(
        neo4j.query, cypher, {"entity": entity, "ns": namespace}
    )

    nodes: list[dict] = []
    edges: list[dict] = []
    seen_nodes: set[str] = set()

    for row in rows:
        for node_key in ("n", "m"):
            n = row.get(node_key, {})
            name = n.get("name", "")
            if name and name not in seen_nodes:
                seen_nodes.add(name)
                nodes.append({
                    "name": name,
                    "type": n.get("type", "unknown"),
                    "namespace": n.get("namespace", namespace),
                })
        for rel in (row.get("r") or []):
            if isinstance(rel, dict):
                edges.append({
                    "subject": row.get("n", {}).get("name", ""),
                    "predicate": rel.get("predicate", ""),
                    "object": row.get("m", {}).get("name", ""),
                    "namespace": rel.get("namespace", namespace),
                })

    return {"nodes": nodes, "edges": edges}
```

**Note:** `MemoryAgentExecutor` is NOT included here yet — it will be added in Task 5 after its tests are written first.

- [ ] **Step 4.4: Run search and traverse tests — expect PASS**

```bash
pytest tests/test_memory_agent.py -k "search or traverse" -v
```

Expected: All search/traverse tests pass.

- [ ] **Step 4.5: Commit**

```bash
git add agents/memory_agent/executor.py tests/test_memory_agent.py
git commit -m "feat(memory-agent): add executor with search and traverse helpers"
```

---

## Task 5: `executor.py` — `MemoryAgentExecutor` (TDD)

**Files:**
- Modify: `agents/memory_agent/executor.py` (append `MemoryAgentExecutor` class)
- Test: `tests/test_memory_agent.py` (append — tests written BEFORE the class is implemented)

- [ ] **Step 5.1: Append failing dispatch tests (RED — MemoryAgentExecutor does not exist yet)**

Append to `tests/test_memory_agent.py`:

```python
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
    mock_graph = AsyncMock()
    mock_graph.astream = MagicMock(return_value=_async_gen([
        {"extract_entities": {"extracted": SAMPLE_EXTRACTION, "retry_count": 0}},
        {"store_memories": {"stored": True, "entities_added": 2, "relationships_added": 1}},
    ]))

    with patch.object(type(executor), "graph", new_callable=lambda: property(lambda self: mock_graph)):
        await executor.execute(context, CollectingQueue())

    final_event = emitted[-1]
    assert final_event.status.state == TaskState.completed
    output = json.loads(final_event.status.message.parts[0].root.text)
    assert output["stored"] is True
    assert output["entities_added"] == 2
    assert output["relationships_added"] == 1
```

Also add this helper at the top of the test file (after the imports), needed by the write dispatch test:

```python
async def _async_gen(items):
    """Yield items from a list as an async generator (for mocking astream)."""
    for item in items:
        yield item
```

- [ ] **Step 5.2: Run — expect FAIL (`ImportError: cannot import name 'MemoryAgentExecutor'`)**

```bash
pytest tests/test_memory_agent.py::test_executor_unknown_operation_emits_failed -v
```

Expected: `ImportError` — `MemoryAgentExecutor` not defined yet.

- [ ] **Step 5.3: Append `MemoryAgentExecutor` to `executor.py`**

Append to `agents/memory_agent/executor.py` (add necessary imports at top of file first):

Add these imports at the top of `executor.py`:

```python
import uuid
from typing import Any

from a2a.server.agent_execution import RequestContext
from a2a.server.events import EventQueue
from a2a.types import (
    Message,
    Part,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)
from langgraph.graph.state import CompiledStateGraph

from agents.memory_agent.graph import build_memory_write_graph
```

Then append the class to the bottom of `executor.py`:

```python
# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------

class MemoryAgentExecutor(LangGraphA2AExecutor):
    """A2A executor for the memory agent — dispatches write/search/traverse."""

    def build_graph(self) -> CompiledStateGraph:
        return build_memory_write_graph()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        # Replicate base class task ID derivation exactly (mirrors agents/base/executor.py)
        cp_task_id = None
        if context.message and context.message.metadata:
            cp_task_id = context.message.metadata.get("controlPlaneTaskId")
        task_id = cp_task_id or context.task_id or str(uuid.uuid4())
        context_id = context.context_id or str(uuid.uuid4())
        self.register_task(task_id)

        parent_span_id = None
        if context.message and context.message.metadata:
            parent_span_id = context.message.metadata.get("parentSpanId")

        from agents.base.tracing import build_langfuse_handler
        langfuse_handler, langfuse_client = build_langfuse_handler(context_id, parent_span_id)
        callbacks = [langfuse_handler] if langfuse_handler else []

        try:
            await self._emit_status(event_queue, task_id, context_id, TaskState.working, "Processing…")

            raw = context.get_user_input() or ""
            try:
                input_json = json.loads(raw)
            except json.JSONDecodeError:
                await self._emit_status(
                    event_queue, task_id, context_id, TaskState.failed,
                    "Input must be a JSON object with an 'operation' field.", final=True,
                )
                return

            operation = input_json.get("operation", "")

            if operation == "write":
                graph_input = {
                    "input": input_json.get("text", ""),
                    "namespace": input_json.get("namespace", ""),
                    "extracted": None,
                    "retry_count": 0,
                    "last_raw": "",
                    "last_error": "",
                    "stored": False,
                    "entities_added": 0,
                    "relationships_added": 0,
                }
                result: dict[str, Any] = {}
                async for event in self.graph.astream(
                    graph_input,
                    config={
                        "configurable": {
                            "executor": self,
                            "task_id": task_id,
                            "context_id": context_id,
                        },
                        "callbacks": callbacks,
                    },
                    stream_mode="updates",
                ):
                    self.check_cancelled(task_id)
                    node_name = next(iter(event))
                    await self._emit_status(
                        event_queue, task_id, context_id,
                        TaskState.working, f"Running node: {node_name}",
                    )
                    update = event[node_name]
                    if update:
                        result.update(update)
                    await self._emit_status(
                        event_queue, task_id, context_id, TaskState.working,
                        f"NODE_OUTPUT::{node_name}::{json.dumps(update or {})}",
                    )
                output_text = json.dumps({
                    "stored": result.get("stored", False),
                    "namespace": input_json.get("namespace", ""),
                    "entities_added": result.get("entities_added", 0),
                    "relationships_added": result.get("relationships_added", 0),
                })

            elif operation == "search":
                search_result = await _search_memories(self, task_id, input_json)
                output_text = json.dumps(search_result)

            elif operation == "traverse":
                traverse_result = await _traverse_graph(self, task_id, input_json)
                output_text = json.dumps(traverse_result)

            else:
                await self._emit_status(
                    event_queue, task_id, context_id, TaskState.failed,
                    f"Unknown operation: '{operation}'. Valid values: write, search, traverse",
                    final=True,
                )
                return

            final_msg = Message(
                kind="message",
                role="agent",
                message_id=str(uuid.uuid4()),
                task_id=task_id,
                context_id=context_id,
                parts=[Part(root=TextPart(text=output_text))],
            )
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=task_id,
                    context_id=context_id,
                    status=TaskStatus(state=TaskState.completed, message=final_msg),
                    final=True,
                )
            )

        except asyncio.CancelledError:
            await self._emit_status(
                event_queue, task_id, context_id, TaskState.canceled,
                "Task was cancelled.", final=True,
            )
        except Exception as exc:
            import traceback
            tb = traceback.format_exc()
            await self._emit_status(
                event_queue, task_id, context_id, TaskState.failed,
                f"{type(exc).__name__}: {exc}\n\n{tb}", final=True,
            )
        finally:
            if langfuse_client:
                await asyncio.to_thread(langfuse_client.flush)
            self.cleanup_task(task_id)
```

- [ ] **Step 5.4: Run all executor dispatch tests — expect PASS**

```bash
pytest tests/test_memory_agent.py -k "executor" -v
```

Expected: 3 passed (`unknown_operation`, `invalid_json`, `write_dispatches_graph`).

- [ ] **Step 5.5: Run full test suite**

```bash
pytest tests/test_memory_agent.py -v
```

Expected: All tests pass.

- [ ] **Step 5.6: Commit**

```bash
git add agents/memory_agent/executor.py tests/test_memory_agent.py
git commit -m "feat(memory-agent): add MemoryAgentExecutor with write/search/traverse dispatch"
```

- [ ] **Step 5.3: Run the full test suite for the memory agent**

```bash
pytest tests/test_memory_agent.py -v
```

Expected: All tests pass. Verify you see tests for: stores env validation (5), extract node (4), store node (3), write graph (2 integration + 1 retry + 1 cancellation), search (3), traverse (2), executor dispatch (2) = ~20 tests.

- [ ] **Step 5.4: Commit**

```bash
git add tests/test_memory_agent.py
git commit -m "feat(memory-agent): add executor dispatch tests"
```

---

## Task 6: `server.py` — A2A app wiring

**Files:**
- Create: `agents/memory_agent/server.py`

No new tests needed — server.py is pure wiring with no logic.

- [ ] **Step 6.1: Create `server.py`**

```python
"""Standalone A2A server for the Memory Agent.

Run with:
    python -m agents.memory_agent.server

Environment variables (all required unless noted):
    MEMORY_NEO4J_URL         – Neo4j bolt URL
    MEMORY_NEO4J_USER        – Neo4j username
    MEMORY_NEO4J_PASSWORD    – Neo4j password
    MEMORY_PG_DSN            – pgvector-enabled PostgreSQL DSN
    MEMORY_EMBEDDING_MODEL   – Embedding model name (e.g. text-embedding-3-small)
    MEMORY_EMBEDDING_DIMS    – Vector dimensions matching the embedding model
    OPENAI_API_KEY           – Required for LLM extraction and embeddings
    OPENAI_BASE_URL          – Optional. Custom OpenAI-compatible base URL
    OPENAI_MODEL             – Optional. LLM model (default: gpt-4o-mini)
    CONTROL_PLANE_URL        – Optional. Control plane URL for self-registration
    MEMORY_AGENT_URL         – Optional. This agent's externally-reachable URL
"""
from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager

import uvicorn
from a2a.server.apps.jsonrpc import A2AFastAPIApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from dotenv import load_dotenv
from fastapi import FastAPI

from agents.base.registration import deregister_from_control_plane, register_with_control_plane
from agents.memory_agent.executor import MemoryAgentExecutor

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)

AGENT_TYPE = "memory-agent"
AGENT_PORT = 8009

INPUT_FIELDS = [
    {
        "name": "operation",
        "label": "Operation",
        "type": "select",
        "options": ["write", "search", "traverse"],
        "required": True,
    },
    {
        "name": "namespace",
        "label": "Namespace",
        "type": "text",
        "required": True,
        "placeholder": "e.g. lead_analyst",
    },
    {
        "name": "text",
        "label": "Text (write)",
        "type": "textarea",
        "required": False,
        "placeholder": "Raw text to ingest (write operation)",
    },
    {
        "name": "query",
        "label": "Query (search)",
        "type": "text",
        "required": False,
        "placeholder": "Semantic search query",
    },
    {
        "name": "entity",
        "label": "Entity (traverse)",
        "type": "text",
        "required": False,
        "placeholder": "Entity name to traverse from",
    },
    {
        "name": "limit",
        "label": "Limit (search, default 5)",
        "type": "number",
        "required": False,
    },
    {
        "name": "depth",
        "label": "Depth (traverse, default 2)",
        "type": "number",
        "required": False,
    },
]

agent_card = AgentCard(
    name="Memory Agent",
    description=(
        "Dual-store memory agent (pgvector + Neo4j). Supports three operations: "
        "write (raw text → LLM extraction → persisted memories), "
        "search (semantic vector search by namespace), and "
        "traverse (graph walk from a named entity)."
    ),
    version="0.1.0",
    url=f"http://localhost:{AGENT_PORT}",
    capabilities=AgentCapabilities(streaming=True, push_notifications=False),
    default_input_modes=["application/json"],
    default_output_modes=["application/json"],
    skills=[
        AgentSkill(
            id="memory/write",
            name="Write Memory",
            description="Ingest raw text, extract entities and relationships, store in pgvector + Neo4j",
            tags=["memory", "write", "neo4j", "pgvector"],
        ),
        AgentSkill(
            id="memory/search",
            name="Search Memory",
            description="Semantic search over stored memories within a namespace",
            tags=["memory", "search", "pgvector"],
        ),
        AgentSkill(
            id="memory/traverse",
            name="Traverse Graph",
            description="Graph traversal from a named entity within a namespace",
            tags=["memory", "traverse", "neo4j"],
        ),
    ],
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    agent_url = os.getenv(
        "MEMORY_AGENT_URL",
        os.getenv("AGENT_URL", f"http://localhost:{AGENT_PORT}"),
    )
    await register_with_control_plane(AGENT_TYPE, agent_url)
    yield
    await deregister_from_control_plane(AGENT_TYPE, agent_url)


def create_app() -> FastAPI:
    app = FastAPI(title="Memory Agent A2A Server", lifespan=lifespan)
    agent_url = os.getenv(
        "MEMORY_AGENT_URL",
        os.getenv("AGENT_URL", f"http://localhost:{AGENT_PORT}"),
    )

    executor = MemoryAgentExecutor()
    task_store = InMemoryTaskStore()
    request_handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=task_store,
    )
    a2a_app = A2AFastAPIApplication(agent_card=agent_card, http_handler=request_handler)
    a2a_app.add_routes_to_app(app)

    @app.get("/graph")
    async def get_graph():
        topology = executor.get_graph_topology()
        topology["input_fields"] = INPUT_FIELDS
        return topology

    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=AGENT_PORT, timeout_graceful_shutdown=15)
```

- [ ] **Step 6.2: Verify imports work**

```bash
python -c "from agents.memory_agent.server import create_app; print('OK')"
```

Expected: `OK`

- [ ] **Step 6.3: Commit**

```bash
git add agents/memory_agent/server.py
git commit -m "feat(memory-agent): add A2A server with 3 skills on port 8009"
```

---

## Task 7: Deployment files

**Files:**
- Create: `Dockerfile.memory-agent`
- Modify: `docker-compose.yml`
- Modify: `run-local.sh`
- Modify: `CLAUDE.md`

- [ ] **Step 7.1: Create `Dockerfile.memory-agent`**

```dockerfile
FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agents/ agents/
COPY control_plane/ control_plane/

EXPOSE 8009
CMD ["python", "-m", "agents.memory_agent.server"]
```

- [ ] **Step 7.2: Add memory-agent service to `docker-compose.yml`**

Find the closing `# ── React Dashboard` comment in `docker-compose.yml`. Insert the new service block immediately before it:

```yaml
  # ── Memory Agent ─────────────────────────────────────────────────────────
  memory-agent:
    build:
      context: .
      dockerfile: Dockerfile.memory-agent
    ports:
      - "8009:8009"
    env_file: ".env"
    environment:
      - LOG_LEVEL=INFO
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - OPENAI_MODEL=${OPENAI_MODEL:-gpt-4o-mini}
      - OPENAI_BASE_URL=${OPENAI_BASE_URL:-https://openrouter.ai/api/v1}
      - CONTROL_PLANE_URL=http://control-plane:8000
      - MEMORY_AGENT_URL=http://memory-agent:8009
      - MEMORY_NEO4J_URL=bolt://neo4j:7687
      - MEMORY_NEO4J_USER=neo4j
      - MEMORY_NEO4J_PASSWORD=mc_password
      - MEMORY_PG_DSN=postgresql://mc:mc_password@postgres:5432/missioncontrol
      - MEMORY_EMBEDDING_MODEL=${MEMORY_EMBEDDING_MODEL}
      - MEMORY_EMBEDDING_DIMS=${MEMORY_EMBEDDING_DIMS}
    networks:
      - mc-net
    depends_on:
      control-plane:
        condition: service_healthy
      neo4j:
        condition: service_healthy
      postgres:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "python", "-c", "import httpx; httpx.get('http://localhost:8009/.well-known/agent-card.json').raise_for_status()"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 15s
```

- [ ] **Step 7.3: Update `run-local.sh`**

Change the existing `[10/10] Starting Dashboard` line to `[11/11]` and insert a new step 10 before it:

```bash
# ── Memory Agent ────────────────────────────────────────────────────────────
MEMORY_PORT=8009

echo "[10/11] Starting Memory Agent on port $MEMORY_PORT..."
MEMORY_NEO4J_URL="${MEMORY_NEO4J_URL:-bolt://localhost:7687}" \
MEMORY_NEO4J_USER="${MEMORY_NEO4J_USER:-neo4j}" \
MEMORY_NEO4J_PASSWORD="${MEMORY_NEO4J_PASSWORD:-mc_password}" \
MEMORY_PG_DSN="${MEMORY_PG_DSN:-postgresql://mc:mc_password@localhost:5432/missioncontrol}" \
MEMORY_EMBEDDING_MODEL="${MEMORY_EMBEDDING_MODEL}" \
MEMORY_EMBEDDING_DIMS="${MEMORY_EMBEDDING_DIMS}" \
CONTROL_PLANE_URL="$CP_URL" \
MEMORY_AGENT_URL="http://127.0.0.1:$MEMORY_PORT" \
  python -m agents.memory_agent.server &
PIDS+=($!)
wait_for_port $MEMORY_PORT "Memory Agent"
```

Also update the summary block at the bottom:

```bash
echo "  Memory Agent:   http://localhost:$MEMORY_PORT"
```

And update `[10/10] Starting Dashboard` → `[11/11] Starting Dashboard`.

- [ ] **Step 7.4: Update `CLAUDE.md`**

In the agents table, add a new row:

```
| Memory Agent (`agents/memory_agent/`) | 8009 | `memory-agent` | Dual-store memory agent: write (raw text → LLM extraction → pgvector + Neo4j), search (semantic), and traverse (graph walk). No mem0 dependency. |
```

In the "Per-Agent URL Variables" table, add:

```
| `MEMORY_AGENT_URL` | Memory Agent | `http://localhost:8009` |
```

In the "Shared Agent Variables" table (or a new "Memory Agent Variables" section), add:

```
| `MEMORY_NEO4J_URL` | — | Neo4j bolt URL (memory agent only, required) |
| `MEMORY_NEO4J_USER` | — | Neo4j username (memory agent only, required) |
| `MEMORY_NEO4J_PASSWORD` | — | Neo4j password (memory agent only, required) |
| `MEMORY_PG_DSN` | — | pgvector Postgres DSN (memory agent only, required) |
| `MEMORY_EMBEDDING_MODEL` | — | Embedding model name (memory agent only, required) |
| `MEMORY_EMBEDDING_DIMS` | — | Vector dims — must match model, no default (memory agent only, required) |
```

- [ ] **Step 7.5: Commit**

```bash
git add Dockerfile.memory-agent docker-compose.yml run-local.sh CLAUDE.md
git commit -m "feat(memory-agent): add Dockerfile, docker-compose service, run-local entry, CLAUDE.md docs"
```

---

## Task 8: `README.md`

**Files:**
- Create: `agents/memory_agent/README.md`

- [ ] **Step 8.1: Create README**

```markdown
# Memory Agent

Dual-store memory agent for Mission Control. Accepts raw text, extracts entities
and relationships via LLM, and persists them to both pgvector (semantic search)
and Neo4j (graph traversal). Does not use mem0 — drives `asyncpg` and
`langchain_neo4j` directly.

**Port:** 8009 | **Agent type:** `memory-agent`

## Skills

All three skills share the same A2A endpoint. Route by including `"operation"` in the JSON input.

### `memory/write`

Ingest raw text into both backends.

```json
{
  "operation": "write",
  "text": "Apple acquired Beats Electronics in 2014 for $3 billion.",
  "namespace": "lead_analyst"
}
```

Response:
```json
{ "stored": true, "namespace": "lead_analyst", "entities_added": 2, "relationships_added": 1 }
```

> ⚠️ `stored: false` is returned as a *completed* task (not failed). Callers must check this field.

### `memory/search`

Semantic search over stored memories.

```json
{ "operation": "search", "query": "Apple acquisitions", "namespace": "lead_analyst", "limit": 5 }
```

Response:
```json
{
  "results": [
    {
      "content": "Organization: Apple, sector: technology",
      "score": 0.94,
      "metadata": { "entities": ["Apple"], "namespace": "lead_analyst", "source": "write" }
    }
  ]
}
```

### `memory/traverse`

Graph traversal from a named entity.

```json
{ "operation": "traverse", "entity": "Apple", "namespace": "lead_analyst", "depth": 2 }
```

Response:
```json
{
  "nodes": [
    { "name": "Apple", "type": "organization", "namespace": "lead_analyst" },
    { "name": "Beats", "type": "organization", "namespace": "lead_analyst" }
  ],
  "edges": [
    { "subject": "Apple", "predicate": "acquired", "object": "Beats", "namespace": "lead_analyst" }
  ]
}
```

## Environment Variables

| Variable | Description |
|---|---|
| `MEMORY_NEO4J_URL` | Neo4j bolt URL (required) |
| `MEMORY_NEO4J_USER` | Neo4j username (required) |
| `MEMORY_NEO4J_PASSWORD` | Neo4j password (required) |
| `MEMORY_PG_DSN` | pgvector PostgreSQL DSN (required) |
| `MEMORY_EMBEDDING_MODEL` | Embedding model name (required) |
| `MEMORY_EMBEDDING_DIMS` | Vector dimensions — no default, must match model (required) |
| `OPENAI_API_KEY` | Required for LLM extraction and embeddings |
| `OPENAI_BASE_URL` | Optional. Custom OpenAI-compatible base URL |
| `OPENAI_MODEL` | LLM model for extraction (default: `gpt-4o-mini`) |
| `MEMORY_AGENT_URL` | This agent's externally-reachable URL (default: `http://localhost:8009`) |

> **`MEMORY_EMBEDDING_DIMS` has no safe default.** Set it to match your embedding model:
> - `text-embedding-3-small` → `1536`
> - `jina-embeddings-v5-text-small` → `1024`

## Running Locally

```bash
MEMORY_NEO4J_URL=bolt://localhost:7687 \
MEMORY_NEO4J_USER=neo4j \
MEMORY_NEO4J_PASSWORD=mc_password \
MEMORY_PG_DSN=postgresql://mc:mc_password@localhost:5432/missioncontrol \
MEMORY_EMBEDDING_MODEL=text-embedding-3-small \
MEMORY_EMBEDDING_DIMS=1536 \
OPENAI_API_KEY=sk-... \
python -m agents.memory_agent.server
```

## Graph Topology

The `/graph` endpoint returns the WriteGraph topology (used by the dashboard):

```
extract_entities → store_memories
```

Search and traverse are direct function calls with no graph nodes.

## Storage Schema

**pgvector** (`memories` table):
- One row per entity with content = `"Type: Name, attr: val"`
- One row per ingested text summary
- All rows include `namespace`, `metadata` (with entity names), and an hnsw-indexed embedding vector

**Neo4j**:
- `:Entity { name, type, namespace }` nodes
- `(a)-[:RELATES { predicate, namespace }]->(b)` relationships
```

- [ ] **Step 8.2: Run full test suite to confirm nothing broken**

```bash
pytest tests/test_memory_agent.py -v
```

Expected: All tests pass.

- [ ] **Step 8.3: Commit**

```bash
git add agents/memory_agent/README.md
git commit -m "docs(memory-agent): add README with skills, env vars, and storage schema"
```
