# Wiki Agent

LLM-maintained intelligence wiki for geopolitics/IR topics. Implements the "LLM Wiki" pattern: instead of re-deriving knowledge on every query (RAG), the agent incrementally builds and maintains a persistent wiki of markdown files that compounds with every ingested source.

**Port:** 8011  
**Type ID:** `wiki-agent`

---

## Operations

Operation is inferred from the input fields present:

| Input fields | Operation |
|---|---|
| `input_text` present | **ingest** |
| `query` present | **query** |
| neither | **lint** |

### Ingest

Runs an 11-node LangGraph pipeline:

```
summarize → extract → find_related → update_pages → create_source_page
→ store_memories → write_baselines → write_files → update_index → append_log → finalize
```

1. **summarize** — calls summarizer agent, stores `summary`
2. **extract** — calls extraction agent, stores entities/claims/relationships
3. **find_related** — searches memory_agent (semantic), fetches related pages from baseline_store
4. **update_pages** — LLM updates each related page (score > 0.6) with new information
5. **create_source_page** — LLM writes new source summary page, suggests topic path
6. **store_memories** — writes summary + entities to memory_agent (namespace-scoped)
7. **write_baselines** — creates/updates versions in baseline_store, writes deltas
8. **write_files** — writes all changed pages as `.md` files to `WIKI_DIR`
9. **update_index** — LLM updates `index.md` with new/changed pages
10. **append_log** — appends one line to `log.md`
11. **finalize** — returns JSON summary

**Input:**
```json
{
  "input_text": "Iran resumed 60% enrichment at Fordow...",
  "source_url": "https://example.com/article",
  "source_title": "Iran Nuclear Update",
  "namespace": "wiki_geo"
}
```

**Output:**
```json
{
  "status": "ok",
  "new_page_path": "wiki.sources.2026-04-14-iran-nuclear-update",
  "pages_updated": ["wiki.geo.iran", "wiki.concepts.nuclear-program"],
  "files_written": ["/wiki/sources/2026-04-14-iran-nuclear-update.md", "/wiki/geo/iran.md"],
  "baseline_versions": {"wiki.geo.iran": 3, "wiki.sources.2026-04-14-iran-nuclear-update": 1},
  "stored_to_memory": true,
  "summary_preview": "Iran has resumed 60% uranium enrichment..."
}
```

### Query

Semantic search + LLM synthesis.

**Input:**
```json
{
  "query": "What is Iran's current nuclear posture?",
  "namespace": "wiki_geo",
  "save_as_page": true,
  "limit": 5
}
```

**Output:**
```json
{
  "query": "What is Iran's current nuclear posture?",
  "answer": "## Iran Nuclear Posture\n\nBased on wiki pages...\n\n## Sources\n- wiki.geo.iran\n- wiki.concepts.nuclear-program",
  "citations": ["wiki.geo.iran", "wiki.concepts.nuclear-program"],
  "saved_as": "wiki.queries.2026-04-14-what-is-iran-s-current-nuclear-posture"
}
```

### Lint

Health-check the wiki.

**Input:** `{}` (empty JSON object)

**Output:**
```json
{
  "orphans": ["actors/unknown-actor.md"],
  "stale": ["concepts/old-treaty.md"],
  "report": "## Contradictions\n...\n## Suggested New Pages\n...\n## Health Notes\n...",
  "report_path": "/wiki/lint-2026-04-14.md"
}
```

---

## Wiki File Structure

```
{WIKI_DIR}/
  index.md                              # catalog of all pages
  log.md                                # append-only ingest/query/lint log
  sources/
    2026-04-14-iran-nuclear-update.md   # one page per ingested source
  geo/
    iran.md
    middle_east.md
  actors/
    irgc.md
  concepts/
    nuclear_program.md
  queries/                              # saved query answers (optional)
    2026-04-14-what-is-....md
  lint-2026-04-14.md                    # lint reports
```

Topic paths use dot notation (`wiki.geo.iran`) which maps to file paths (`{WIKI_DIR}/geo/iran.md`).

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `WIKI_AGENT_URL` | `http://localhost:8011` | Externally-reachable URL |
| `WIKI_DIR` | required | Directory where .md files are written |
| `SUMMARIZER_URL` | `http://localhost:8002` | Summarizer agent |
| `EXTRACTION_URL` | `http://localhost:8004` | Extraction agent |
| `MEMORY_AGENT_URL` | `http://localhost:8009` | Memory agent |
| `BASELINE_URL` | `http://localhost:8010` | Baseline store |
| `OPENAI_API_KEY` | required | LLM for page-writing |
| `OPENAI_BASE_URL` | OpenAI default | Custom OpenAI-compatible base URL |
| `OPENAI_MODEL` | `gpt-4o-mini` | LLM model |
| `CONTROL_PLANE_URL` | optional | Self-registration |

---

## Running Locally

```bash
WIKI_DIR=./wiki \
OPENAI_API_KEY=sk-... \
python -m agents.wiki_agent.server
```

Or via `run-local.sh` (starts all components including wiki agent on port 8011).

---

## Dependencies

- **Summarizer agent** (port 8002) — text summarization
- **Extraction agent** (port 8004) — entity/claim extraction
- **Memory agent** (port 8009) — semantic search + write (pgvector + Neo4j)
- **Baseline store** (port 8010) — versioned page storage

The wiki agent degrades gracefully if sub-agents are unavailable (falls back to truncated input / empty extraction / no related pages).
