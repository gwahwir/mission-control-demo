# Lead Analyst Agent

Multi-instance orchestrator server that hosts N lead analyst agents from YAML configs. Each analyst fans out work to downstream sub-agents via A2A, collects their results in parallel, and uses an LLM meta-analyst to synthesize an aggregated report.

## Architecture

```
lead_analyst/
├── server.py              # Multi-mount FastAPI server
├── config.py              # YAML loading for analyst + sub-agent definitions
├── graph.py               # Dynamic LangGraph with N parallel sub-agent nodes
├── executor.py            # LeadAnalystExecutor (bridges A2A → LangGraph)
├── analyst_configs/       # YAML config files (one per analyst)
│   └── lead_analyst.yaml  # Default analyst (type_id: lead-analyst)
└── prompts/               # Custom aggregation prompts (referenced via aggregation_prompt_file)
```

## Graph

```
receive → call_<sub_agent_1> ─┐
        → call_<sub_agent_2> ─┤→ aggregate → respond
        → ...                 ─┤
        → call_<sub_agent_N> ─┘
```

- **receive** — reads and validates input
- **call_\<sub_agent\>** — one per sub-agent in the YAML config, all fan out in parallel
- **aggregate** — LLM-powered meta-analysis that synthesizes sub-agent results
- **respond** — formats the final output

## YAML Configuration

Each analyst is defined by a YAML file in `analyst_configs/`. The type ID is auto-derived from the filename (e.g., `geopolitical_analyst.yaml` → `geopolitical-analyst`).

```yaml
name: Geopolitical Lead Analyst
description: Fans out to ASEAN, Realist IR, and Antifragile specialists

# Optional overrides
version: "0.1.0"
model: null                       # falls back to OPENAI_MODEL env
temperature: 0.3
max_completion_tokens: 4096

# Optional custom aggregation prompt (inline or file)
aggregation_prompt: null
aggregation_prompt_file: null     # relative to agents/lead_analyst/prompts/

# Required: at least one sub-agent
sub_agents:
  - label: ASEAN Security Analyst
    url: http://localhost:8006/asean-security
  - label: Realist IR Analyst
    url: http://localhost:8006/realist-ir

# Optional dashboard metadata
skills: []
input_fields:
  - name: text
    label: Analysis Request
    type: textarea
    required: true
    placeholder: Enter the text or request...
```

## Running

```bash
OPENAI_API_KEY=sk-... python -m agents.lead_analyst.server
```

Default port: **8005**

Each analyst is mounted at `/{type_id}/` (e.g., `/lead-analyst/`).

- `GET /` — lists all mounted analysts
- `GET /{type_id}/.well-known/agent-card.json` — agent card
- `GET /{type_id}/graph` — graph topology with downstream agents

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `LEAD_ANALYST_AGENT_URL` | No | `http://localhost:8005` | This server's externally-reachable base URL |
| `CONTROL_PLANE_URL` | No | — | Control plane URL for self-registration/deregistration |
| `OPENAI_API_KEY` | No | — | Required for LLM-powered aggregation (falls back to simple concatenation without it) |
| `OPENAI_BASE_URL` | No | OpenAI default | Custom OpenAI-compatible base URL |
| `OPENAI_MODEL` | No | `gpt-4o-mini` | Default LLM model for aggregation (can be overridden per-analyst in YAML) |

Falls back to the generic `AGENT_URL` env var if `LEAD_ANALYST_AGENT_URL` is not set.

## Output

When `OPENAI_API_KEY` is set, produces an LLM-synthesized JSON report:

```json
{
  "synthesis": "3-5 paragraph narrative integrating all perspectives",
  "perspective_comparison": {
    "convergent_points": ["where frameworks agree"],
    "divergent_points": ["where frameworks disagree"],
    "complementary_insights": ["how frameworks illuminate different dimensions"]
  },
  "key_takeaways": ["actionable insights for decision-makers"],
  "recommended_actions": ["strategic recommendations"],
  "areas_for_further_research": ["critical unknowns"]
}
```

Without `OPENAI_API_KEY`, falls back to simple concatenation of sub-agent outputs.
