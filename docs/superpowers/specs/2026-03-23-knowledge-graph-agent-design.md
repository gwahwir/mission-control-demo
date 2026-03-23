# Knowledge Graph Agent — Design Spec

**Date:** 2026-03-23
**Status:** Approved
**Port:** 8008
**Agent Type ID:** `knowledge-graph`
**Location:** `agents/knowledge_graph/`

---

## 1. Purpose

A persistent knowledge graph agent that ingests raw articles or text snippets and builds an evolving, queryable knowledge graph of **entities** (persons, organisations, locations, products) and **issues** (topics of world interest: geopolitical tensions, economic crises, policy debates, emerging technologies, etc.) as first-class citizens.

The agent tracks how entities and issues evolve over time across multiple ingestion events. It is designed to eventually feed structured knowledge upstream to the Lead Analyst and Probability agents.

---

## 2. Scope (Current Implementation)

**In scope:**
- Ingest raw text → extract entities, issues, and relationships via LLM
- Store extracted data into mem0 (Neo4j graph + pgvector vectors)
- Return a dual-format response: structured JSON diff + human-readable narrative

**Out of scope (documented for future implementation — see Section 9):**
- Query operation
- Diff operation

---

## 3. Architecture

```
agents/knowledge_graph/
├── graph.py       # LangGraph state machine (2 nodes)
├── executor.py    # Subclass of LangGraphA2AExecutor
├── server.py      # A2A FastAPI server, port 8008
└── README.md
```

Follows the identical structure of all existing agents in this repo (`graph.py` → `executor.py` → `server.py`), inheriting from `LangGraphA2AExecutor`.

---

## 4. Data Schema

### 4.1 Extraction Output (Node 1 → Node 2)

```json
{
  "entities": [
    {
      "name": "Elon Musk",
      "type": "person",
      "attributes": {"role": "CEO", "sentiment": "neutral"}
    },
    {
      "name": "Tesla",
      "type": "organization",
      "attributes": {"sector": "automotive"}
    },
    {
      "name": "United States",
      "type": "location",
      "attributes": {"type": "country"}
    },
    {
      "name": "Starlink",
      "type": "product",
      "attributes": {"owner": "SpaceX"}
    }
  ],
  "issues": [
    {
      "name": "AI Regulation Debate",
      "type": "issue",
      "attributes": {
        "domain": "technology|policy",
        "severity": "high|medium|low",
        "status": "emerging|ongoing|resolved",
        "summary": "Brief description of the issue"
      }
    }
  ],
  "relationships": [
    {"subject": "Elon Musk", "predicate": "leads", "object": "Tesla"},
    {"subject": "AI Regulation Debate", "predicate": "involves", "object": "United States"}
  ],
  "source_summary": "2-3 sentence summary of the source article."
}
```

### 4.2 Agent Response (Dual Output)

```json
{
  "diff": {
    "entities": {"added": [...], "updated": [...]},
    "issues": {"added": [...], "updated": [...]},
    "relationships": {"added": [...]}
  },
  "narrative": "This article introduced 3 new entities and updated the ongoing 'AI Regulation Debate' issue, linking it to Tesla and the United States for the first time.",
  "stats": {
    "entities_added": 2,
    "entities_updated": 1,
    "issues_added": 0,
    "issues_updated": 1,
    "relationships_added": 3
  }
}
```

### 4.3 mem0 Storage Mapping

- Each **entity** and **issue** is stored as a mem0 memory with `user_id` set to the entity/issue name — mem0's native deduplication key
- Both Neo4j (graph node) and pgvector (semantic embedding) are populated per entry
- **Relationships** are stored as mem0 graph edges in Neo4j

---

## 5. LangGraph Nodes

### Node 1: `extract_entities_and_issues`

Accepts raw text input. Makes an LLM call (OpenAI) with a structured extraction prompt to produce entities, issues, relationships, and a source summary.

**Self-correcting retry behaviour:**
- On malformed or unparseable JSON response, retries up to **3 times**
- Each retry appends the previous raw output and parse error to the prompt:
  > *"Your previous response failed to parse as valid JSON. Error: `<error>`. Your response was: `<raw_output>`. Please correct your response and try again, returning only valid JSON."*
- After 3 exhausted attempts, falls back to empty extraction and logs a warning — pipeline continues rather than failing
- Uses LangGraph `RetryPolicy` combined with state-carried error context threaded into the prompt on each attempt

### Node 2: `store_in_mem0`

Receives the structured extraction. Performs the following steps:

1. Snapshot pre-ingest graph state for affected entities/issues
2. For each entity and issue: call `mem0.add()` with Neo4j + pgvector backends
3. For each relationship: write graph edge via mem0
4. Snapshot post-ingest graph state
5. Compute diff (added vs. updated nodes and edges)
6. Make a second small LLM call to generate the human-readable narrative from the diff + source summary
7. Return dual-format output

**Individual write failures** are logged but do not abort the full ingest — partial success is preferred over total failure.

**Cancellation:** `executor.check_cancelled(task_id)` is called at the start of both nodes, consistent with all other agents in this repo.

---

## 6. Data Flow

```
Raw text (A2A input)
        │
        ▼
┌──────────────────────────────────┐
│   extract_entities_and_issues     │  ← LLM call (OpenAI)
│   - Parse input text              │    Self-correcting retry on bad JSON
│   - Extract entities              │    (up to 3 attempts with error context)
│   - Extract issues                │
│   - Extract relationships         │
│   - Produce source_summary        │
└──────────────┬───────────────────┘
               │
               ▼
┌──────────────────────────────────┐
│          store_in_mem0            │  ← mem0 hybrid client
│   - Pre-ingest graph snapshot     │    (Neo4j + pgvector)
│   - Add entities via mem0         │
│   - Add issues via mem0           │
│   - Add relationships             │
│   - Post-ingest graph snapshot    │
│   - Compute diff                  │
│   - Generate narrative (LLM)      │
└──────────────┬───────────────────┘
               │
               ▼
      Dual output artifact
      (structured diff + narrative)
```

---

## 7. Environment Variables

| Variable | Description |
|---|---|
| `MEM0_NEO4J_URL` | Neo4j bolt URL (e.g. `bolt://localhost:7687`) |
| `MEM0_NEO4J_USER` | Neo4j username |
| `MEM0_NEO4J_PASSWORD` | Neo4j password |
| `MEM0_PG_DSN` | pgvector connection string |
| `KNOWLEDGE_GRAPH_AGENT_URL` | Agent's externally reachable URL |
| `OPENAI_API_KEY` | Required for LLM calls |
| `OPENAI_BASE_URL` | Optional custom OpenAI-compatible base URL |
| `OPENAI_MODEL` | LLM model (default: `gpt-4o-mini`) |
| `CONTROL_PLANE_URL` | Control plane URL for self-registration |

**mem0 client lifecycle:** Module-level `Memory` client instantiated once on first use (mirroring `_openai_client` pattern in the extraction agent). If required env vars are missing, raises a descriptive error at startup rather than failing silently at request time.

---

## 8. Testing

All tests follow the existing `pytest-asyncio` + `pytest-httpx` pattern in `tests/`.

| Test | Description |
|---|---|
| `test_kg_extract_node` | Unit test for `extract_entities_and_issues` with mocked OpenAI; verifies schema compliance |
| `test_kg_extract_node_self_correcting_retry` | Verifies retry behaviour: first 1-2 responses return bad JSON, final attempt returns valid JSON; confirms error context is injected into retry prompt |
| `test_kg_extract_node_retry_exhausted` | Verifies fallback to empty extraction after 3 failed attempts |
| `test_kg_store_node` | Unit test for `store_in_mem0` with mocked mem0 client; verifies diff computation and narrative generation |
| `test_kg_full_pipeline` | Integration test mocking OpenAI + mem0; submits raw text, asserts dual-output artifact structure |
| `test_kg_cancellation` | Verifies clean cancellation mid-graph |

---

## 9. Future Operations (Not Implemented)

### 9.1 Query

Accept a named entity or issue and return a structured answer synthesising:
- Semantic recall from pgvector (what do we know about X?)
- Graph traversal from Neo4j (what is X connected to, and how?)
- A human-readable summary

Input: `{"query": "AI Regulation Debate"}`
Output: summary + related entities/issues + relationship map

### 9.2 Diff

Accept a named entity or issue and a reference date/snapshot ID. Return:
- What attributes changed
- What new relationships appeared or disappeared
- A narrative describing how X has evolved since the reference point

Input: `{"entity": "Elon Musk", "since": "2026-01-01"}`
Output: attribute diff + relationship diff + narrative

---

## 10. Integration Path (Future)

This agent is designed to feed the **Lead Analyst** and **Probability Agent** pipelines:

- Lead Analyst could query this agent's knowledge graph to enrich its context before fanning out to specialists
- Probability Agent could use entity/issue evolution diffs to inform probability shifts over time
- The dual JSON/narrative output format is designed to be consumable by both machine (JSON) and LLM-based (narrative) downstream agents
