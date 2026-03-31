# Output Schema Validation Per Agent — Design Spec

**Date:** 2026-03-31
**Status:** Draft
**Scope:** Cross-cutting — affects `agents/base/`, all agent `graph.py` files, `control_plane/routes.py`

---

## Problem

Agents produce outputs that are consumed by other agents and the dashboard, but there is no enforced contract on what shape those outputs take. Three failure modes occur silently today:

1. **LLM format drift** — An LLM changes how it formats a JSON object (e.g., omits a required field, changes a key name). Downstream consumers receive malformed data without any signal that something went wrong.
2. **Schema evolution without coordination** — A developer changes an agent's output format without updating the consumers. The task completes with `state: completed` and a structurally invalid payload.
3. **Partial node output** — A graph node emits a `NODE_OUTPUT` event with a JSON blob that doesn't match what the dashboard or downstream agents expect. The control plane stores it without complaint.

The result: tasks succeed but produce garbage. The dashboard renders incorrectly, downstream agent calls silently receive bad data, and there is no metric or alert to surface the problem.

---

## Goals

1. Define a JSON Schema for each agent's output (and per-node outputs where useful).
2. Validate agent outputs at the **agent side** before emitting them — so violations are caught at the source.
3. Validate again at the **control plane** when parsing the final `output_text` — so invalid outputs from agents that do not validate themselves are still caught.
4. On validation failure: log a structured warning, increment a metric, and default to `warn` mode (task completes with a flag). A `strict` mode (fail the task) is opt-in per agent.
5. Keep schemas close to the agent code — stored as `output_schema.json` alongside each agent's source files.

---

## Non-Goals

- Input schema validation (input is already validated via Pydantic `TaskRequest` at the control plane boundary).
- Runtime schema evolution / migrations (out of scope — treated as a deployment concern).
- Validating the internal LangGraph state between nodes (only the final output and explicit `NODE_OUTPUT` events are validated).

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│ Agent process                                                        │
│                                                                      │
│  LangGraph nodes → format_output() ──→ OutputValidator.validate()   │
│                                              │                       │
│                    schema loaded from        │ warn/strict           │
│                    agents/{name}/output_schema.json                  │
│                              │               │                       │
│                              └───────────────▼                       │
│                                     emit NODE_OUTPUT / final text    │
└──────────────────────────────────────────────────────────────────────┘
                │
                ▼  (SSE stream to control plane)
┌──────────────────────────────────────────────────────────────────────┐
│ Control plane _stream_agent()                                        │
│                                                                      │
│  parse final output_text ──→ OutputValidator.validate(agent_id)     │
│  parse NODE_OUTPUT blobs ──→ OutputValidator.validate(agent_id, node)│
│                                    │                                 │
│                          log + increment mc_output_schema_violations │
└──────────────────────────────────────────────────────────────────────┘
```

### Two-layer validation

| Layer | Location | When | On failure |
|---|---|---|---|
| **Agent-side** | `agents/base/executor.py` `format_output()` | Before emitting `completed` event | Log + metric; optionally fail task |
| **Control-plane-side** | `control_plane/routes.py` `_stream_agent()` | On receiving final `output_text` and each `NODE_OUTPUT` | Log + metric; never fail task (task is already complete from agent's perspective) |

Control-plane validation is a safety net for agents that haven't implemented agent-side validation yet, or that produce output from a code path that bypasses `format_output`.

---

## Schema Storage

Each agent that produces structured output owns a schema file:

```
agents/
  relevancy/
    output_schema.json          ← final output schema
  lead_analyst/
    output_schema.json          ← final output (aggregation result)
    node_schemas/
      aggregate.json            ← schema for aggregate NODE_OUTPUT event
      peripheral_scan.json
      ach_red_team.json
  specialist_agent/
    output_schema.json          ← per-specialist output (shared schema)
  probability_agent/
    output_schema.json          ← final briefing schema
    node_schemas/
      parse_assessments.json
      aggregate_probabilities.json
      detect_disagreements.json
  extraction_agent/
    output_schema.json
  memory_agent/
    output_schema/
      write.json                ← keyed by operation
      search.json
      traverse.json
  knowledge_graph/
    output_schema.json
  echo/
    output_schema.json
  summarizer/
    output_schema.json          ← minimal: {"output": "string"}
```

Agents with no structured output (free text) may omit `output_schema.json`. The validator treats a missing schema as "no validation required" — not an error.

---

## Shared Validator Component

A single `OutputValidator` class lives in `agents/base/output_validator.py`. Both agent-side and control-plane-side validation call it.

```python
# agents/base/output_validator.py

class ValidationResult:
    valid: bool
    errors: list[str]   # JSON Schema error messages
    schema_found: bool  # False if no schema file exists for this agent/node

class OutputValidator:
    def validate_output(
        self,
        agent_id: str,
        output: str,            # raw output_text string
        *,
        node_name: str | None = None,   # if validating a NODE_OUTPUT event
        strict: bool = False,
    ) -> ValidationResult:
        """
        Load schema for agent_id (and optionally node_name).
        Parse output as JSON. Validate against schema.
        Returns ValidationResult with valid=True if no schema found.
        Raises OutputValidationError if strict=True and validation fails.
        """
```

Schema loading order for a given `(agent_id, node_name)`:
1. If `node_name` is set: look for `agents/{agent_id}/node_schemas/{node_name}.json`
2. Otherwise: look for `agents/{agent_id}/output_schema.json`
3. If the agent has operation-keyed schemas (memory agent): look for `agents/{agent_id}/output_schema/{operation}.json`
4. If no schema file found: return `ValidationResult(valid=True, schema_found=False)`

The validator is stateless — it caches loaded schemas in a module-level dict keyed by file path to avoid repeated disk reads.

---

## Per-Agent Output Schemas

### Echo Agent

Trivial. Output is uppercase text — no structured schema needed.

```json
{}
```

An empty `{}` schema accepts any JSON. Since echo outputs plain text (not JSON), the agent-side validator should skip non-JSON strings rather than error. See "Non-JSON outputs" below.

### Summarizer Agent

Plain text output. No schema required. Schema file omitted.

### Relevancy Agent

Final `output_text` is JSON. Schema:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["verdict", "confidence", "reasoning"],
  "additionalProperties": false,
  "properties": {
    "verdict": {
      "type": "string",
      "enum": ["relevant", "not_relevant"]
    },
    "confidence": {
      "type": "string",
      "enum": ["high", "medium", "low"]
    },
    "reasoning": {
      "type": "string",
      "minLength": 1
    }
  }
}
```

### Extraction Agent

Final output is a JSON object with entities, events, and relationships extracted from text.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["entities", "events", "relationships"],
  "additionalProperties": true,
  "properties": {
    "entities": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["name", "type"],
        "properties": {
          "name": { "type": "string" },
          "type": { "type": "string" },
          "description": { "type": "string" }
        }
      }
    },
    "events": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["description"],
        "properties": {
          "description": { "type": "string" },
          "date": { "type": "string" },
          "actors": { "type": "array", "items": { "type": "string" } }
        }
      }
    },
    "relationships": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["subject", "predicate", "object"],
        "properties": {
          "subject": { "type": "string" },
          "predicate": { "type": "string" },
          "object": { "type": "string" }
        }
      }
    }
  }
}
```

### Specialist Agent

Each of the 22 specialists produces a free-text analysis — no universal JSON structure. The shared output schema validates only that the output is a non-empty string (not JSON). Validation mode: `warn` only.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "string",
  "minLength": 50,
  "description": "Specialist analysis — plain text, minimum 50 characters"
}
```

> **Note:** Specialist output is intentionally free text. Schema validation here acts only as a liveness check (is there a real output?), not a structure check.

### Lead Analyst Agent

Final `output_text` is JSON from the aggregation pipeline.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["synthesis", "perspective_comparison", "key_takeaways", "recommended_actions", "areas_for_further_research"],
  "additionalProperties": true,
  "properties": {
    "synthesis": {
      "type": "string",
      "minLength": 100,
      "description": "3-5 paragraph narrative integrating all perspectives"
    },
    "perspective_comparison": {
      "type": "object",
      "required": ["convergent_points", "divergent_points", "complementary_insights"],
      "properties": {
        "convergent_points": {
          "type": "array",
          "items": { "type": "string" },
          "minItems": 1
        },
        "divergent_points": {
          "type": "array",
          "items": { "type": "string" }
        },
        "complementary_insights": {
          "type": "array",
          "items": { "type": "string" }
        }
      }
    },
    "key_takeaways": {
      "type": "array",
      "items": { "type": "string" },
      "minItems": 1,
      "maxItems": 10
    },
    "recommended_actions": {
      "type": "array",
      "items": { "type": "string" },
      "minItems": 1
    },
    "areas_for_further_research": {
      "type": "array",
      "items": { "type": "string" }
    }
  }
}
```

**Node schemas** for Lead Analyst (stored in `agents/lead_analyst/node_schemas/`):

`aggregate.json` — validates the `aggregate` node's `NODE_OUTPUT` payload. Same structure as the final output schema above (the aggregate node produces the synthesis object that becomes the final output).

`peripheral_scan.json`:
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["peripheral_findings"],
  "properties": {
    "peripheral_findings": {
      "type": "string",
      "minLength": 10
    }
  }
}
```

`ach_red_team.json`:
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["ach_analysis"],
  "properties": {
    "ach_analysis": {
      "type": "string",
      "minLength": 10
    }
  }
}
```

### Probability Agent

Final `output_text` is a probability briefing:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["scenarios", "overall_confidence", "briefing_summary"],
  "additionalProperties": true,
  "properties": {
    "scenarios": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "required": ["name", "probability"],
        "properties": {
          "name": { "type": "string" },
          "probability": {
            "type": "number",
            "minimum": 0,
            "maximum": 1
          },
          "rationale": { "type": "string" },
          "confidence": {
            "type": "string",
            "enum": ["High", "Medium", "Low"]
          }
        }
      }
    },
    "overall_confidence": {
      "type": "string",
      "enum": ["High", "Medium", "Low"]
    },
    "briefing_summary": {
      "type": "string",
      "minLength": 50
    },
    "key_disagreements": {
      "type": "array",
      "items": { "type": "string" }
    },
    "peripheral_signals": {
      "type": "array",
      "items": { "type": "string" }
    }
  }
}
```

**Node schemas** for Probability Agent:

`parse_assessments.json` — validates the `parse_assessments` NODE_OUTPUT (an array of structured assessments):
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "array",
  "minItems": 1,
  "items": {
    "type": "object",
    "required": ["framework_name", "summary", "confidence_level"],
    "properties": {
      "framework_name": { "type": "string" },
      "summary": { "type": "string" },
      "key_findings": { "type": "array", "items": { "type": "string" } },
      "evidence_cited": { "type": "array", "items": { "type": "string" } },
      "scenario_adjustments": {
        "type": "array",
        "items": {
          "type": "object",
          "required": ["scenario_name", "direction", "magnitude"],
          "properties": {
            "scenario_name": { "type": "string" },
            "direction": { "type": "string", "enum": ["increase", "decrease", "neutral"] },
            "magnitude": { "type": "string", "enum": ["major", "moderate", "minor"] },
            "reasoning": { "type": "string" }
          }
        }
      },
      "confidence_level": { "type": "string", "enum": ["High", "Medium", "Low"] },
      "predictions": { "type": "array", "items": { "type": "string" } },
      "watch_variables": { "type": "object" }
    }
  }
}
```

### Knowledge Graph Agent

Output is a structured diff object:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["entities_added", "relationships_added", "narrative"],
  "additionalProperties": true,
  "properties": {
    "entities_added": {
      "type": "integer",
      "minimum": 0
    },
    "relationships_added": {
      "type": "integer",
      "minimum": 0
    },
    "narrative": {
      "type": "string"
    },
    "entities": {
      "type": "array",
      "items": { "type": "object" }
    }
  }
}
```

### Memory Agent

Three operation-keyed schemas:

**`write.json`:**
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["stored", "namespace"],
  "properties": {
    "stored": { "type": "boolean" },
    "namespace": { "type": "string" },
    "entities_added": { "type": "integer", "minimum": 0 },
    "relationships_added": { "type": "integer", "minimum": 0 },
    "memories_updated": { "type": "integer", "minimum": 0 },
    "memories_deleted": { "type": "integer", "minimum": 0 }
  }
}
```

**`search.json`:**
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["results"],
  "properties": {
    "results": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["content", "score"],
        "properties": {
          "content": { "type": "string" },
          "score": { "type": "number", "minimum": 0, "maximum": 1 },
          "metadata": { "type": "object" }
        }
      }
    }
  }
}
```

**`traverse.json`:**
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["nodes", "edges"],
  "properties": {
    "nodes": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["name", "type"],
        "properties": {
          "name": { "type": "string" },
          "type": { "type": "string" },
          "namespace": { "type": "string" }
        }
      }
    },
    "edges": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["subject", "predicate", "object"],
        "properties": {
          "subject": { "type": "string" },
          "predicate": { "type": "string" },
          "object": { "type": "string" },
          "namespace": { "type": "string" }
        }
      }
    }
  }
}
```

---

## Non-JSON Outputs

Several agents (Summarizer, Specialist) produce plain-text outputs, not JSON. The validator must detect this case and skip JSON parsing. Detection rule:

> If `output_schema.json` declares `"type": "string"` at the root, validate the raw string directly without JSON parsing.
> If the schema expects an object or array but the output does not start with `{` or `[`, log a warning that the output is not JSON and skip validation (do not raise).

This prevents false positives on plain-text agents that occasionally get fed through the validator.

---

## Validation Mode

Each agent declares its validation mode via an optional `validation_mode` field at the top level of `output_schema.json`:

```json
{
  "validation_mode": "warn",
  "$schema": "...",
  "type": "object",
  ...
}
```

| Mode | Behavior on failure |
|---|---|
| `warn` (default) | Log structured warning + increment `mc_output_schema_violations_total`; task completes normally |
| `strict` | Log + metric + fail the task (`record.state = FAILED`, `record.error = "Output schema violation: ..."`) |

`strict` mode is appropriate for agents whose output feeds directly into another agent as structured input (e.g., Relevancy feeding into a classifier downstream). `warn` mode is appropriate during a schema rollout period or for free-text agents.

Default when `validation_mode` is absent: `warn`.

---

## Control Plane Integration

### New metric in `control_plane/metrics.py`

```python
output_schema_violations = Counter(
    "mc_output_schema_violations_total",
    "Agent output schema validation failures",
    ["agent_id", "node_name", "mode"],  # mode = warn | strict
)
```

### Changes to `control_plane/routes.py`

In `_stream_agent`, after parsing each `NODE_OUTPUT` event:

```python
# After extracting node_name and json_payload:
from agents.base.output_validator import OutputValidator
_validator = OutputValidator()  # module-level singleton

validation = _validator.validate_output(agent_id, json_payload, node_name=node_name)
if not validation.valid:
    logger.warning(
        "node_output_schema_violation",
        agent_id=agent_id,
        task_id=task_id,
        node=node_name,
        errors=validation.errors,
    )
    output_schema_violations.labels(agent_id=agent_id, node_name=node_name, mode="warn").inc()
```

In `_stream_agent`, after the SSE loop terminates with `state: completed`:

```python
validation = _validator.validate_output(agent_id, record.output_text)
if not validation.valid:
    logger.warning(
        "output_schema_violation",
        agent_id=agent_id,
        task_id=task_id,
        errors=validation.errors,
    )
    output_schema_violations.labels(agent_id=agent_id, node_name="__final__", mode="warn").inc()
```

The control plane always uses `warn` mode regardless of the agent's declared mode — it has no authority to retroactively fail a task that the agent already completed.

### Changes to `agents/base/executor.py`

In `format_output()` (or immediately after, in `execute()`):

```python
from agents.base.output_validator import OutputValidator

_validator = OutputValidator()

# After: output_text = self.format_output(result)
validation = _validator.validate_output(self.agent_id, output_text)
if not validation.valid:
    logger.warning("output_schema_violation", agent=self.agent_id, errors=validation.errors)
    if validation.mode == "strict":
        raise OutputValidationError(f"Output schema violation: {validation.errors}")
```

This requires agents to expose `self.agent_id` — currently they do not. The `agent_id` is the `type_id` from the agent card YAML. See "Agent ID propagation" below.

---

## Agent ID Propagation

Currently `LangGraphA2AExecutor` has no `agent_id` attribute. It must be added so the validator knows which schema to load.

Each agent's `executor.py` subclass already calls `super().__init__()`. The base class `__init__` signature should accept `agent_id`:

```python
class LangGraphA2AExecutor(AgentExecutor, CancellableMixin):
    def __init__(self, agent_id: str) -> None:
        CancellableMixin.__init__(self)
        self.agent_id = agent_id
        self._graph = None
        self._topology = None
        self._running_tasks = {}
```

Each concrete executor passes its type ID:

```python
# agents/relevancy/executor.py
class RelevancyExecutor(LangGraphA2AExecutor):
    def __init__(self):
        super().__init__(agent_id="relevancy")
```

For multi-agent executors (specialist, lead analyst) that host multiple `type_id`s, `agent_id` is set per-instance:

```python
# agents/specialist_agent/executor.py
class SpecialistExecutor(LangGraphA2AExecutor):
    def __init__(self, config: SpecialistConfig):
        super().__init__(agent_id=config.type_id)
        self._config = config
```

---

## Schema Discovery at Runtime

`OutputValidator` resolves schema paths relative to the package root using `importlib.resources` or direct path construction:

```python
import pathlib

_AGENTS_ROOT = pathlib.Path(__file__).parent.parent  # agents/

def _schema_path(agent_id: str, node_name: str | None) -> pathlib.Path | None:
    base = _AGENTS_ROOT / agent_id
    if node_name:
        p = base / "node_schemas" / f"{node_name}.json"
        if p.exists():
            return p
    p = base / "output_schema.json"
    if p.exists():
        return p
    # Operation-keyed schemas (memory agent)
    # Caller passes node_name=operation for these
    return None
```

Schema files are loaded and cached on first access. The cache key is the absolute path string.

---

## Dependencies

Add to `requirements.txt`:

```
jsonschema==4.23.0
```

`jsonschema` is the standard Python JSON Schema validator. No other new dependencies required.

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| Schema file does not exist for agent | `ValidationResult(valid=True, schema_found=False)` — no error |
| Output is not valid JSON but schema expects object/array | Log warning "output is not JSON"; skip validation; return `valid=True` |
| Output is valid JSON but fails schema | Return `valid=False, errors=[...]` |
| Schema file is malformed JSON | Log `schema_load_error`; skip validation; return `valid=True` |
| `jsonschema` raises unexpected exception | Catch, log, return `valid=True` (never block a task due to validator bug) |

The validator must never raise an unhandled exception. All errors degrade gracefully to "skip validation."

---

## Testing

File: `tests/test_output_validator.py`

| Test | What is asserted |
|---|---|
| Valid relevancy output passes schema | `valid=True` |
| Relevancy output missing `verdict` fails schema | `valid=False`, errors mention `verdict` |
| Invalid `verdict` enum value fails schema | `valid=False`, errors mention enum |
| Lead analyst output with empty `key_takeaways` fails | `valid=False`, `minItems` violation |
| Valid lead analyst output passes | `valid=True` |
| Agent with no schema file returns `valid=True, schema_found=False` | Correct defaults |
| Plain-text specialist output passes string schema | `valid=True` |
| Non-JSON output with object schema logs warning and returns `valid=True` | Graceful skip |
| Malformed schema file returns `valid=True` | Graceful degradation |
| Node schema loaded for `(lead_analyst, aggregate)` | Correct file resolved |
| Node schema for unknown node falls back to base schema | Falls back correctly |
| `strict` mode raises `OutputValidationError` on failure | Exception raised |
| `warn` mode does not raise on failure | No exception |
| Schema cache: second call for same agent does not re-read disk | Same object returned |
| Memory agent `search` operation loads `output_schema/search.json` | Correct schema |

All tests use `pytest-asyncio` (`asyncio_mode = auto`). No real agents needed — tests construct `OutputValidator` directly and pass synthetic JSON strings.

---

## Rollout Strategy

Validation ships in `warn` mode for all agents by default. The sequence:

1. **Add `jsonschema` dependency and `OutputValidator`** — no behavior change yet.
2. **Add `agent_id` to base executor** — minor refactor of all executor `__init__`.
3. **Add schema files for each agent** — no behavior change, just new JSON files.
4. **Enable warn-mode validation** at control plane (`_stream_agent`) — logs violations, increments metric. No task failures.
5. **Monitor `mc_output_schema_violations_total`** for 1–2 weeks. Fix any schemas that over-validate (false positives).
6. **Flip agents to `strict` mode one at a time** — start with Relevancy (simplest schema, binary verdict).

---

## Open Questions

1. **Schema versioning** — Should schemas be versioned (e.g., `output_schema_v2.json`) to support rolling deployments where old and new agents run simultaneously? Current proposal ignores this; treat it as a follow-on if rolling deploys become a requirement.

2. **Dashboard schema contract** — The dashboard reads `node_outputs` from task records. Should the dashboard consume typed schemas from `GET /agents/{id}/output_schemas` so it can render node outputs correctly? This would require a new control plane endpoint that proxies schema files from agents. Deferred to a separate spec.

3. **Cross-agent output→input contracts** — When Lead Analyst feeds Specialist Agent, the specialist's input is the lead analyst's output. Should input schemas be derived from the upstream agent's output schema? This is a larger "agent contract" problem that goes beyond output validation — deferred.

4. **LLM output instability** — LLMs occasionally produce well-formed JSON that technically passes the schema but is semantically meaningless (e.g., all `key_takeaways` are empty strings). Consider adding a `minLength` constraint on string array items in a future revision.
