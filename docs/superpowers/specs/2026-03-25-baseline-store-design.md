# Baseline Store Design

**Date:** 2026-03-25
**Status:** Draft
**Port:** 8010
**Service type:** Plain FastAPI service (not A2A agent)

---

## Overview

A deterministic storage and retrieval layer for topic baselines. A **baseline** is a narrative assessment of what is currently understood to be true about a topic, evolving incrementally as new articles arrive. The store handles versioning, hierarchy, delta logging, and semantic search — with no LLM calls, no task lifecycle, and no A2A layer.

All agency (relevance assessment, delta extraction, narrative synthesis) lives in the **Baseline Agent** (future, separate), which uses this service as a tool over plain HTTP.

---

## Architecture

```
baseline_store/
├── server.py     # FastAPI app, lifespan (DDL on startup)
├── stores.py     # asyncpg pool + embedder singletons
├── routes.py     # All REST endpoints
└── README.md
```

Infrastructure reuses the existing `postgres` Docker Compose service. Requires `ltree` and `vector` extensions — both ship with the `pgvector/pgvector:pg16` image already in `docker-compose.yml`. No new containers required.

The store owns the embedding concern entirely. Callers pass plain text; the store embeds internally. This keeps callers agnostic to embedding model, dimensions, and SDK.

---

## REST API

All endpoints return `application/json`. No authentication in initial implementation.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/topics` | Register a topic with its hierarchical path and display name |
| `GET` | `/topics` | List all registered topics *(added during spec authoring — not in original brainstorm scope but clearly useful)* |
| `GET` | `/baselines/{topic_path}/current` | Get the most recent baseline version for a topic |
| `GET` | `/baselines/{topic_path}/history` | Get full append-only version history + delta log |
| `POST` | `/baselines/{topic_path}/versions` | Write a new baseline version (narrative + citations) |
| `POST` | `/baselines/{topic_path}/deltas` | Record a delta entry for a version transition |
| `GET` | `/baselines/{topic_path}/rollup` | Aggregate current baselines across all descendant topics (ancestor excluded) |
| `GET` | `/baselines/similar` | Global semantic search — find most similar baseline versions by text query |

---

## Request & Response Shapes

### `POST /topics`

```json
// Request
{ "topic_path": "climate_change.energy", "display_name": "Energy" }

// Response 201
{ "id": "uuid", "topic_path": "climate_change.energy", "display_name": "Energy", "created_at": "..." }
```

`topic_path` uses dot-separated `ltree` format. Examples: `"us_iran_conflict"`, `"climate_change.energy"`, `"climate_change.energy.oil_prices"`.

---

### `GET /topics`

```json
// Response 200
{
  "topics": [
    { "id": "uuid", "topic_path": "climate_change", "display_name": "Climate Change", "created_at": "..." },
    { "id": "uuid", "topic_path": "climate_change.energy", "display_name": "Energy", "created_at": "..." }
  ]
}
```

---

### `GET /baselines/{topic_path}/current`

```json
// Response 200
{
  "topic_path": "climate_change.energy",
  "version_number": 7,
  "narrative": "As of March 2026, energy markets remain under pressure...",
  "citations": [
    { "article_id": "abc123", "title": "Oil hits $95", "url": "https://...", "source": "Reuters", "published_at": "2026-03-24T12:00:00Z", "excerpt": "..." }
  ],
  "created_at": "2026-03-25T08:30:00Z"
}

// Response 404 if topic is not registered
{ "detail": "Topic not registered: climate_change.energy" }

// Response 404 if topic is registered but no versions written yet
{ "detail": "No versions written yet for topic: climate_change.energy" }
```

---

### `POST /baselines/{topic_path}/versions`

The store computes the embedding from `narrative` internally. Caller does not pass a vector.

```json
// Request
{
  "narrative": "Updated assessment as of March 2026...",
  "citations": [
    { "article_id": "xyz789", "title": "Iran enrichment update", "url": "https://...", "source": "AP", "published_at": "2026-03-25T06:00:00Z", "excerpt": "Iran crossed..." }
  ]
}

// Response 201
{ "version_number": 8, "id": "uuid", "created_at": "2026-03-25T09:00:00Z" }

// Response 404 if topic not registered
{ "detail": "Topic not registered: climate_change.energy — call POST /topics first" }
```

`version_number` is computed as `MAX(version_number) + 1` for the topic. First version is `1`.

---

### `POST /baselines/{topic_path}/deltas`

Written by the baseline agent immediately after writing a new version, recording what caused the change.

```json
// Request
{
  "from_version": 7,
  "to_version": 8,
  "article_metadata": {
    "article_id": "xyz789",
    "title": "Iran enrichment update",
    "url": "https://...",
    "source": "AP",
    "published_at": "2026-03-25T06:00:00Z"
  },
  "delta_summary": "Iran crossed the 60% enrichment threshold, superseding prior assessment.",
  "claims_added": ["Iran crossed the 60% enrichment threshold"],
  "claims_superseded": ["Iran was assessed below 60% enrichment threshold"]
}

// Response 201
{ "id": "uuid", "created_at": "2026-03-25T09:00:01Z" }

// Response 422 if to_version does not exist in baseline_versions for this topic
{ "detail": "to_version 8 does not exist for topic: climate_change.energy" }
```

`from_version` is `null` when this is the first baseline version ever written for a topic. The store validates that `to_version` exists in `baseline_versions` before inserting — callers must write the version before recording the delta.

---

### `GET /baselines/{topic_path}/history`

Returns all versions (newest first) and the full delta log. Returns `404` if the topic is not registered. Returns `200` with `versions: [], deltas: []` if the topic is registered but has no versions yet.

```json
// Response 200
{
  "topic_path": "climate_change.energy",
  "versions": [
    {
      "version_number": 8,
      "narrative": "...",
      "citations": [...],
      "created_at": "2026-03-25T09:00:00Z"
    },
    {
      "version_number": 7,
      "narrative": "...",
      "citations": [...],
      "created_at": "2026-03-24T15:00:00Z"
    }
  ],
  "deltas": [
    {
      "from_version": 7,
      "to_version": 8,
      "delta_summary": "Iran crossed the 60% enrichment threshold...",
      "claims_added": [...],
      "claims_superseded": [...],
      "article_metadata": { "title": "...", "url": "...", "source": "AP", "published_at": "..." },
      "created_at": "2026-03-25T09:00:01Z"
    }
  ]
}
```

---

### `GET /baselines/{topic_path}/rollup`

Returns the current baseline version for each registered **descendant** topic. The ancestor topic itself is excluded — its own current baseline is available via `/current`. No synthesis — the baseline agent is responsible for combining narratives if needed.

Returns `200` with `descendants: []` if the topic has no registered descendants (or no descendants have baselines yet).

```json
// Response 200
{
  "ancestor": "climate_change",
  "descendants": [
    {
      "topic_path": "climate_change.energy",
      "version_number": 8,
      "narrative": "...",
      "citations": [...],
      "created_at": "..."
    },
    {
      "topic_path": "climate_change.energy.oil_prices",
      "version_number": 3,
      "narrative": "...",
      "citations": [...],
      "created_at": "..."
    }
  ]
}
```

Uses `topic_path <@ $ancestor` ltree operator — single index scan across all descendants at any depth. Only descendants that have at least one baseline version are included.

---

### `GET /baselines/similar?query=...&limit=5`

Embeds `query` internally and returns the top-N most semantically similar baseline versions across **all topics** (global search — not scoped to any topic path). Used by the baseline agent to retrieve relevant prior assessments before deciding whether to update.

```json
// Response 200
{
  "results": [
    {
      "topic_path": "us_iran_conflict",
      "version_number": 4,
      "narrative": "...",
      "citations": [...],
      "score": 0.91,
      "created_at": "..."
    }
  ]
}
```

`score` is cosine similarity (`1 - distance`). Default `limit` is 5.

---

## Data Model

Three tables, created on startup via `asyncpg` DDL if they do not already exist.

```sql
CREATE EXTENSION IF NOT EXISTS ltree;
CREATE EXTENSION IF NOT EXISTS vector;

-- Topic registry
CREATE TABLE IF NOT EXISTS baseline_topics (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    topic_path   ltree NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    created_at   TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS baseline_topics_path_gist
    ON baseline_topics USING GIST (topic_path);

-- Append-only baseline versions
CREATE TABLE IF NOT EXISTS baseline_versions (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    topic_path     ltree NOT NULL,
    version_number INTEGER NOT NULL,
    narrative      TEXT NOT NULL,
    embedding      vector(N),             -- N set from BASELINE_EMBEDDING_DIMS at startup
    citations      JSONB DEFAULT '[]',
    created_at     TIMESTAMPTZ DEFAULT now(),
    UNIQUE (topic_path, version_number)
);
CREATE INDEX IF NOT EXISTS baseline_versions_topic_gist
    ON baseline_versions USING GIST (topic_path);
CREATE INDEX IF NOT EXISTS baseline_versions_topic_version
    ON baseline_versions (topic_path, version_number DESC);
CREATE INDEX IF NOT EXISTS baseline_versions_embedding
    ON baseline_versions USING hnsw (embedding vector_cosine_ops);

-- Delta log
CREATE TABLE IF NOT EXISTS baseline_deltas (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    topic_path        ltree NOT NULL,
    from_version      INTEGER,            -- NULL if first baseline for this topic
    to_version        INTEGER NOT NULL,
    article_metadata  JSONB DEFAULT '{}',
    delta_summary     TEXT NOT NULL,
    claims_added      JSONB DEFAULT '[]',
    claims_superseded JSONB DEFAULT '[]',
    created_at        TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS baseline_deltas_topic_gist
    ON baseline_deltas USING GIST (topic_path);
```

**Notes:**
- `vector(N)` is fixed at table creation time. `BASELINE_EMBEDDING_DIMS` must be set before first run and must match the embedding model's output dimensions. No safe default — different models produce different sizes. **Important:** `CREATE TABLE IF NOT EXISTS` will silently keep the existing column with its original `N` if the table already exists. Changing `BASELINE_EMBEDDING_DIMS` after the table has been created requires a schema migration — it is not enough to change the env var.
- `version_number` is monotonically incrementing per `topic_path`, computed as `SELECT COALESCE(MAX(version_number), 0) + 1` inside a transaction.
- `citations` JSONB schema: `[{ article_id, title, url, source, published_at, excerpt }]`

---

## `stores.py` — Singletons

Two module-level singletons, lazily initialized on first use:

- `get_pgvector_pool()` — returns an `asyncpg` connection pool; runs DDL on first call; requires `BASELINE_PG_DSN`
- `get_embedder()` — returns an async embedding callable; requires `BASELINE_EMBEDDING_MODEL` and `OPENAI_API_KEY`

Both raise `EnvironmentError` on first use if required env vars are missing.

---

## Error Handling

| Scenario | HTTP Status | Detail |
|---|---|---|
| `topic_path` not registered on version write | `404` | "Topic not registered — call POST /topics first" |
| Duplicate `topic_path` on `POST /topics` | `409` | "Topic already registered: ..." |
| `topic_path` not registered on `/current` | `404` | "Topic not registered: ..." |
| `topic_path` registered but no versions yet on `/current` | `404` | "No versions written yet for topic: ..." |
| `topic_path` not registered on `/history` | `404` | "Topic not registered: ..." |
| Topic registered but no versions yet on `/history` | `200` | `{ versions: [], deltas: [] }` |
| `topic_path` not registered on `/rollup` | `404` | "Topic not registered: ..." |
| `/rollup` topic has no registered descendants | `200` | `{ ancestor: "...", descendants: [] }` |
| `to_version` does not exist on delta write | `422` | "to_version N does not exist for topic: ..." — write the version before the delta |
| `version_number` conflict (race) | `409` | Caller should re-fetch MAX and retry |
| Invalid `ltree` path format | `422` | Postgres rejects malformed paths; surfaced as validation error |
| Missing required env vars | Service fails to start with clear `EnvironmentError` message | — |
| Embedding dimension mismatch | `500` logged with clear message | `BASELINE_EMBEDDING_DIMS` must match model output dims |

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `BASELINE_PG_DSN` | — | pgvector + ltree enabled Postgres DSN (required) |
| `BASELINE_EMBEDDING_MODEL` | — | Embedding model name (required) |
| `BASELINE_EMBEDDING_DIMS` | — | Vector dims — must match model output, no default (required) |
| `OPENAI_API_KEY` | — | Required for embedder |
| `OPENAI_BASE_URL` | OpenAI default | Custom OpenAI-compatible base URL |
| `BASELINE_PORT` | `8010` | Port the service listens on |
| `BASELINE_STORE_URL` | `http://localhost:8010` | Externally-reachable URL (used by future baseline agent) |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

`BASELINE_*` vars are intentionally separate from `MEMORY_*` and `MEM0_*` vars.

---

## Testing

File: `tests/test_baseline_store.py`

| Test | What is mocked | What is asserted |
|---|---|---|
| `POST /topics` happy path | asyncpg pool | Returns 201 with correct fields |
| `POST /topics` duplicate | asyncpg pool raises `UniqueViolationError` | Returns 409 |
| `POST /baselines/{topic}/versions` happy path | asyncpg pool + embedder | Returns 201 with `version_number: 1`; embedding computed and stored |
| `POST /baselines/{topic}/versions` topic not registered | asyncpg pool returns no row for topic | Returns 404 |
| `POST /baselines/{topic}/versions` version conflict | asyncpg INSERT raises `UniqueViolationError` | Returns 409 |
| `GET /baselines/{topic}/current` happy path | asyncpg pool returns one row | Returns 200 with correct narrative and citations |
| `GET /baselines/{topic}/current` topic not registered | asyncpg pool returns no topic row | Returns 404 with "Topic not registered" |
| `GET /baselines/{topic}/current` registered but no versions | asyncpg pool returns topic row but no version rows | Returns 404 with "No versions written yet" |
| `GET /baselines/{topic}/history` happy path | asyncpg pool returns versions + deltas | Both arrays present; versions ordered newest first |
| `GET /baselines/{topic}/history` topic not registered | asyncpg pool returns no topic row | Returns 404 |
| `GET /baselines/{topic}/history` registered but no versions | asyncpg pool returns topic row but no version rows | Returns 200 with `versions: [], deltas: []` |
| `POST /baselines/{topic}/deltas` | asyncpg pool | Returns 201 |
| `GET /baselines/{topic}/rollup` | asyncpg pool returns descendant rows | All descendants returned; ancestor not included |
| `GET /baselines/{topic}/similar` | asyncpg pool + embedder | Results ranked by score descending; `score` between 0 and 1 |
| Embedder called on version write | embedder mock | Embedder called exactly once with narrative text |
| Embedder called on similar query | embedder mock | Embedder called exactly once with query text |

All tests use `pytest-asyncio` (`asyncio_mode = auto`). Store singletons patched at module level. No real Postgres or embedding model required.

---

## Deployment

- **`docker-compose.yml`** — add `baseline-store` service depending on `postgres`. No new infrastructure containers.
- **`run-local.sh`** — add step starting `python -m baseline_store.server` with `BASELINE_*` env vars.
- **`Dockerfile.baseline-store`** — same pattern as other service Dockerfiles.
- **`CLAUDE.md`** — add `baseline_store` entry and all `BASELINE_*` env vars to the env var tables.

---

## Future: Neo4j-backed Hierarchy (Approach 3)

If topics require **many-to-many cross-cutting relationships** (e.g., "Energy" belongs under both "Climate Change" *and* "Geopolitics"), migrate the topic hierarchy from `ltree` to Neo4j:

- Topics as `(:Topic { path: str, display_name: str })` nodes
- `IS_SUBTOPIC_OF` relationships for hierarchy edges
- `/rollup` becomes a Cypher traversal instead of a `<@` ltree scan
- `baseline_versions` and `baseline_deltas` remain in Postgres unchanged

`ltree` handles all tree (single-parent) hierarchies efficiently. Only migrate to Neo4j when the hierarchy genuinely requires a graph (multiple parents per node). This is a store-internal change — the REST API surface does not change.
