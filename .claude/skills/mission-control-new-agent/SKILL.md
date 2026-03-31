---
name: mission-control-new-agent
description: Use when adding a new LangGraph A2A agent to the mission-control codebase at mission-control. Covers all required files, patterns, and registration steps.
---

# Mission Control: Adding a New Agent

## Required Files

```
agents/<name>/          # use short snake_case: "sentiment", "extractor" (no _agent suffix unless project already uses it)
├── __init__.py         # empty
├── graph.py            # LangGraph state + nodes
├── executor.py         # subclass LangGraphA2AExecutor
├── server.py           # FastAPI + A2A + lifespan + /graph
└── README.md           # required — graph diagram, env vars, I/O schema

Dockerfile.<name>       # at repo root
docker-compose.yml      # add service block
run-local.sh            # add startup entry
CLAUDE.md               # update agent table (port, type ID, description)
```

## Next Available Port

Ports 8001–8010 are taken. Use **8011+**. Check `docker-compose.yml` to confirm no collision.

## graph.py Pattern

```python
from typing import Any, TypedDict
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from langgraph.types import RetryPolicy

class YourState(TypedDict):
    input: str
    output: str

async def your_node(state: YourState, config: RunnableConfig) -> dict[str, Any]:
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    executor.check_cancelled(task_id)   # REQUIRED in every node, first line
    # ... work ...
    return {"output": "result"}

def build_graph():
    g = StateGraph(YourState)
    g.add_node("your_node", your_node,
               retry_policy=RetryPolicy(max_attempts=3, initial_interval=1.0, backoff_factor=2.0))
    g.set_entry_point("your_node")
    g.add_edge("your_node", END)
    return g.compile()
```

**Rules:**
- `executor.check_cancelled(task_id)` must be the **first line** of every node — no exceptions
- Extract `executor` and `task_id` from `config["configurable"]`
- LLM nodes: wrap in `RetryPolicy` to handle transient API errors
- Check cancelled again after any long `await` (e.g., after an OpenAI call)

## executor.py Pattern

Minimal — just override `build_graph()`:

```python
from agents.base.executor import LangGraphA2AExecutor
from agents.your_agent.graph import build_graph

class YourExecutor(LangGraphA2AExecutor):
    def build_graph(self):
        return build_graph()
```

Override `prepare_input()` only if you need to parse JSON input with multiple fields.
Override `format_output()` only if `result["output"]` isn't the right string to return.

## server.py Pattern

```python
import logging, os, sys
from contextlib import asynccontextmanager
logging.basicConfig(level=os.getenv("LOG_LEVEL","INFO").upper(),
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                    stream=sys.stdout)
logger = logging.getLogger(__name__)

import uvicorn
from a2a.server.apps.jsonrpc import A2AFastAPIApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from fastapi import FastAPI
from agents.base.registration import register_with_control_plane, deregister_from_control_plane
from agents.your_agent.executor import YourExecutor
from dotenv import load_dotenv
load_dotenv()

AGENT_TYPE = "your-agent"    # kebab-case; used as type_id in control plane
AGENT_PORT = 8011

INPUT_FIELDS = [             # dashboard renders a form from this
    {"name": "text", "label": "Input Text", "type": "textarea",
     "required": True, "placeholder": "..."},
]

agent_card = AgentCard(
    name="Your Agent", description="What it does.", version="0.1.0",
    url=f"http://localhost:{AGENT_PORT}",
    capabilities=AgentCapabilities(streaming=True, push_notifications=False),
    default_input_modes=["text/plain"],
    default_output_modes=["text/plain"],
    skills=[AgentSkill(id="skill-1", name="Skill", description="...", tags=[])],
)

@asynccontextmanager
async def lifespan(app):
    url = os.getenv("YOUR_AGENT_URL", os.getenv("AGENT_URL", f"http://localhost:{AGENT_PORT}"))
    await register_with_control_plane(AGENT_TYPE, url)
    yield
    await deregister_from_control_plane(AGENT_TYPE, url)

def create_app():
    app = FastAPI(title="Your Agent", lifespan=lifespan)
    executor = YourExecutor()
    handler = DefaultRequestHandler(agent_executor=executor, task_store=InMemoryTaskStore())
    A2AFastAPIApplication(agent_card=agent_card, http_handler=handler).add_routes_to_app(app)

    @app.get("/graph")
    async def get_graph():
        t = executor.get_graph_topology()
        t["input_fields"] = INPUT_FIELDS
        return t

    return app

app = create_app()
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=AGENT_PORT)
```

**Env var naming:** `YOUR_AGENT_URL` → e.g., `SENTIMENT_AGENT_URL`, `ECHO_AGENT_URL`. Pattern: `{SCREAMING_SNAKE_AGENT_NAME}_URL`.

## Registration

`register_with_control_plane()` is called in lifespan — it retries 5× with exponential backoff. No-op if `CONTROL_PLANE_URL` env var is unset (agent works standalone). Deregistration retries 3× and logs a warning on failure (control plane detects dead agents via 30s health poll).

## Cancellation

Already handled by `LangGraphA2AExecutor`. Your only responsibility: call `executor.check_cancelled(task_id)` at the top of every graph node. `CancelledError` propagates up, executor emits `TaskState.canceled` and cleans up.

## Dockerfile

```dockerfile
FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY agents/ agents/
COPY control_plane/ control_plane/
EXPOSE 8011
CMD ["python", "-m", "agents.your_agent.server"]
```

## docker-compose.yml Service Block

```yaml
your-agent:
  build:
    context: .
    dockerfile: Dockerfile.your-agent
  ports:
    - "8011:8011"
  environment:
    - LOG_LEVEL=INFO
    - CONTROL_PLANE_URL=http://control-plane:8000
    - YOUR_AGENT_URL=http://your-agent:8011
    - OPENAI_API_KEY=${OPENAI_API_KEY}
    - OPENAI_MODEL=${OPENAI_MODEL:-gpt-4o-mini}
    - OPENAI_BASE_URL=${OPENAI_BASE_URL:-https://openrouter.ai/api/v1}
  env_file: ".env"
  networks:
    - mc-net
  depends_on:
    control-plane:
      condition: service_healthy
  healthcheck:
    test: ["CMD", "python", "-c", "import httpx; httpx.get('http://localhost:8011/.well-known/agent-card.json').raise_for_status()"]
    interval: 10s
    timeout: 5s
    retries: 5
    start_period: 10s
```

Note: healthcheck hits `/.well-known/agent-card.json` (A2A standard, auto-registered). Only non-A2A services (like baseline_store) use different health endpoints.

## run-local.sh Addition

```bash
echo "[N/M] Starting Your Agent on port 8011..."
CONTROL_PLANE_URL="$CP_URL" \
YOUR_AGENT_URL="http://127.0.0.1:8011" \
  python -m agents.your_agent.server &
PIDS+=($!)
wait_for_port 8011 "Your Agent"
```

Also add `"  Your Agent: http://localhost:8011"` to the summary block.

## Common Mistakes

| Mistake | Fix |
|---|---|
| Forgot `README.md` | Required per CLAUDE.md — include graph diagram, env vars, I/O schema |
| Forgot to update CLAUDE.md agent table | Add row with port, type ID, description |
| Used `_agent` suffix on dir name | Prefer short names: `agents/sentiment/` not `agents/sentiment_agent/` (exception: `extraction_agent` already exists) |
| `check_cancelled()` not first line of node | Move it above all other logic including LLM calls |
| Missing `env_file: ".env"` in compose | Copy relevancy/summarizer pattern exactly |
| Used wrong env var name pattern | Must be `{AGENT_NAME}_AGENT_URL` matching CLAUDE.md table |
| Port collision | Check docker-compose.yml before claiming a port |

## LLM Node Boilerplate

```python
from openai import AsyncOpenAI
openai_kwargs = {}
if base_url := os.getenv("OPENAI_BASE_URL"):
    openai_kwargs["base_url"] = base_url
client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"), **openai_kwargs)
model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
```
