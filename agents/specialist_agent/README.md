# Specialist Agent

Multi-agent-per-deployment server that hosts multiple LLM-based specialist agents from a single process. Each specialist is defined by a YAML file in the `agent_cards/` directory.

## Architecture

```
specialist_agent/
├── server.py          # FastAPI server, mounts all specialists
├── config.py          # YAML loading & SpecialistConfig dataclass
├── graph.py           # Generic parameterized LangGraph
├── executor.py        # SpecialistExecutor (bridges A2A → LangGraph)
├── agent_cards/       # YAML definitions (one per specialist)
│   ├── text_analyst.yaml
│   ├── code_reviewer.yaml
│   └── ... (14 analytical framework agents)
└── prompts/           # External .md prompt files for large system prompts
    ├── taleb_antifragile.md
    ├── realist_ir.md
    └── ... (14 analytical framework prompts)
```

Each YAML file produces an independent A2A agent with its own:
- Sub-path (e.g. `/code-reviewer/`)
- Agent card at `/{type_id}/.well-known/agent-card.json`
- JSON-RPC endpoint at `/{type_id}/`
- Graph introspection at `/{type_id}/graph`
- Control plane registration (appears as a separate agent on the dashboard)
- In-memory task store (no task ID collisions between specialists)

## YAML Schema

```yaml
type_id: code-reviewer              # sub-path & control plane type ID (derived from filename if omitted)
name: Code Reviewer Agent           # required
description: Reviews code for quality and bugs
version: "0.1.0"                    # optional, default "0.1.0"

skills:                             # optional
  - id: review-code
    name: Code Review
    description: Analyzes code for issues
    tags: [code, review]

input_fields:                       # optional, rendered in the dashboard
  - name: text
    label: Code to Review
    type: textarea
    required: true
    placeholder: Paste code to review...
  # Note: a key_questions field is automatically appended to all specialists
  # by the server at startup — no need to define it in YAML.

# LLM settings — provide system_prompt OR system_prompt_file, not both
system_prompt: |                    # inline prompt (for short prompts)
  You are an expert code reviewer...
system_prompt_file: my_prompt.md    # path to .md file in prompts/ dir (for large prompts)
output_format: |                    # optional, appended to user message requesting structured output
  Respond as JSON with keys: ...
model: gpt-4o-mini                  # optional, falls back to OPENAI_MODEL env var
temperature: 0.3                    # optional, default 0.3
max_completion_tokens: 1024                    # optional, default 1024
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `SPECIALIST_AGENT_PORT` | `8006` | Port to listen on |
| `SPECIALIST_AGENT_URL` | `http://localhost:8006` | Externally-reachable base URL |
| `AGENT_URL` | — | Fallback base URL |
| `CONTROL_PLANE_URL` | — | Control plane URL for self-registration |
| `OPENAI_API_KEY` | — | Required for LLM calls |
| `OPENAI_BASE_URL` | OpenAI default | Custom OpenAI-compatible base URL |
| `OPENAI_MODEL` | `gpt-4o-mini` | Default model (overridden per-specialist via YAML `model` field) |

## Adding a New Specialist

1. Create a new YAML file in `agent_cards/` (e.g. `my_agent.yaml`)
2. Define at minimum: `name` and `system_prompt`
3. Restart the server — the new specialist is auto-discovered and mounted

The `type_id` is derived from the filename if not specified (`my_agent.yaml` → `my-agent`).

### Analytical Framework Agents

The specialist agent hosts 14 analytical framework agents (Taleb Antifragile, Realist IR, Behavioral Economics, etc.) defined via YAML configs that reference external `.md` prompt files in `prompts/`. Each produces structured JSON analysis output via the `output_format` field. These agents use `max_completion_tokens: 4096` for detailed analytical responses.

## Running

```bash
# Standalone
OPENAI_API_KEY=sk-... python -m agents.specialist_agent.server

# With control plane registration
CONTROL_PLANE_URL=http://localhost:8000 \
OPENAI_API_KEY=sk-... \
python -m agents.specialist_agent.server

# Docker
docker compose up specialist
```

## Input Format

Specialists accept either plain text or a JSON object:

```json
{ "text": "document to analyse...", "key_questions": "What are the key risks?" }
```

If the input is valid JSON with a `text` field, `key_questions` is extracted and appended to the user message before the LLM call. Plain text input is also accepted for backward compatibility (no JSON parsing required).

When called via Lead Analyst, the JSON format is always used so `key_questions` is passed as a named field rather than embedded in the text body.

## Graph

```
[process] → [respond]
```

- **process**: Parses input (JSON or plain text), appends `key_questions` if present, calls the LLM with the specialist's system prompt
- **respond**: Copies the LLM response to output

Both nodes support cancellation via `check_cancelled(task_id)`.
