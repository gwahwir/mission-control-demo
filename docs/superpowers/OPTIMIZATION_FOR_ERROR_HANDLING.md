# Agent Error Handling Optimization Plan

Concrete fixes for every error handling gap found across all agents. Each item includes the affected file, the problem, and the exact change to make.

---

## How Errors Currently Flow

```
Node raises exception
  → base/executor.py:195  except Exception as exc  (catches everything)
      → emits TaskStatus "failed" with traceback string
          → A2A returns 200 with failed status to caller
```

`asyncio.CancelledError` is caught separately at line 186 and emits `canceled`. Every other failure — API error, timeout, programming bug, bad JSON — is collapsed into one undifferentiated `failed` bucket with no retry, no classification, and no structured logging.

---

## Severity 1 — State Corruption

### 1.1 Extraction Agent — `return e` in except block
**File:** `agents/extraction_agent/graph.py` ~line 152
**Problem:** The outer `except Exception as e:` returns the raw `Exception` object as the node's return value. LangGraph tries to merge this into the typed state dict and raises a `TypeError`, corrupting the task.
**Fix:** Return a valid error state dict instead:
```python
except Exception as e:
    logger.error("extract_using_llm failed: %s", e, exc_info=True)
    return {
        "entities": [],
        "events": [],
        "relationships": [],
        "error": str(e),
    }
```

---

## Severity 2 — Unprotected LLM Calls (Task Crashes)

Every agent that calls OpenAI without a try/except will propagate the raw exception up to the base executor, which formats it as a failed task with a raw traceback. Wrap each call with specific exception handling so failures are logged with context and return a usable degraded state.

### 2.1 Summarizer Agent — No error handling around LLM call
**File:** `agents/summarizer/graph.py` ~lines 27–63
**Problem:** `openai_client.chat.completions.create()` is called bare. Any API error (rate limit, bad key, network timeout) crashes the task.
**Fix:**
```python
try:
    response = await openai_client.chat.completions.create(...)
    content = response.choices[0].message.content or ""
except openai.RateLimitError as e:
    logger.warning("Summarizer rate limited, task_id=%s: %s", task_id, e)
    return {"summary": "[Rate limit reached — retry later]", "error": str(e)}
except openai.APIError as e:
    logger.error("Summarizer OpenAI API error, task_id=%s: %s", task_id, e, exc_info=True)
    return {"summary": "[LLM unavailable]", "error": str(e)}
```
Also guard against a `None` content value: `content = (response.choices[0].message.content or "").strip()`

### 2.2 Specialist Agent — No error handling around LLM call
**File:** `agents/specialist_agent/graph.py` ~lines 57–68
**Problem:** Same pattern as summarizer — unprotected `create()` call. One specialist failure kills the whole task, which in turn drops a result from the lead analyst fan-out.
**Fix:** Wrap with the same `RateLimitError` / `APIError` pattern. Return a partial result dict with an `error` key so the lead analyst can log and skip it gracefully rather than crashing.

### 2.3 Probability Agent — `_llm_call` helper has no error handling
**File:** `agents/probability_agent/graph.py` ~lines 196–221
**Problem:** `_llm_call()` is called by `parse_assessments`, `aggregate_probabilities`, `detect_disagreements`, `scan_periphery`, and `generate_briefing`. None of these have a fallback. A single transient API error breaks the entire pipeline.
**Fix:** Add try/except inside `_llm_call()` itself so every caller gets consistent protection:
```python
async def _llm_call(client, model, system_prompt, user_content, task_id):
    try:
        response = await client.chat.completions.create(...)
        return response.choices[0].message.content or ""
    except openai.RateLimitError as e:
        logger.warning("Probability agent rate limited, task_id=%s: %s", task_id, e)
        raise  # let the node decide whether to retry or degrade
    except openai.APIError as e:
        logger.error("Probability agent API error, task_id=%s: %s", task_id, e, exc_info=True)
        raise
```
Then each node can catch the re-raised exception and return a degraded state rather than crashing entirely.

### 2.4 Lead Analyst — No error handling around aggregation LLM call
**File:** `agents/lead_analyst/graph.py` ~lines 279–288
**Problem:** The final aggregation `create()` call is unprotected. If the API fails after all sub-agents have completed successfully, the entire task is lost.
**Fix:** Wrap in try/except and fall back to concatenating sub-agent outputs directly (the no-API-key path already implements this — just route to it on error too):
```python
try:
    response = await client.chat.completions.create(...)
    aggregated = response.choices[0].message.content or ""
except openai.APIError as e:
    logger.error("Lead analyst aggregation LLM failed, falling back to concat: %s", e)
    aggregated = "\n\n---\n\n".join(text for _, text in valid_results)
```

---

## Severity 3 — Missing Input / Config Validation

### 3.1 Lead Analyst Config — `yaml.safe_load` not guarded
**File:** `agents/lead_analyst/config.py` ~line 76
**Problem:** Malformed YAML raises `yaml.YAMLError` which is not caught. The server crashes on startup with an unhelpful traceback rather than a clear config error message.
**Fix:**
```python
try:
    raw = yaml.safe_load(path.read_text())
except yaml.YAMLError as e:
    raise ValueError(f"Invalid YAML in {path}: {e}") from e
```

### 3.2 Specialist Agent Config — `yaml.safe_load` and file read not guarded
**File:** `agents/specialist_agent/config.py` ~lines 54, 75
**Problem:** Same as 3.1, plus `prompt_path.read_text()` at line 75 will raise `FileNotFoundError` if the prompt file is missing — again with no contextual message.
**Fix:**
```python
try:
    raw = yaml.safe_load(config_path.read_text())
except yaml.YAMLError as e:
    raise ValueError(f"Invalid YAML in {config_path}: {e}") from e

try:
    prompt = prompt_path.read_text()
except FileNotFoundError:
    raise ValueError(f"Prompt file not found: {prompt_path}") from None
```

### 3.3 Probability Agent — `_parse_json_safe` swallows all parse errors silently
**File:** `agents/probability_agent/graph.py` ~lines 224–232
**Problem:** The helper strips markdown fences and calls `json.loads()`, but if the second parse attempt also fails, the bare `json.JSONDecodeError` bubbles up uncaught into whichever node called it.
**Fix:** Add a final fallback and log the failure:
```python
def _parse_json_safe(text: str, task_id: str = "") -> Any:
    text = text.strip()
    for attempt in [text, re.sub(r"^```[a-z]*\n?|\n?```$", "", text, flags=re.DOTALL).strip()]:
        try:
            return json.loads(attempt)
        except json.JSONDecodeError:
            continue
    logger.warning("_parse_json_safe could not parse response, task_id=%s", task_id)
    return {}  # or [] depending on expected type — callers should check for empty result
```

### 3.4 Echo Agent — `client.close()` in finally block not guarded
**File:** `agents/echo/graph.py` ~line 68
**Problem:** `await client.close()` in the `finally:` block can itself raise, masking the original exception from the `try:` block.
**Fix:**
```python
finally:
    try:
        await client.close()
    except Exception:
        pass  # cleanup failure should not hide the original error
```

---

## Severity 4 — Silent Fallbacks That Hide Failures

### 4.1 Lead Analyst — Failed sub-agent results silently dropped
**File:** `agents/lead_analyst/graph.py` (fan-out aggregation)
**Problem:** Error results are filtered out with a list comprehension but never logged. When analysts fail under load, the aggregation silently produces a partial result with no indication of what's missing.
**Fix:** Log before filtering:
```python
for label, text in results:
    if text.startswith("[Error"):
        logger.warning("Sub-agent %s failed in task %s: %s", label, task_id, text)

valid_results = [(label, text) for label, text in results if not text.startswith("[Error")]
```
Optionally include the count of failures in the aggregated output so the caller knows the result is partial.

### 4.2 Summarizer — Silent empty string when LLM returns `None` content
**File:** `agents/summarizer/graph.py` ~line 62
**Problem:** `response.choices[0].message.content` can legitimately be `None` (e.g. content filter triggered). The current code returns `""` silently, which looks like a successful empty summary.
**Fix:**
```python
content = response.choices[0].message.content
if content is None:
    logger.warning("Summarizer received None content from LLM (possible content filter), task_id=%s", task_id)
    return {"summary": "[No content returned by LLM]"}
```

### 4.3 Relevancy Agent — LLM call itself not in try block
**File:** `agents/relevancy/graph.py` ~lines 64–84
**Problem:** The JSON parsing of the LLM response has a try/except, but the `create()` call preceding it does not. A `RetryPolicy(max_attempts=3)` is applied at the node level, which will retry on exceptions — but the retries happen silently with no log entry per attempt.
**Fix:** Wrap the `create()` call and log each caught exception before re-raising so the RetryPolicy retries are visible:
```python
try:
    response = await openai_client.chat.completions.create(...)
except openai.APIError as e:
    logger.warning("Relevancy LLM call failed (will retry), task_id=%s: %s", task_id, e)
    raise  # RetryPolicy will catch and retry
```

---

## Cross-Cutting Recommendations

### R1 — Add explicit timeouts to all OpenAI calls
No agent sets `timeout=` explicitly (except extraction at 300s). AsyncOpenAI's default is effectively unlimited in some versions. Set a consistent bound:
```python
# At client construction (affects all calls)
client = AsyncOpenAI(api_key=..., timeout=httpx.Timeout(60.0, connect=10.0))
```
Or per-call: `await client.chat.completions.create(..., timeout=60)`

### R2 — Add LangGraph `RetryPolicy` to all LLM-call nodes
Relevancy and extraction already use `RetryPolicy`. Apply it uniformly:
```python
from langgraph.pregel import RetryPolicy

builder.add_node(
    "summarize",
    summarize,
    retry=RetryPolicy(max_attempts=3, initial_interval=1.0, backoff_factor=2.0),
)
```
This gives transient API errors (429, 503) an automatic second chance without any per-node retry boilerplate.

### R3 — Distinguish error categories in task failure messages
The base executor emits a single `failed` status with a raw traceback. Consider classifying errors before they reach the executor so the task output message is human-readable:
- `openai.RateLimitError` → `"LLM rate limit reached — retry after backoff"`
- `openai.AuthenticationError` → `"Invalid API key configured"`
- `asyncio.TimeoutError` → `"LLM call timed out"`
- `json.JSONDecodeError` → `"Could not parse LLM response as JSON"`
- Everything else → existing traceback format

### R4 — Replace all `print()` debug statements with structured logging
**File:** `agents/extraction_agent/graph.py` ~lines 38, 62, 142, 151
All agents should use the module logger (`logger = logging.getLogger(__name__)`). Debug prints bypass log level configuration and can expose raw task data in production stdout.

### R5 — Validate LLM response structure before indexing
All agents assume `response.choices[0].message.content` exists. Add a helper:
```python
def _extract_content(response) -> str:
    if not response.choices:
        raise ValueError("LLM returned empty choices list")
    return response.choices[0].message.content or ""
```
Use this in every agent instead of direct indexing.

---

## Priority Order for Implementation

| Priority | Item | Agent | Impact |
|---|---|---|---|
| P0 | 1.1 — `return e` state corruption | extraction | Runtime crash |
| P1 | 2.3 — `_llm_call` unprotected | probability | Full pipeline crash |
| P1 | 2.2 — LLM call unprotected | specialist | Sub-agent crash cascades |
| P2 | 2.1 — LLM call unprotected | summarizer | Task crash |
| P2 | 2.4 — Aggregation LLM unprotected | lead_analyst | Drops completed work |
| P2 | R1 — Explicit timeouts | all | Prevents hung tasks |
| P3 | 3.3 — `_parse_json_safe` bubbles | probability | Node crash on bad LLM output |
| P3 | 4.1 — Silent sub-agent drop | lead_analyst | Invisible partial results |
| P3 | 3.1 / 3.2 — Config YAML unguarded | lead_analyst, specialist | Startup crash |
| P4 | R2 — Add RetryPolicy everywhere | summarizer, specialist, probability, lead_analyst | Resilience to transient errors |
| P4 | 4.2 / 4.3 — Silent fallbacks | summarizer, relevancy | Hidden failures |
| P5 | R4 — Replace print() with logging | extraction | Observability |
| P5 | R3 — Classify error types | all | Debuggability |
| P5 | R5 — Validate response structure | all | Defensive coding |

---

*Generated 2026-03-18. Cross-reference each item against the source file and line numbers cited before applying fixes.*
