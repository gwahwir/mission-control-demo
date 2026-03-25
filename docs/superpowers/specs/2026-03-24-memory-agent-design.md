# Memory Agent Design

**Date:** 2026-03-24
**Status:** Approved
**Port:** 8009
**Agent type ID:** `memory-agent`

## Overview

A general-purpose dual-store memory agent that any other agent in the Mission Control system can call to write and retrieve memories. Memories are namespaced by a caller-supplied string, stored in two backends simultaneously — pgvector for semantic search and Neo4j for graph traversal — and written via LLM extraction from raw text.

This agent does **not** use mem0. It drives `asyncpg` (pgvector) and `langchain_neo4j` (Neo4j) directly, giving full control over schema, query logic, and what gets stored.

---

## Architecture

```
agents/memory_agent/
├── graph.py       # WriteGraph: extract → resolve_conflicts → store (LangGraph, 3 nodes + retry)
├── stores.py      # Singleton clients for pgvector (asyncpg), Neo4j, and embedder
├── executor.py    # Overrides execute() to dispatch write/search/traverse
├── server.py      # A2A FastAPI server, port 8009, 3 skills
└── README.md
```

Infrastructure reuses the existing `postgres` (pgvector) and `neo4j` Docker Compose services — no new containers required.

---

## Skills

Three A2A skills declared on the AgentCard. Callers route to the correct skill by including an `operation` field in the JSON input body alongside the skill-specific parameters.

| Skill ID | `operation` value | Key Input Fields | Output |
|---|---|---|---|
| `memory/write` | `"write"` | `text: str`, `namespace: str` | `{ stored: bool, namespace: str, entities_added: int, relationships_added: int, memories_updated: int, memories_deleted: int }` |
| `memory/search` | `"search"` | `query: str`, `namespace: str`, `limit: int` (optional, default 5) | `{ results: [{ content: str, score: float, metadata: { entities: [...], namespace: str, source: str } }] }` |
| `memory/traverse` | `"traverse"` | `entity: str`, `namespace: str`, `depth: int` (optional, default 2) | `{ nodes: [{ name: str, type: str, namespace: str }], edges: [{ subject: str, predicate: str, object: str, namespace: str }] }` |

The `metadata` object in search results is the raw JSONB column value returned verbatim from storage — not a reconstructed object.

Example input body for write:
```json
{ "operation": "write", "text": "Apple acquired Beats in 2014.", "namespace": "lead_analyst" }
```

---

## Executor Dispatch

`MemoryAgentExecutor` subclasses `LangGraphA2AExecutor` and overrides `execute()` entirely. `build_graph()` is implemented and returns the WriteGraph (satisfying the abstract method contract); it is only invoked for `operation: "write"`.

The overridden `execute()` must replicate the base class task ID derivation and lifecycle management before branching on operation, then emit status events for all paths:

```
execute(context, event_queue):
  # Replicate base class task ID + lifecycle (must happen before any branch)
  cp_task_id = context.message.metadata.get("controlPlaneTaskId") if metadata else None
  task_id    = cp_task_id or context.task_id or uuid4()
  context_id = context.context_id or uuid4()
  self.register_task(task_id)     # required for check_cancelled to work
  try:
    emit TaskState.working

    input_json = parse(context.get_user_input())
    operation  = input_json["operation"]

    if operation == "write":
        run WriteGraph via self.graph.astream(...)   ← base class streaming pattern
        emit TaskState.completed with output
    elif operation == "search":
        result = await search_memories(task_id, input_json)
        emit TaskState.completed with result
    elif operation == "traverse":
        result = await traverse_graph(task_id, input_json)
        emit TaskState.completed with result
    else:
        emit TaskState.failed with "Unknown operation: ..."
  except CancelledError:
    emit TaskState.canceled
  except Exception:
    emit TaskState.failed
  finally:
    self.cleanup_task(task_id)   # always clean up
```

`task_id` is passed into `search_memories` and `traverse_graph` so they can call `check_cancelled(task_id)`.

`get_graph_topology()` (called by `GET /graph`) uses `build_graph()` and returns the WriteGraph topology — the 3-node write flow (`extract_entities` → `resolve_conflicts` → `store_memories`). The two direct-call paths (search, traverse) are not LangGraph nodes and are not shown in the dashboard graph; this is acceptable because they are single-step point queries with no branching.

---

## Data Flow

### Write (`operation: "write"`)

The WriteGraph has **3 nodes**: `extract_entities`, `resolve_conflicts`, and `store_memories`. It follows the same self-correcting retry pattern as `knowledge_graph/graph.py`.

```
raw text + namespace
  → [Node: extract_entities]
        calls executor.check_cancelled(task_id) at top (via config["configurable"])
        LLM call → { entities, relationships, summary }
        on JSON parse failure: increment retry_count, set extracted=None
        conditional edge: if extracted is None and retries < 3, loop back to self
        conditional edge: if extracted or retries exhausted, go to resolve_conflicts

  → [Node: resolve_conflicts]
        calls executor.check_cancelled(task_id) at top (via config["configurable"])
        search pgvector for top-K existing memories in namespace (K=10, cosine similarity)
        if no existing memories: skip LLM call, pass extracted through unchanged
        else: batch LLM call with extracted entities + existing memories →
              list of resolutions: { action: "KEEP"|"UPDATE"|"DELETE", id: uuid, new_content: str }
              UPDATE: overwrite content + embedding in pgvector row; update Neo4j node properties
              DELETE: remove pgvector row + Neo4j node; set memories_deleted += 1
              KEEP: no-op for that existing memory
              new entities not matched to existing memories proceed to store_memories unchanged
        on LLM parse failure: log warning, skip conflict resolution, proceed with all extracted as new
        → store_memories

  → [Node: store_memories]
        calls executor.check_cancelled(task_id) at top (via config["configurable"])
        asyncpg INSERT: one row per entity + one row for summary (pgvector)
        langchain_neo4j CREATE/MERGE: nodes + relationships (Neo4j)
        per-item failures: log warning, skip, continue
        → END

  → output state: { stored: bool, namespace: str, entities_added: int, relationships_added: int,
                    memories_updated: int, memories_deleted: int }
```

Each node receives `executor` and `task_id` via `config["configurable"]` (same pattern as `knowledge_graph/graph.py` lines 193-195, 301-302).

`stored` is `true` if at least one item was successfully written to either backend. `stored` is `false` only if both backends rejected all writes. **Important for callers:** `stored: false` is returned as a *completed* task (not failed), because write failures for individual items are partial — the task itself succeeded structurally. Callers that invoke `memory/write` must inspect the `stored` field in the completed output to detect write failures, not just check task state.

### Search (`operation: "search"`)

Direct async function — no LangGraph:
1. `check_cancelled(task_id)`
2. Embed `query` using `get_embedder()`
3. `check_cancelled(task_id)`
4. `SELECT content, metadata, 1 - (embedding <=> $vec) AS score FROM memories WHERE namespace = $ns ORDER BY embedding <=> $vec LIMIT $limit`
5. Return `{ results: [{content, score, metadata}] }` — `metadata` is the raw JSONB column value

### Traverse (`operation: "traverse"`)

Direct async function — no LangGraph:
1. `check_cancelled(task_id)`
2. Run Cypher via `langchain_neo4j`:
   ```cypher
   MATCH (n:Entity {name: $entity, namespace: $ns})-[r*1..$depth]-(m:Entity {namespace: $ns})
   RETURN n, r, m
   ```
3. Flatten to `{ nodes: [{name, type, namespace}], edges: [{subject, predicate, object, namespace}] }`

---

## Storage Design

### pgvector — `memories` table

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS memories (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    namespace   TEXT NOT NULL,
    content     TEXT NOT NULL,
    embedding   vector(1024),
    metadata    JSONB DEFAULT '{}',
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS memories_namespace_idx ON memories (namespace);
CREATE INDEX IF NOT EXISTS memories_embedding_idx ON memories
    USING hnsw (embedding vector_cosine_ops);
```

`hnsw` is used instead of `ivfflat` — it performs well on small-to-medium datasets without requiring a minimum row count (available in pgvector 0.5+, present in the `pgvector/pgvector:pg16-trixie` image already in docker-compose).

**Note on `MEMORY_EMBEDDING_DIMS`:** The column type `vector(N)` is fixed at table creation time. `MEMORY_EMBEDDING_DIMS` must be set to match the actual output dimensions of the embedding model before first run. There is no safe default — different models produce different sizes (e.g., `text-embedding-3-small` produces 1536 by default; Jina v5 small produces 1024). If the wrong value is set, embeddings will be silently rejected or cause schema errors. The `CREATE TABLE IF NOT EXISTS` DDL uses the value of `MEMORY_EMBEDDING_DIMS` at startup.

Table and index are created on agent startup via `asyncpg` if they do not already exist.

`metadata` column content written during the write path:
```json
{ "entities": ["Apple", "Beats"], "namespace": "lead_analyst", "source": "write" }
```

### Neo4j

- Nodes: `:Entity { name: str, type: str, namespace: str }`
- Relationships: `(a:Entity)-[:RELATES { predicate: str, namespace: str }]->(b:Entity)`
- Namespace is a property on every node and edge; all queries filter by `namespace`.
- Both `memory_agent` and `knowledge_graph` write to the same Neo4j instance. `memory_agent` uses `:Entity` nodes; mem0 (used by `knowledge_graph`) uses its own internal label scheme. Coexistence is expected to be safe, but operators should verify no label collisions with the specific mem0 version in `requirements.txt` before pointing both agents at the same instance in production.

### pgvector coexistence with `knowledge_graph`

The `knowledge_graph` agent's mem0 client manages its own tables internally. The `memory_agent` creates a `memories` table — a distinct name that does not conflict with any known mem0-managed table. Both agents can point at the same Postgres database. The `MEMORY_PG_DSN` and `MEM0_PG_DSN` env vars can point at the same or different DSNs.

---

## `stores.py` — Client Singletons

Three module-level singletons initialized lazily on first use:

- `get_pgvector_pool()` — returns an `asyncpg` connection pool; runs DDL on first call; requires `MEMORY_PG_DSN`
- `get_neo4j_graph()` — returns a `langchain_neo4j` `Neo4jGraph` instance; requires `MEMORY_NEO4J_URL`, `MEMORY_NEO4J_USER`, `MEMORY_NEO4J_PASSWORD`
- `get_embedder()` — returns an embedding callable; requires `MEMORY_EMBEDDING_MODEL` and `OPENAI_API_KEY`

All three raise `EnvironmentError` on first use if required env vars are missing. All are thin wrappers to allow easy mocking in tests.

---

## Error Handling

| Scenario | Behavior |
|---|---|
| LLM extraction returns invalid JSON | Retry up to 3 times with error context injected into prompt (conditional edge loop) |
| Conflict resolution LLM returns invalid JSON | Log warning, skip conflict resolution entirely, proceed with all extracted as new inserts |
| Missing required env vars | `EnvironmentError` on first store access; agent fails to start |
| Single entity fails to write to Neo4j | Log warning, skip, continue |
| Single embedding fails to insert into pgvector | Log warning, skip, continue |
| Both backends reject all writes | `stored: false` in output; task still completes (callers must check `stored`) |
| Unknown `operation` value | Emit `TaskState.failed` with descriptive message |
| Task cancelled mid-write | `check_cancelled(task_id)` at top of each LangGraph node raises `CancelledError` |
| Task cancelled mid-search or mid-traverse | `check_cancelled(task_id)` called before each I/O call |

---

## Dashboard — `/graph` Endpoint and `INPUT_FIELDS`

`GET /graph` returns the WriteGraph topology (3 nodes: `extract_entities`, `resolve_conflicts`, `store_memories`) plus `input_fields` for the dashboard form. Because the dashboard renders one form per agent, `INPUT_FIELDS` exposes the write skill's fields — the primary/most complex operation — as the default form. Search and traverse parameters are documented in the README for programmatic callers.

```python
INPUT_FIELDS = [
    {"name": "operation", "label": "Operation", "type": "select",
     "options": ["write", "search", "traverse"], "required": True},
    {"name": "namespace", "label": "Namespace", "type": "text", "required": True,
     "placeholder": "e.g. lead_analyst"},
    {"name": "text", "label": "Text (write)", "type": "textarea", "required": False,
     "placeholder": "Raw text to ingest (write operation)"},
    {"name": "query", "label": "Query (search)", "type": "text", "required": False,
     "placeholder": "Semantic search query"},
    {"name": "entity", "label": "Entity (traverse)", "type": "text", "required": False,
     "placeholder": "Entity name to traverse from"},
    {"name": "limit", "label": "Limit (search)", "type": "number", "required": False,
     "placeholder": "5"},
    {"name": "depth", "label": "Depth (traverse)", "type": "number", "required": False,
     "placeholder": "2"},
]
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `MEMORY_AGENT_URL` | `http://localhost:8009` | Agent's externally-reachable URL |
| `MEMORY_NEO4J_URL` | — | Neo4j bolt URL (required) |
| `MEMORY_NEO4J_USER` | — | Neo4j username (required) |
| `MEMORY_NEO4J_PASSWORD` | — | Neo4j password (required) |
| `MEMORY_PG_DSN` | — | pgvector-enabled PostgreSQL DSN (required) |
| `MEMORY_EMBEDDING_MODEL` | — | Embedding model name (required; dims must match `MEMORY_EMBEDDING_DIMS`) |
| `MEMORY_EMBEDDING_DIMS` | — | Vector dimensions, no default — must be set explicitly to match the model |
| `OPENAI_API_KEY` | — | Required for LLM extraction and embeddings |
| `OPENAI_BASE_URL` | OpenAI default | Custom OpenAI-compatible base URL |
| `OPENAI_MODEL` | `gpt-4o-mini` | LLM model for entity extraction |

`MEMORY_*` vars are intentionally separate from `MEM0_*` vars so both agents can coexist.

---

## Testing

File: `tests/test_memory_agent.py`

| Test | What is mocked | What is asserted |
|---|---|---|
| `memory/write` happy path (no existing memories) | LLM extraction call + asyncpg pool (returns 0 existing rows) + Neo4j graph | Output has correct `entities_added`, `relationships_added`, `memories_updated: 0`, `memories_deleted: 0`, `stored: true` |
| `memory/write` conflict resolution — UPDATE | LLM extraction + asyncpg pool (returns 1 existing row) + conflict LLM returns UPDATE resolution + asyncpg UPDATE + Neo4j | `memories_updated: 1`, updated row has new content; new entities still inserted |
| `memory/write` conflict resolution — DELETE | LLM extraction + asyncpg pool (returns 1 existing row) + conflict LLM returns DELETE + asyncpg DELETE + Neo4j node delete | `memories_deleted: 1`; deleted row absent from pool |
| `memory/write` conflict resolution LLM failure | conflict LLM returns invalid JSON | Warning logged; all extracted entities inserted as new; `memories_updated: 0`, `memories_deleted: 0` |
| `memory/write` retry | LLM returns invalid JSON on attempt 1, valid on attempt 2 (invoke graph end-to-end; do not call node function directly) | Task completes; `retry_count` field in state = 1 after second attempt |
| `memory/search` | `stores.get_pgvector_pool` + `stores.get_embedder` | Results are ranked, namespace-filtered, `metadata` is the raw JSONB value |
| `memory/traverse` | `stores.get_neo4j_graph` | Nodes and edges match expected shape |
| Namespace isolation | `stores.get_pgvector_pool` returns rows only for matching namespace | Search in namespace `A` returns 0 results |
| Cancellation (write) | `check_cancelled` raises on second call inside a node | Task reaches `canceled` state |
| Cancellation (search) | `check_cancelled` raises before DB query | Task reaches `canceled` state |
| Unknown operation | No store mocks | Task reaches `failed` state with message containing `"Unknown operation"` |

Retry test note: the WriteGraph retry loop is a conditional edge (same as `knowledge_graph`). Test by calling `graph.ainvoke()` with two sequential LLM mock responses, not by calling the node function directly. See `tests/test_knowledge_graph.py` for the established pattern.

All tests use `pytest-asyncio` (`asyncio_mode = auto`) and `pytest-httpx`. No real Neo4j or Postgres required. Store singletons are patched at the module level.

---

## Deployment

**`docker-compose.yml`** — add `memory-agent` service depending on `control-plane`, `postgres`, and `neo4j`. No new infrastructure services required.

**`run-local.sh`** — add step starting `python -m agents.memory_agent.server` with `MEMORY_*` env vars.

**`Dockerfile.memory-agent`** — same pattern as `Dockerfile.knowledge-graph`.

**`CLAUDE.md`** — add `memory_agent` row to agents table (port 8009, type `memory-agent`) and add all `MEMORY_*` env vars to the env var tables.
