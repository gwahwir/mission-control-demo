# Baseline Store

## Purpose

Baseline Store is a deterministic storage and retrieval layer for **topic baselines** — versioned narrative snapshots of what is currently understood to be true about a subject. It is designed to be called by agents (such as the Lead Analyst) that need to:

- **Persist** an evolving understanding of a topic over time
- **Track deltas** — record what changed, what was added, and what was superseded between versions
- **Retrieve** the current baseline before running a new analysis (so the agent can focus on what's changed, not re-derive known facts)
- **Search semantically** across all baselines to find related topics

Unlike the agents in this project, Baseline Store is a plain FastAPI service — it is not an A2A agent and does not self-register with the control plane.

---

## Typical Workflow

```
1. Register a topic          POST /topics
         ↓
2. Write initial baseline    POST /baselines/{topic}/versions
         ↓
3. New article arrives → agent re-analyses
         ↓
4. Write updated baseline    POST /baselines/{topic}/versions
         ↓
5. Record what changed       POST /baselines/{topic}/deltas
         ↓
6. Next session: fetch        GET /baselines/{topic}/current
   current baseline before
   running new analysis
```

**Topic paths** use dot-separated `ltree` format — e.g. `geo`, `geo.middle_east`, `geo.middle_east.iran`. This enables rollup queries that aggregate all descendants under a parent topic.

---

## How to Run Locally

```bash
# With an OpenAI-compatible embedding model:
export BASELINE_PG_DSN="postgresql://user:pass@localhost:5432/baselines"
export BASELINE_EMBEDDING_MODEL="text-embedding-3-small"
export BASELINE_EMBEDDING_DIMS="1536"
export OPENAI_API_KEY="sk-..."

# With a Jina embedding model (JINA_API_KEY used instead of OPENAI_API_KEY):
export BASELINE_PG_DSN="postgresql://user:pass@localhost:5432/baselines"
export BASELINE_EMBEDDING_MODEL="jina-embeddings-v5-text-small"
export BASELINE_EMBEDDING_DIMS="1024"
export JINA_API_KEY="jina_..."

python -m baseline_store.server
# Server listens on http://0.0.0.0:8010 by default
```

The service creates the required PostgreSQL tables (`baseline_topics`, `baseline_versions`, `baseline_deltas`) with `ltree` and `pgvector` extensions on first startup.

---

## Usage Examples

### 1. Register a topic

Topics must be registered before any baselines can be written. Use dot notation for hierarchy.

```bash
curl -s -X POST http://localhost:8010/topics \
  -H "Content-Type: application/json" \
  -d '{"topic_path": "geo.middle_east.iran", "display_name": "Iran"}'
```

```json
{
  "id": "a1b2c3d4-...",
  "topic_path": "geo.middle_east.iran",
  "display_name": "Iran",
  "created_at": "2026-03-31T09:00:00+00:00"
}
```

Register a parent topic separately if you want rollup to work:

```bash
curl -s -X POST http://localhost:8010/topics \
  -H "Content-Type: application/json" \
  -d '{"topic_path": "geo.middle_east", "display_name": "Middle East"}'
```

---

### 2. List all registered topics

```bash
curl -s http://localhost:8010/topics
```

```json
{
  "topics": [
    {"id": "...", "topic_path": "geo.middle_east", "display_name": "Middle East", "created_at": "..."},
    {"id": "...", "topic_path": "geo.middle_east.iran", "display_name": "Iran", "created_at": "..."}
  ]
}
```

---

### 3. Write a baseline version

The store embeds the narrative internally — callers pass plain text. `citations` is optional.

```bash
curl -s -X POST http://localhost:8010/baselines/geo.middle_east.iran/versions \
  -H "Content-Type: application/json" \
  -d '{
    "narrative": "As of March 2026, Iran has enriched uranium to approximately 60% purity at Fordow and Natanz. International negotiations remain stalled. The IAEA has limited inspection access.",
    "citations": [
      {
        "article_id": "reuters-2026-03-28",
        "title": "Iran enrichment update",
        "url": "https://reuters.com/...",
        "source": "Reuters",
        "published_at": "2026-03-28T12:00:00Z",
        "excerpt": "Iran has crossed the 60% enrichment threshold..."
      }
    ]
  }'
```

```json
{
  "id": "e5f6a7b8-...",
  "version_number": 1,
  "created_at": "2026-03-31T09:05:00+00:00"
}
```

Each subsequent `POST` to the same topic increments `version_number` automatically.

---

### 4. Record a delta (what changed between versions)

After writing a new version, record what drove the change. `from_version` is `null` for the first-ever baseline.

```bash
curl -s -X POST http://localhost:8010/baselines/geo.middle_east.iran/deltas \
  -H "Content-Type: application/json" \
  -d '{
    "from_version": 1,
    "to_version": 2,
    "article_metadata": {
      "article_id": "ap-2026-03-30",
      "title": "Iran resumes talks",
      "url": "https://apnews.com/...",
      "source": "AP",
      "published_at": "2026-03-30T08:00:00Z"
    },
    "delta_summary": "Iran agreed to resume indirect talks via Oman, reversing the prior assessment that negotiations were fully stalled.",
    "claims_added": ["Iran has agreed to resume indirect talks via Oman"],
    "claims_superseded": ["International negotiations remain fully stalled"]
  }'
```

```json
{"id": "...", "created_at": "2026-03-31T09:10:00+00:00"}
```

---

### 5. Get the current baseline

Fetch the latest version before running a new analysis. The agent uses this to perform delta analysis rather than re-deriving known facts.

```bash
curl -s http://localhost:8010/baselines/geo.middle_east.iran/current
```

```json
{
  "topic_path": "geo.middle_east.iran",
  "version_number": 2,
  "narrative": "As of March 2026, Iran has enriched uranium to approximately 60%...",
  "citations": [...],
  "created_at": "2026-03-31T09:08:00+00:00"
}
```

Returns `404` if the topic is not registered, or if no versions have been written yet.

---

### 6. Get full version history and delta log

```bash
curl -s http://localhost:8010/baselines/geo.middle_east.iran/history
```

```json
{
  "topic_path": "geo.middle_east.iran",
  "versions": [
    {"version_number": 2, "narrative": "...", "citations": [...], "created_at": "..."},
    {"version_number": 1, "narrative": "...", "citations": [...], "created_at": "..."}
  ],
  "deltas": [
    {
      "from_version": 1,
      "to_version": 2,
      "delta_summary": "Iran agreed to resume indirect talks...",
      "claims_added": ["..."],
      "claims_superseded": ["..."],
      "article_metadata": {"title": "Iran resumes talks", ...},
      "created_at": "..."
    }
  ]
}
```

---

### 7. Rollup: get current baselines for all descendants

Useful when the Lead Analyst needs a full picture of all sub-topics under a parent.

```bash
curl -s http://localhost:8010/baselines/geo.middle_east/rollup
```

```json
{
  "ancestor": "geo.middle_east",
  "descendants": [
    {
      "topic_path": "geo.middle_east.iran",
      "version_number": 2,
      "narrative": "...",
      "citations": [...],
      "created_at": "..."
    },
    {
      "topic_path": "geo.middle_east.israel",
      "version_number": 5,
      "narrative": "...",
      "citations": [...],
      "created_at": "..."
    }
  ]
}
```

Only descendants that have at least one written version are included. The ancestor topic itself is excluded — fetch it separately via `/current`.

---

### 8. Semantic search across all baselines

Find the most relevant baselines by free-text query, across all topics. Useful for cross-domain signal detection.

```bash
curl -s "http://localhost:8010/baselines/similar?query=nuclear+enrichment+negotiations&limit=3"
```

```json
{
  "results": [
    {
      "topic_path": "geo.middle_east.iran",
      "version_number": 2,
      "narrative": "...",
      "citations": [...],
      "score": 0.94,
      "created_at": "..."
    },
    {
      "topic_path": "geo.asia.north_korea",
      "version_number": 7,
      "narrative": "...",
      "citations": [...],
      "score": 0.81,
      "created_at": "..."
    }
  ]
}
```

`score` is cosine similarity (0–1). Default `limit` is 5.

---

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/topics` | Register a new topic path |
| GET | `/topics` | List all registered topics |
| POST | `/baselines/{topic_path}/versions` | Write a new versioned narrative; embedding generated internally |
| POST | `/baselines/{topic_path}/deltas` | Record a delta entry describing a transition between two versions |
| GET | `/baselines/{topic_path}/current` | Get the latest version for a topic |
| GET | `/baselines/{topic_path}/history` | Get the full version and delta history |
| GET | `/baselines/{topic_path}/rollup` | Get the current version of every descendant topic |
| GET | `/baselines/similar` | Semantic search across all baselines by query text |

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `BASELINE_PG_DSN` | Yes | — | pgvector + ltree enabled Postgres DSN |
| `BASELINE_EMBEDDING_MODEL` | Yes | — | Embedding model name (e.g. `text-embedding-3-small`, `jina-embeddings-v5-text-small`) |
| `BASELINE_EMBEDDING_DIMS` | Yes | — | Vector dimensions; must match the model's output size |
| `OPENAI_API_KEY` | Conditional | — | Required when `BASELINE_EMBEDDING_MODEL` does not contain `"jina"` |
| `JINA_API_KEY` | Conditional | — | Required when `BASELINE_EMBEDDING_MODEL` contains `"jina"` (e.g. `jina-embeddings-v5-text-small`) |
| `OPENAI_BASE_URL` | No | OpenAI default | Custom OpenAI-compatible base URL |
| `BASELINE_PORT` | No | `8010` | Port the server listens on |
| `LOG_LEVEL` | No | `INFO` | Logging verbosity (DEBUG, INFO, WARNING, ERROR) |

---

## Note on Embedding Dimensions

`BASELINE_EMBEDDING_DIMS` is used at DDL time to set the fixed dimension of the `vector` column in `baseline_versions`. If you change this value after the table has been created, you must drop and recreate `baseline_versions` — the column dimension cannot be altered in place. Ensure the value matches the actual output dimension of `BASELINE_EMBEDDING_MODEL`.
