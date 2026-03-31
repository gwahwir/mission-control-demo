# Baseline Store

## Purpose

Baseline Store is a deterministic storage and retrieval layer for topic baselines. It maintains versioned narrative snapshots organised in an ltree path hierarchy (e.g. `geo.europe.ukraine`), records delta log entries that describe transitions between versions, and supports pgvector-powered semantic search across all current baselines. Unlike the agents in this project, Baseline Store is a plain FastAPI service — it is not an A2A agent and does not self-register with the control plane. It is intended to be called by agents (such as the Lead Analyst) that need to persist or compare baseline narratives over time.

## How to Run Locally

Set the required environment variables and start the server:

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

The service will create the required PostgreSQL tables (with ltree and pgvector extensions) on first startup via `get_pgvector_pool()`.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/topics` | Register a new topic path (e.g. `geo.europe.ukraine`) |
| GET | `/topics` | List all registered topics |
| POST | `/baselines/{topic_path}/versions` | Write a new versioned narrative; embedding is generated internally |
| POST | `/baselines/{topic_path}/deltas` | Record a delta entry describing a transition between two versions |
| GET | `/baselines/{topic_path}/current` | Get the latest version record for a topic |
| GET | `/baselines/{topic_path}/history` | Get the full version and delta history for a topic |
| GET | `/baselines/{topic_path}/rollup` | Get the current version of every descendant topic under the given path |
| GET | `/baselines/similar` | Semantic search across all current baselines by query text |

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `BASELINE_PG_DSN` | Yes | — | pgvector + ltree enabled Postgres DSN |
| `BASELINE_EMBEDDING_MODEL` | Yes | — | Embedding model name (e.g. `text-embedding-3-small`) |
| `BASELINE_EMBEDDING_DIMS` | Yes | — | Vector dimensions; must match the model's output size |
| `OPENAI_API_KEY` | Conditional | — | Required when `BASELINE_EMBEDDING_MODEL` does not contain `"jina"` |
| `JINA_API_KEY` | Conditional | — | Required when `BASELINE_EMBEDDING_MODEL` contains `"jina"` (e.g. `jina-embeddings-v5-text-small`) |
| `OPENAI_BASE_URL` | No | OpenAI default | Custom OpenAI-compatible base URL |
| `BASELINE_PORT` | No | `8010` | Port the server listens on |
| `LOG_LEVEL` | No | `INFO` | Logging verbosity (DEBUG, INFO, WARNING, ERROR) |

## Note on Embedding Dimensions

`BASELINE_EMBEDDING_DIMS` is used at DDL time to set the fixed dimension of the `vector` column in the `baseline_versions` table. If you change this value after the table has been created, you must drop and recreate the `baseline_versions` table — the column dimension cannot be altered in place. Ensure the value matches the actual output dimension of the model specified in `BASELINE_EMBEDDING_MODEL`.
