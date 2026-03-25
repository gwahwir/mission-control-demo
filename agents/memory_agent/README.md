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
extract_entities → resolve_conflicts → store_memories
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
