# Agents

This directory contains every A2A-wrapped LangGraph agent in the Mission Control platform. Each agent is a standalone HTTP server that speaks the [A2A protocol](https://github.com/google/a2a) — it can be discovered, invoked, monitored, and cancelled by any A2A-compatible client, including the Mission Control Control Plane.

---

## Architecture

```
agents/
├── base/
│   ├── cancellation.py   # CancellableMixin — per-task cancel signals
│   └── executor.py       # LangGraphA2AExecutor — base class for all agents
└── echo/
    ├── graph.py          # EchoState TypedDict + 3-node LangGraph
    ├── executor.py       # EchoAgentExecutor (extends base executor)
    └── server.py         # FastAPI A2A server, AgentCard, uvicorn entrypoint
```

### How an agent works

```
A2A Client (Control Plane)
        │  JSON-RPC  POST /
        ▼
┌─────────────────────────────┐
│   A2AFastAPIApplication     │  ← a2a-sdk HTTP server wrapper
│   DefaultRequestHandler     │
└────────────┬────────────────┘
             │
             ▼
┌─────────────────────────────┐
│   LangGraphA2AExecutor      │  ← base/executor.py
│   ┌─────────────────────┐   │
│   │  LangGraph graph    │   │  ← agent-specific graph (e.g. echo/graph.py)
│   │  node → node → END  │   │
│   └─────────────────────┘   │
│   CancellableMixin          │  ← check_cancelled() at every node
└─────────────────────────────┘
             │
             ▼
   TaskStatusUpdateEvent stream
   (working → working → completed | canceled | failed)
```

### Base classes

| Class | File | Purpose |
|---|---|---|
| `LangGraphA2AExecutor` | `base/executor.py` | Bridges A2A requests to `graph.ainvoke()`; streams progress events; handles cancellation and errors |
| `CancellableMixin` | `base/cancellation.py` | Per-task `asyncio.Event` cancel signals; `check_cancelled()` raises `CancelledError` cleanly |

### Adding a new agent

1. Create a new subdirectory under `agents/`, e.g. `agents/summariser/`.
2. Write a LangGraph `StateGraph` in `graph.py`.
3. Subclass `LangGraphA2AExecutor` in `executor.py` and implement `build_graph()`.
4. Copy `agents/echo/server.py` into your new folder, update the `AgentCard`, and set a unique port.
5. Call `executor.check_cancelled(task_id)` at the start of **every** graph node.

```python
# agents/summariser/executor.py
from agents.base.executor import LangGraphA2AExecutor
from agents.summariser.graph import build_summariser_graph

class SummariserAgentExecutor(LangGraphA2AExecutor):
    def build_graph(self):
        return build_summariser_graph()
```

---

## Echo Agent

The Echo Agent is the reference implementation. Its graph has three nodes:

```
receive ──► process ──► respond ──► END
```

| Node | What it does |
|---|---|
| `receive` | Reads input from state, checks for cancellation |
| `process` | Uppercases the input: `ECHO: HELLO WORLD` |
| `respond` | Writes the final output string to state |

**Agent Card** (served at `/.well-known/agent.json`):

| Field | Value |
|---|---|
| Name | Echo Agent |
| Port | `8001` |
| Streaming | `true` |
| Skills | `echo` |

---

## Starting the Echo Agent

### Prerequisites

- Python 3.11+
- Virtual environment with dependencies installed

```bash
# From the repo root
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Run locally

```bash
python -m agents.echo.server
```

The agent starts on **http://localhost:8001**.

Verify it is up:

```bash
curl http://localhost:8001/.well-known/agent.json
```

### Run with Docker

```bash
# From the repo root
docker build -f Dockerfile.agent -t mc/echo-agent .
docker run -p 8001:8001 mc/echo-agent
```

### Run via Docker Compose (full stack)

```bash
# From the repo root
docker compose up echo-agent
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `LOG_LEVEL` | `INFO` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

---

## Cancellation

Every LangGraph node must call `executor.check_cancelled(task_id)` as its first line. This checks an `asyncio.Event` that the A2A `cancel` handler sets when the Control Plane issues a `tasks/cancel` request. If the event is set, `CancelledError` is raised and the executor emits a final `canceled` status event.

```python
def my_node(state, config):
    executor = config["configurable"]["executor"]
    task_id  = config["configurable"]["task_id"]
    executor.check_cancelled(task_id)   # ← required on every node
    # ... node logic ...
```
