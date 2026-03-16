# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Python (Control Plane & Agents)
```bash
# Install dependencies
pip install -r requirements.txt

# Run all tests
pytest

# Run a single test file
pytest tests/test_task_lifecycle.py -v

# Run a single test by name
pytest tests/test_task_lifecycle.py::test_name -v

# Run control plane locally (requires at least one agent running)
AGENT_URLS=echo-agent@http://localhost:8001 python -m control_plane.server

# Run echo agent locally
python -m agents.echo.server

# Run summarizer agent locally
OPENAI_API_KEY=sk-... python -m agents.summarizer.server
```

### Dashboard (React/Vite)
```bash
cd dashboard
npm install
npm run dev    # dev server at http://localhost:5173
npm run build  # production build
```

### Docker
```bash
docker compose up                        # Full stack
docker compose up --scale echo-agent=3  # Scale agent horizontally
OPENAI_API_KEY=sk-... docker compose up  # With OpenAI key for summarizer
```

## Architecture

Mission Control is a 3-tier A2A-compliant agent orchestration platform.

### Control Plane (`control_plane/`)
FastAPI service that orchestrates agents. Key responsibilities:
- **Registry** (`registry.py`): Discovers agents from `AGENT_URLS` env var on startup, health-polls every 30s, routes tasks using least-active-tasks load balancing across instances of the same agent type.
- **Async dispatch** (`routes.py`): `POST /agents/{id}/tasks` returns 202 immediately; task runs in a background asyncio task.
- **Task store** (`task_store.py`): Pluggable backends — in-memory (default) or PostgreSQL (when `DATABASE_URL` is set). Task state machine: `submitted → working → completed/failed/canceled`, with `input-required` as an intermediate state.
- **Pub/sub** (`pubsub.py`): WebSocket fan-out via in-memory asyncio queues (single process) or Redis pub/sub (when `REDIS_URL` is set, enables multi-instance scaling).
- **A2A client** (`a2a_client.py`): Thin async JSON-RPC client implementing `message/send`, `tasks/cancel`, and `stream_message`.

### Agents (`agents/`)
LangGraph agents wrapped with `a2a-sdk` HTTP servers. Each agent:
- Exposes `/.well-known/agent-card.json` with its metadata
- Responds to A2A JSON-RPC requests
- Must call `executor.check_cancelled(task_id)` in every graph node to support clean mid-run cancellation

**Base classes** (`agents/base/`):
- `LangGraphA2AExecutor` — bridges A2A JSON-RPC → LangGraph graph execution, emits `TaskStatusUpdateEvent` at each node
- `CancellableMixin` — per-task `asyncio.Event` for cancellation signals

**Echo agent** (`agents/echo/`) — reference implementation: 3-node graph (`receive → process → respond`), no external dependencies.

**Summarizer agent** (`agents/summarizer/`) — agent composition demo: calls the echo agent via A2A, then summarizes the result with an OpenAI LLM.

### Dashboard (`dashboard/`)
React SPA (Vite + Mantine UI). Polls `/agents` every 10s and `/tasks` every 3s. Opens `WS /ws/tasks/{id}` for live updates. Vite dev server proxies `/agents`, `/tasks`, and `/ws` to `http://localhost:8000`.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `AGENT_URLS` | `http://localhost:8001` | Comma-separated agent URLs, optionally with `name@url` format |
| `DATABASE_URL` | None | PostgreSQL DSN for persistent task store |
| `REDIS_URL` | None | Redis URL for multi-instance WebSocket pub/sub |
| `LOG_LEVEL` | `INFO` | Logging verbosity |
| `OPENAI_API_KEY` | None | Required for summarizer agent |
| `OPENAI_MODEL` | `gpt-4o-mini` | LLM model for summarizer |

## Tests

Tests live in `tests/` and use `pytest-asyncio` (all tests are async by default via `asyncio_mode = auto`). A2A HTTP calls are mocked with `pytest-httpx` — no real agent process is needed. `conftest.py` provides fixtures for in-memory task store, broker, registry (with one fake echo agent), and an async HTTP client. Use `wait_for_task()` from conftest to poll until a task reaches a terminal state, since dispatch is async (202).

## Adding a New Agent

1. Create `agents/<name>/graph.py` — define a LangGraph graph with `check_cancelled()` in each node
2. Create `agents/<name>/executor.py` — subclass `LangGraphA2AExecutor`
3. Create `agents/<name>/server.py` — instantiate an A2A HTTP server on a new port
4. Add a `Dockerfile.<name>` and a service entry in `docker-compose.yml`
5. Register it via `AGENT_URLS` (e.g., `name@http://host:port`)
