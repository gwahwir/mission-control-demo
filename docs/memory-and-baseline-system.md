# Memory & Baseline System

This document is intended to be fully self-contained — a new Claude session should be able to understand, debug, and reimplement the memory and baseline system from this document alone.

---

## 1. Overview

The system maintains persistent, compounding intelligence knowledge across analysis runs using two complementary stores:

| | Baseline Store | Memory Agent |
|---|---|---|
| **Port** | 8010 | 8009 |
| **Type** | Plain FastAPI REST service | A2A LangGraph agent |
| **Storage** | Full narrative text, versioned | Vector embeddings + knowledge graph |
| **Purpose** | Give Lead Analyst historical context | Semantic search over wiki pages |
| **Query type** | Fetch by topic path | Embedding similarity search |
| **Used by** | Pipeline tab, Analyst Flow tab, Baselines tab, Wiki Agent | Wiki Agent only |
| **Backend** | PostgreSQL with `ltree` + `pgvector` | pgvector + Neo4j |

The key design principle: **each analysis run starts from where the last one ended**. The baseline narrative for a topic accumulates across runs — each run reads the current baseline, incorporates it into analysis, and writes back a new version.

---

## 2. Baseline Store

### Data model

**Topics** — registered topic paths with display names. Uses `ltree` for hierarchy.
```
geo.middle_east.iran  →  display_name: "Iran"
wiki.geo.iran         →  display_name: "Wiki: Iran"
```

**Versions** — append-only. Each write creates a new numbered version storing the full narrative text.
```
v1  2026-04-14 07:52  — comparison table from first pipeline run
v2  2026-04-14 08:37  — manually written test
v3  2026-04-14 09:05  — full executive summary + assessment table
```

**Deltas** — structured change log between versions:
- `from_version` / `to_version`
- `delta_summary` — prose description of what changed
- `claims_added` — new assessments introduced
- `claims_superseded` — prior assessments reversed or contradicted
- `article_metadata` — optional source provenance

### REST API

```
GET  /topics                              list all registered topics
POST /topics                              register a new topic
                                          body: {topic_path, display_name}

GET  /baselines/{topic_path}/current      fetch latest version narrative
GET  /baselines/{topic_path}/history      fetch all versions + all deltas
POST /baselines/{topic_path}/versions     write a new version
                                          body: {narrative, citations: []}
POST /baselines/{topic_path}/deltas       write a delta record
                                          body: {from_version, to_version,
                                                 delta_summary, claims_added,
                                                 claims_superseded, article_metadata}
```

### Who reads from the baseline store

**Pipeline tab ([04]) and Analyst Flow tab ([06])** — at the start of each run, `fetchBaseline(topicPath)` retrieves the current version. The `narrative` field is passed verbatim as the `baselines` input to Lead Analyst A.

**Wiki Agent `find_related` node** — after the Memory Agent returns semantic search results, each result's `topic_path` metadata is used to fetch the full narrative from `/baselines/{topic_path}/current`. Narratives are passed to `update_pages` for LLM-based page updating.

**Wiki Agent query operation** — same pattern: Memory Agent search → fetch narratives for top results → LLM synthesize answer.

**Baselines tab ([07])** — reads `/topics` to list topics, reads `/baselines/{topic_path}/history` to display all versions and deltas.

### Who writes to the baseline store

**Pipeline tab ([04])** — frontend directly calls the baseline store after Lead Analyst A completes (`writeBaselineVersion` then `writeBaselineDelta` in `dashboard/src/hooks/useApi.js`).

**Analyst Flow tab ([06])** — same write-back logic added to `AnalystFlowPage.jsx`. Triggered automatically when the analyst task completes.

**Wiki Agent `write_baselines` node** — writes a new version for every page in `updated_pages` (both new source pages and updated existing pages). Also writes a delta record for updated (non-new) pages. See section 5 for full node implementation.

### Write payload shapes

**New version:**
```json
{ "narrative": "..full markdown text..", "citations": [] }
```
Response: `{ "id": "uuid", "version_number": 3, "created_at": "..." }`

**Delta:**
```json
{
  "from_version": 2,
  "to_version": 3,
  "delta_summary": "prose description of change",
  "claims_added": ["claim text...", "..."],
  "claims_superseded": ["old claim...", "..."],
  "article_metadata": { "source": "url", "title": "Article Title" }
}
```

---

## 3. Memory Agent

### What it stores

The Memory Agent indexes content for semantic retrieval. It does **not** store full narratives — only:
- **Vector embeddings** (pgvector) of article summaries + extracted entities, scoped by namespace (e.g. `wiki_geo`)
- **Graph relationships** (Neo4j) between entities

### A2A operation dispatch

The Memory Agent (like the Wiki Agent) infers its operation from which fields are present in the JSON input:

| Fields present | Operation |
|---|---|
| `text` + optional `namespace` + optional `metadata` | **write** — embed and store |
| `query` + optional `namespace` + optional `limit` | **search** — semantic similarity |
| `entity` or `relationship` traversal fields | **traverse** — graph walk |

### Write payload (used by Wiki Agent `store_memories`)
```json
{
  "text": "summary text + entity list + source page path",
  "namespace": "wiki_geo",
  "metadata": { "topic_path": "wiki.sources.2026-04-14-iran-update" }
}
```
The `topic_path` in metadata is critical — it allows `find_related` to recover which baseline store page a memory entry corresponds to.

### Search payload (used by Wiki Agent `find_related` and query operation)
```json
{
  "query": "summary text + top entity names",
  "namespace": "wiki_geo",
  "limit": 5
}
```
Response:
```json
{
  "results": [
    { "score": 0.82, "metadata": { "topic_path": "wiki.geo.iran" }, ... },
    ...
  ]
}
```

Only results with `score >= 0.6` are passed to `update_pages` for LLM-based updating.

---

## 4. How Baseline Structure is Formed

This is the most important section for understanding quality and consistency.

### Step 1 — Lead Analyst A produces a free-form markdown report

The Lead Analyst's `final_synthesis` node calls an LLM with all specialist analyses, ACH red team output, peripheral scan findings, and the existing baseline as context. It produces a long markdown document. The structure varies per run but typically includes sections like:

```markdown
## Executive Summary
## Primary Assessment
## Baseline Change Summary
## ACH Findings
...
```

There is no enforced output schema — the LLM decides what sections to write.

### Step 2 — `extractUpdatedNarrative()` extracts the new baseline text

This function (defined in `PipelinePage.jsx` and duplicated in `AnalystFlowPage.jsx`) scans the output for specific section headers **in priority order**:

```javascript
function extractUpdatedNarrative(analysisText, oldNarrative) {
  const lines = analysisText.split("\n");

  // Priority 1-3: dedicated baseline sections
  for (const marker of ["## Updated Baseline", "## Baseline Change Summary", "## Baseline Update"]) {
    const idx = lines.findIndex((l) => l.toLowerCase().includes(marker.toLowerCase()));
    if (idx !== -1) {
      const section = [];
      for (let i = idx + 1; i < lines.length; i++) {
        if (lines[i].startsWith("## ") && section.length) break;
        section.push(lines[i]);
      }
      const text = section.join("\n").trim();
      if (text) return text;
    }
  }

  // Priority 4-5: summary sections (prepend to old baseline)
  for (const marker of ["## Executive Summary", "## Primary Assessment"]) {
    const idx = lines.findIndex((l) => l.toLowerCase().includes(marker.toLowerCase()));
    if (idx !== -1) {
      const section = [];
      for (let i = idx + 1; i < lines.length; i++) {
        if (lines[i].startsWith("## ") && section.length) break;
        section.push(lines[i]);
      }
      const summary = section.join("\n").trim();
      if (summary) return oldNarrative
        ? `${summary}\n\n[Prior baseline]\n${oldNarrative}`
        : summary;
    }
  }

  // Fallback: first 3000 characters of entire output
  return analysisText.slice(0, 3000);
}
```

The most commonly matched header in practice is `## Baseline Change Summary` (priority 2), since the LLM rarely produces `## Updated Baseline` (priority 1) without being explicitly instructed to.

### Step 3 — `extractDeltaFields()` extracts structured change data

```javascript
function extractDeltaFields(analysisText) {
  const lines = analysisText.split("\n");
  let sectionText = "";

  // Find a baseline/comparison section for the delta source
  for (const marker of ["Baseline Change Summary", "Baseline Comparison", "Appendix: Baseline"]) {
    const idx = lines.findIndex((l) => l.toLowerCase().includes(marker.toLowerCase()));
    if (idx !== -1) {
      const section = [];
      for (let i = idx + 1; i < lines.length; i++) {
        if (lines[i].startsWith("# ") && section.length) break;
        section.push(lines[i]);
      }
      sectionText = section.join("\n").trim();
      break;
    }
  }

  const source = sectionText || analysisText;

  // delta_summary: first substantive paragraph (max 300 chars)
  const paras = source.split("\n\n");
  const firstPara = paras.find((p) => p.trim().length > 40) ?? "";
  const deltaSummary = firstPara.trim().slice(0, 300)
    || "Baseline updated following new report analysis.";

  // claims_added / claims_superseded: keyword scan of bullet lines
  const claimsAdded = [];
  const claimsSuperseded = [];
  for (const line of source.split("\n")) {
    const stripped = line.replace(/^[•\-* ]+/, "").trim();
    if (!stripped || stripped.length < 20) continue;
    const lower = stripped.toLowerCase();
    if (["confirmed", "updated", "new signal", "new development", "added"]
        .some((kw) => lower.includes(kw))) {
      claimsAdded.push(stripped);
    } else if (["challenged", "superseded", "no longer", "reversed", "contradicted"]
        .some((kw) => lower.includes(kw))) {
      claimsSuperseded.push(stripped);
    }
  }

  return {
    deltaSummary,
    claimsAdded: claimsAdded.slice(0, 10),
    claimsSuperseded: claimsSuperseded.slice(0, 10),
  };
}
```

### Step 4 — On the next run, the stored narrative becomes the context

The stored narrative is passed verbatim as the `baselines` field to Lead Analyst A:

```javascript
// PipelinePage.jsx / AnalystFlowPage.jsx
const analystTask = await dispatchTask(leadAnalystAgent.id, {
  text: JSON.stringify({
    text: report,
    baselines: baseline?.narrative ?? "",  // whatever was last stored
    key_questions: keyQuestions,
  }),
});
```

The Lead Analyst's `call_baseline_comparison` node compares the new report against this text and produces the `## Baseline Change Summary` section — which is then extracted as the next version's narrative.

### Structural drift

Because extraction is heuristic and LLM output varies, baseline structure can drift across versions:
```
v1 → brief comparison table (## Baseline Change Summary was short)
v2 → manually written test content
v3 → full executive summary + detailed assessment table (richer run)
```

**Fix:** Add an explicit instruction to the Lead Analyst's `final_synthesis` prompt to always produce a `## Updated Baseline` section in a defined format. Since `## Updated Baseline` is the **first** header the extraction function looks for, targeting it directly gives maximum control over what gets stored. Currently the LLM never writes this exact header unprompted, causing every run to fall through to `## Baseline Change Summary` or lower.

---

## 5. Wiki Agent — Full Implementation

### Operation dispatch (executor.py)

The Wiki Agent infers its operation from which JSON fields are present in the A2A input:

```python
if input_json.get("input_text"):
    operation = "ingest"    # → runs 11-node LangGraph pipeline
elif input_json.get("query"):
    operation = "query"     # → run_query() in wiki_ops.py
else:
    operation = "lint"      # → run_lint() in wiki_ops.py
```

### WikiState (the LangGraph state schema)

```python
class WikiState(TypedDict):
    input_text: str           # raw source text to ingest
    source_url: str           # URL of the source
    source_title: str         # title for the source page
    source_metadata: dict     # arbitrary metadata
    namespace: str            # memory agent namespace (default: "wiki_geo")
    summary: str              # set by summarize node
    extracted: dict           # set by extract node: {entities, claims, relationships}
    related_pages: list[dict] # set by find_related: [{topic_path, narrative, version, score}]
    updated_pages: list[dict] # set by update_pages + create_source_page:
                              #   [{topic_path, new_content, delta_summary, is_new, from_version}]
    new_page_path: str        # set by create_source_page: dotted path of new source page
    stored_to_memory: bool    # set by store_memories
    baseline_versions: dict   # set by write_baselines: {topic_path: version_number}
    files_written: list[str]  # set by write_files: list of absolute file paths
    retry_count: int
    last_error: str
    output: str               # set by finalize: JSON string of final result
```

### 11-node ingest pipeline

```
summarize → extract → find_related → update_pages → create_source_page
→ store_memories → write_baselines → write_files → update_index
→ append_log → finalize → END
```

**Node 1: `summarize`**
Calls the summarizer agent (A2A, port 8002) with `{"text": input_text}`.
Falls back to first 500 chars of input if summarizer is unavailable.
Sets: `summary`

**Node 2: `extract`**
Calls the extraction agent (A2A, port 8004) with `{"text": input_text}`.
Returns `{entities, claims, relationships}`.
Falls back to `{}` if extraction fails.
Sets: `extracted`

**Node 3: `find_related`**
Builds a search query from `summary[:300]` + top 5 entity names.
Calls Memory Agent (A2A, port 8009) with `{"query": ..., "namespace": ..., "limit": 5}`.
For each result, fetches full narrative from baseline store at `/baselines/{topic_path}/current`.
Sets: `related_pages` — list of `{topic_path, narrative, version, score}`

**Node 4: `update_pages`**
For each related page with `score >= 0.6`, calls LLM to update the page.

LLM system prompt:
```
You are a wiki editor for a geopolitics/IR intelligence wiki.
Update the page to incorporate new information: add new facts, update outdated claims, note contradictions.
Preserve the page's existing structure and style.
Return ONLY valid JSON: {"updated_content": "...", "delta_summary": "..."}
```

User message includes: existing page (up to 2000 chars), new summary (500 chars), new extracted data (800 chars).
Sets: `updated_pages` — list of `{topic_path, new_content, delta_summary, is_new: false, from_version}`

**Node 5: `create_source_page`**
Calls LLM to write a new wiki page for the ingested source.

LLM system prompt:
```
Write a new wiki page in Markdown with sections:
  # [Title]
  **Source:** [url] | **Date:** [date]
  ## Summary
  ## Key Entities
  ## Key Claims
  ## Related Topics
Return ONLY valid JSON: {"page_content": "...", "suggested_topic_path": "..."}
suggested_topic_path format: wiki.sources.YYYY-MM-DD-slug (3-5 word slug)
```

Appends new source page to `updated_pages` with `is_new: true`.
Sets: `new_page_path`, appends to `updated_pages`

**Node 6: `store_memories`**
Builds memory text: `summary + "\n\nEntities: " + entity_list + "\n\nSource page: " + new_page_path`
Calls Memory Agent write operation with:
```json
{
  "text": "..memory text..",
  "namespace": "wiki_geo",
  "metadata": { "topic_path": "wiki.sources.2026-04-14-..." }
}
```
The `topic_path` in metadata is how `find_related` later recovers which baseline store entry to fetch.
Sets: `stored_to_memory`

**Node 7: `write_baselines`**
For every page in `updated_pages`:
1. Check if topic exists: `GET /baselines/{topic_path}/current`
2. If 404, register: `POST /topics {topic_path, display_name}`
3. Write new version: `POST /baselines/{topic_path}/versions {narrative, citations: []}`
4. For non-new pages (updated existing pages), write delta:
   `POST /baselines/{topic_path}/deltas {from_version, to_version, delta_summary, claims_added: [], claims_superseded: [], article_metadata: {source, title}}`

Sets: `baseline_versions` — `{topic_path: version_number}`

**Node 8: `write_files`**
Converts each `topic_path` to a filesystem path via `topic_path_to_file_path()`:
- Strips leading `wiki.` prefix
- Replaces `.` separators with `/`
- Appends `.md`
- Example: `wiki.geo.iran` → `{WIKI_DIR}/geo/iran.md`

Writes markdown content to disk, creating parent directories as needed.
Sets: `files_written`

**Node 9: `update_index`**
Reads current `{WIKI_DIR}/index.md`.
Calls LLM with current index + lists of new/changed page paths to produce an updated index.
Writes result back to `index.md`.

**Node 10: `append_log`**
Appends one line to `{WIKI_DIR}/log.md`:
```
- [2026-04-14T07:52:00Z] INGEST | source='Title' | new_page=wiki.sources.2026-... | pages_updated=2 | files_written=3
```

**Node 11: `finalize`**
Produces final JSON output string:
```json
{
  "status": "ok",
  "new_page_path": "wiki.sources.2026-04-14-...",
  "pages_updated": ["wiki.geo.iran", "wiki.concepts.nuclear-program"],
  "files_written": ["/wiki/sources/...", "/wiki/geo/iran.md"],
  "baseline_versions": {"wiki.geo.iran": 3, "wiki.sources.2026-...": 1},
  "stored_to_memory": true,
  "summary_preview": "first 200 chars of summary..."
}
```

### `run_query()` (wiki_ops.py)

```
1. Search Memory Agent for relevant page references
2. Fetch full narratives from baseline store for top results
3. LLM synthesize answer with inline citations
4. Optionally save answer as new wiki page under wiki.queries.*
```

Input fields: `query`, `namespace` (default `wiki_geo`), `limit` (default 5), `save_as_page` (default false)

Output:
```json
{
  "query": "...",
  "answer": "## Answer\n...\n## Sources\n- wiki.geo.iran",
  "citations": ["wiki.geo.iran", "wiki.concepts.nuclear-program"],
  "saved_as": "wiki.queries.2026-04-14-query-slug"   // only if save_as_page=true
}
```

### `run_lint()` (wiki_ops.py)

```
1. List all .md files under WIKI_DIR
2. Find orphans: pages not referenced in index.md
3. Find stale pages: not mentioned in last 30 lines of log.md
   (excludes sources/, index.md, log.md, lint-*.md)
4. LLM produces structured report with: ## Contradictions, ## Suggested New Pages, ## Health Notes
5. Write report to {WIKI_DIR}/lint-{date}.md
```

Output:
```json
{
  "orphans": ["actors/unknown-actor.md"],
  "stale": ["concepts/old-treaty.md"],
  "report": "## Contradictions\n...",
  "report_path": "/wiki/lint-2026-04-14.md"
}
```

---

## 6. Frontend Auto-Refresh Chain

The Baselines tab ([07]) auto-refreshes whenever a baseline write completes in another tab. The wiring:

**App.jsx** holds a `baselinesRefreshKey` counter and a stable `handleBaselineWritten` callback:
```javascript
const [baselinesRefreshKey, setBaselinesRefreshKey] = useState(0);
const handleBaselineWritten = useCallback(() => setBaselinesRefreshKey((k) => k + 1), []);
```

`handleBaselineWritten` is passed as `onBaselineWritten` prop to both:
- `<PipelinePage onBaselineWritten={handleBaselineWritten} />` — called after `setRunState("done")`
- `<AnalystFlowPage onBaselineWritten={handleBaselineWritten} />` — called after `setRunState("done")`

`baselinesRefreshKey` is passed as `refreshKey` prop to:
- `<BaselinesPage refreshKey={baselinesRefreshKey} />`

`BaselinesPage` includes `refreshKey` in its history-fetch `useEffect` dependency array:
```javascript
useEffect(() => {
  setHistory(null);
  loadHistory(selectedTopic);
}, [selectedTopic, loadHistory, refreshKey]);  // refreshKey triggers re-fetch
```

---

## 7. The Compounding Intelligence Loop

### Pipeline tab / Analyst Flow tab

```
New report
    │
    ▼
ensureTopicRegistered(topic, label)      POST /topics if not exists
    │
    ▼
fetchBaseline(topicPath)                 GET /baselines/{topic}/current
    │  → baseline.narrative (or "" if none)
    │
    ▼
dispatchTask(leadAnalystAgent.id, {
  text: report,
  baselines: baseline.narrative,         ← accumulated historical context
  key_questions: keyQuestions,
})
    │
    │  Lead Analyst A runs:
    │    discover_and_select → call_specialist(×N) → call_peripheral_scan
    │    → aggregate → call_ach_red_team → call_baseline_comparison
    │    → final_synthesis
    │
    ▼
extractUpdatedNarrative(output, oldNarrative)   → new narrative text
extractDeltaFields(output)                       → delta fields
    │
    ▼
writeBaselineVersion(topic, newNarrative)        POST /baselines/{topic}/versions
writeBaselineDelta(topic, {...})                 POST /baselines/{topic}/deltas
    │
    ▼
onBaselineWritten()  →  baselinesRefreshKey++  →  Baselines tab [07] re-fetches
```

### Wiki analysis pipeline (`wiki_analysis_pipeline.py`)

```
New report
    │
    ▼
Wiki Agent query:
  POST wiki-agent {"query": wikiQuery, "namespace": namespace, "limit": 5}
    │  → Memory Agent search → fetch narratives from Baseline Store
    │  → LLM synthesize answer
    │  → {answer: "...", citations: ["wiki.geo.iran", ...]}
    │
    │  answer becomes "baselines" input for Lead Analyst
    ▼
Relevancy check (optional):
  POST relevancy-agent {"text": report, "question": query + wiki context}
    │  → {relevant: true/false, confidence: 0.0-1.0}
    │  skip if not relevant or confidence < 0.5
    ▼
Lead Analyst A:
  POST lead-analyst {"text": report, "baselines": wikiAnswer, "key_questions": ...}
    │  → synthesis markdown
    ▼
Wiki Agent ingest:
  POST wiki-agent {"input_text": synthesis, "source_title": "Lead Analyst A — {date}", "namespace": namespace}
    │  runs 11-node pipeline:
    │    summarize → extract → find_related → update_pages → create_source_page
    │    → store_memories → write_baselines → write_files → update_index → append_log → finalize
    │
    ▼
Next run picks up the updated wiki pages automatically
```

---

## 8. Topic Path Namespaces

Two namespaces coexist and should be treated as **independent**:

| Namespace | Example path | Written by | Used by |
|---|---|---|---|
| `geo.*` | `geo.middle_east.iran` | Pipeline tab, Analyst Flow tab | Pipeline tab, Analyst Flow tab |
| `wiki.*` | `wiki.geo.iran` | Wiki Agent ingest | Wiki Agent query, wiki pipeline |

A Pipeline tab run on topic `geo.middle_east.iran` and a wiki pipeline run that updates `wiki.geo.iran` are **separate baseline lineages**, even if they cover the same subject.

To unify them, either:
- Point the Pipeline tab at a `wiki.*` topic path, or
- Have the wiki ingest pipeline write back to the `geo.*` path

Currently they are kept separate by convention.

---

## 9. File Structure (Wiki Agent)

```
{WIKI_DIR}/
  index.md                              — catalog of all pages (LLM-maintained)
  log.md                                — append-only ingest/query/lint log
  sources/
    2026-04-14-iran-nuclear-update.md   — one page per ingested source
  geo/
    iran.md
    middle_east.md
  actors/
    irgc.md
  concepts/
    nuclear_program.md
  queries/                              — saved query answers (save_as_page=true)
    2026-04-14-what-is-....md
  lint-2026-04-14.md                    — lint reports
```

Topic path → file path conversion (`page_writer.py`):
- Strip leading `wiki.` prefix
- Split on `.`
- Last segment becomes filename + `.md`
- All prior segments become directory path
- Example: `wiki.geo.iran` → `{WIKI_DIR}/geo/iran.md`
- Example: `wiki.sources.2026-04-14-update` → `{WIKI_DIR}/sources/2026-04-14-update.md`

---

## 10. Environment Variables

### Baseline Store

| Variable | Default | Description |
|---|---|---|
| `BASELINE_PG_DSN` | required | PostgreSQL DSN (needs `ltree` + `pgvector` extensions) |
| `BASELINE_EMBEDDING_MODEL` | required | Embedding model name for semantic search |
| `BASELINE_EMBEDDING_DIMS` | required | Vector dimensions — must match model |
| `BASELINE_PORT` | `8010` | Listen port |
| `BASELINE_URL` | `http://localhost:8010` | Used by wiki agent to reach baseline store |

### Memory Agent

| Variable | Default | Description |
|---|---|---|
| `MEMORY_NEO4J_URL` | required | Neo4j bolt URL |
| `MEMORY_NEO4J_USER` | required | Neo4j username |
| `MEMORY_NEO4J_PASSWORD` | required | Neo4j password |
| `MEMORY_PG_DSN` | required | pgvector Postgres DSN |
| `MEMORY_EMBEDDING_MODEL` | required | Embedding model name |
| `MEMORY_EMBEDDING_DIMS` | required | Vector dimensions — must match model |
| `MEMORY_AGENT_URL` | `http://localhost:8009` | Used by wiki agent to reach memory agent |

### Wiki Agent

| Variable | Default | Description |
|---|---|---|
| `WIKI_DIR` | required | Directory where `.md` files are written |
| `WIKI_AGENT_URL` | `http://localhost:8011` | Externally-reachable URL for self-registration |
| `SUMMARIZER_URL` | `http://localhost:8002` | Summarizer agent |
| `EXTRACTION_URL` | `http://localhost:8004` | Extraction agent |
| `MEMORY_AGENT_URL` | `http://localhost:8009` | Memory agent |
| `BASELINE_URL` | `http://localhost:8010` | Baseline store |
| `OPENAI_API_KEY` | required | LLM for page writing |
| `OPENAI_BASE_URL` | OpenAI default | Custom OpenAI-compatible base URL |
| `OPENAI_MODEL` | `gpt-4o-mini` | LLM model for all wiki agent LLM calls |

### Jina (embedding provider for Memory Agent and Baseline Store)

The system uses Jina embeddings (`jina-embeddings-v5-text-small` or similar). A valid Jina API key must be set — a placeholder key like `jina_xxx` will cause the Memory Agent to return a `RuntimeError: Invalid API key` as text instead of JSON, which causes `json.loads()` failures in the wiki agent's query and `find_related` nodes. Symptoms: wiki queries return empty results, `find_related` finds no related pages.

---

## 11. Known Issues and Gotchas

**Jina API key errors propagate as text** — if the Memory Agent fails due to an invalid embedding key, it returns the Python traceback as a plain text A2A response. The wiki agent's `run_query()` wraps `json.loads()` in a try/except to handle this gracefully (falls back to empty results), but `find_related` does not and will log a warning and return an empty `related_pages` list.

**Baseline structure drift** — because `extractUpdatedNarrative()` uses heuristic header matching, the structure of stored narratives can vary between versions. The most reliable fix is instructing the Lead Analyst's final synthesis prompt to always produce a `## Updated Baseline` section.

**Separate topic namespaces** — `geo.*` (Pipeline/Analyst Flow) and `wiki.*` (Wiki Agent) are separate baseline lineages even for the same subject. Running both pipelines on Iran will produce two separate baselines that don't know about each other unless the topic paths are aligned.

**Version 2 test write** — `geo.middle_east.iran` v2 contains the text "test narrative v2" written manually during debugging. This will be visible to Lead Analyst A as historical context until it is overwritten by a subsequent production run.

**Wiki agent on Docker** — `WIKI_DIR` is mounted as a named Docker volume (`wiki_data:/wiki`). To access wiki markdown files from the host (e.g. in Obsidian), the volume mount would need to be changed to a bind mount pointing to a local directory.
