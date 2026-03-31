# Agent Optimization Recommendations

Code review findings for all agents in `agents/`. Issues are grouped by severity.

---

## Critical Bugs

### 1. Extraction Agent — `extract_using_llm` returns exception object
**File:** `agents/extraction_agent/graph.py` ~line 152
**Problem:** The `except` block ends with `return e`, returning the raw `Exception` object as the node's result. LangGraph will attempt to merge this into the state dict, causing a `TypeError` at runtime. The error is silently swallowed from the caller's perspective but corrupts the task state.
**Fix:** Replace `return e` with either `raise` (to propagate the exception) or return a proper error dict matching the state schema, e.g.:
```python
return {"error": str(e), "entities": [], "events": [], "relationships": []}
```

---

### 2. Lead Analyst — Wrong field name when building sub-agent prompt
**File:** `agents/lead_analyst/graph.py` ~line 219
**Problem:** The prompt builder calls `analysis.get("evidence")`, but the specialist agent's output schema uses the field name `evidence_cited`. This always evaluates to `None`, so the evidence section of every aggregated prompt is silently blank.
**Fix:** Change `analysis.get("evidence")` → `analysis.get("evidence_cited")`.

---

## Performance Issues

### 3. OpenAI client re-instantiated on every request
**Agents affected:** `agents/extraction_agent/`, `agents/probability_agent/`, `agents/lead_analyst/`, `agents/specialist_agent/`
**Problem:** Each of these agents constructs `AsyncOpenAI(...)` (or `langfuse.openai.AsyncOpenAI(...)`) inside a node function. This creates a new HTTPX connection pool and re-reads environment variables on every task invocation, adding unnecessary overhead that compounds under load.
**Fix:** Instantiate the client once at module level (or as a cached attribute on the executor class) and reuse it across calls:
```python
# module level
_openai_client = AsyncOpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL"),
)
```

---

### 4. Extraction Agent — `max_completion_tokens` set to unreachable value
**File:** `agents/extraction_agent/graph.py`
**Problem:** `max_completion_tokens=60000` is passed to the OpenAI API, but `gpt-4o-mini`'s hard maximum output is 16,384 tokens. The value is silently clamped by the API, but the misleadingly large number suggests this was intended to mean "unlimited" — which is not supported — and hides the real limit from maintainers.
**Fix:** Set to a realistic value that matches expected output size, e.g. `max_completion_tokens=4096`.

---

## Code Quality Issues

### 5. Extraction Agent — Debug `print()` statements in production code
**File:** `agents/extraction_agent/graph.py` ~lines 38, 62, 151
**Problem:** Raw `print()` calls dump intermediate data to stdout in production, polluting logs and potentially leaking sensitive content.
**Fix:** Replace with structured logging via the module logger (`logger.debug(...)`) or remove entirely.

---

### 6. Extraction Agent — Large block of commented-out dead code
**File:** `agents/extraction_agent/graph.py` ~lines 155–180
**Problem:** An old `format_response` function was left commented out and never removed. Dead code increases cognitive load and maintenance burden.
**Fix:** Delete the commented block. Git history preserves it if needed later.

---

### 7. Specialist Agent — Commented-out dead import
**File:** `agents/specialist_agent/graph.py` ~line 41
**Problem:** `#from openai import AsyncOpenAI` is a leftover from before the switch to Langfuse instrumentation. It adds noise with no value.
**Fix:** Delete the commented import line.

---

### 8. Probability Agent — No-op `respond` node
**File:** `agents/probability_agent/graph.py`
**Problem:** The `respond` node calls `executor.check_cancelled(task_id)` and returns `{}`. By this point `generate_briefing` has already written the final output to state. The node performs no transformation and exists only as a graph endpoint, adding an unnecessary round-trip.
**Fix:** Remove the `respond` node and update the graph edges so `generate_briefing` is the terminal node. Move the cancellation check into `generate_briefing` before it returns if needed.

---

## Observability / Consistency Issues

### 9. Inconsistent Langfuse instrumentation across agents
**Agents affected:** `agents/extraction_agent/` (uninstrumented); `agents/specialist_agent/`, `agents/lead_analyst/` (instrumented)
**Problem:** Specialist and lead analyst import `langfuse.openai.AsyncOpenAI` so their LLM calls appear in Langfuse traces. The extraction agent uses the plain `openai.AsyncOpenAI`, making its LLM calls invisible in traces — harder to debug latency and cost.
**Fix:** Replace `from openai import AsyncOpenAI` with `from langfuse.openai import AsyncOpenAI` in the extraction agent (guarded by a `try/except ImportError` fallback if Langfuse is optional).

---

### 10. Probability Agent — Duplicated `adjustments_by_scenario` logic
**File:** `agents/probability_agent/graph.py` ~lines 287–307 and 357–372
**Problem:** `aggregate_probabilities` and `detect_disagreements` both independently build a `defaultdict` structure using identical magnitude/direction math over the same data. This is a DRY violation — a future bug fix in one copy would need to be mirrored manually.
**Fix:** Extract a shared helper function (e.g. `_build_scenario_adjustments(analyses)`) and call it from both nodes.

---

### 11. Lead Analyst — Failed sub-agent results silently discarded
**File:** `agents/lead_analyst/graph.py`
**Problem:** After fan-out, results are filtered with:
```python
results = [r for r in results if not r[1].startswith("[Error")]
```
This silently drops failed sub-agent calls. There is no log statement, no counter, and no indication in the task output that some analyses are missing. When specialists fail under load or due to transient errors, the aggregation proceeds as if they never ran.
**Fix:** Log a warning for each dropped result (including agent name and error text) before filtering, and optionally surface the count of failed sub-agents in the final aggregated output.

---

*Generated 2026-03-18. Cross-reference each item against the source file and line numbers cited before applying fixes.*
