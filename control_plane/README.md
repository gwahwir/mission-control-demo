# Control Plane

The Control Plane is the single entry point for all agent interactions. It is a FastAPI service that discovers A2A agents on startup, routes tasks to them, persists task history, streams live updates to the dashboard over WebSocket, and exposes a Prometheus metrics endpoint for observability.

---

## Architecture

```
                  React Dashboard
                        │
            REST API / WebSocket
                        │
        ┌───────────────▼──────────────────┐
        │           Control Plane           │
        │                                  │
        │  ┌────────────┐  ┌────────────┐  │
        │  │   Agent    │  │    Task    │  │
        │  │  Registry  │  │   Store    │  │
        │  └─────┬──────┘  └─────┬──────┘  │
        │        │               │          │
        │  ┌─────▼──────────────▼──────┐  │
        │  │      Routes / Router       │  │
        │  │  REST endpoints            │  │
        │  │  WebSocket gateway         │  │
        │  │  Prometheus /metrics       │  │
        │  └────────────────────────────┘  │
        │                                  │
        │  ┌────────────────────────────┐  │
        │  │        A2A Client          │  │
        │  └────────────────────────────┘  │
        └──────┬──────────┬───────────┬────┘
               │ A2A      │ A2A       │ A2A
          ┌────▼───┐  ┌───▼────┐  ┌──▼─────┐
          │Agent 1 │  │Agent 2 │  │Agent N │
          └────────┘  └────────┘  └────────┘
```

---

## Module Overview

```
control_plane/
├── server.py       # FastAPI app factory, lifespan, middleware wiring
├── routes.py       # All REST + WebSocket endpoint handlers
├── registry.py     # AgentRegistry — discovery, health polling
├── a2a_client.py   # Async A2A JSON-RPC client (message/send, tasks/cancel)
├── task_store.py   # In-memory TaskStore (TaskRecord, TaskState)
├── config.py       # Settings loaded from environment variables
├── log.py          # structlog configuration + CorrelationIdMiddleware
└── metrics.py      # Prometheus counters, histograms, /metrics endpoint
```

---

## REST API

| Method | Path | Description |
|---|---|---|
| `GET` | `/agents` | List all registered agents and their online/offline status |
| `GET` | `/agents/{id}` | Get a single agent's details and skills |
| `POST` | `/agents/{id}/tasks` | Dispatch a new task to an agent |
| `GET` | `/agents/{id}/tasks/{taskId}` | Fetch a task's current state |
| `DELETE` | `/agents/{id}/tasks/{taskId}` | Cancel a running task |
| `GET` | `/tasks` | Global task history across all agents |
| `GET` | `/metrics` | Prometheus metrics |
| `WS` | `/ws/tasks/{taskId}` | Live task state stream (WebSocket) |

Interactive API docs are served at **http://localhost:8000/docs**.

### Task lifecycle states

```
submitted ──► working ──► completed
                 │
                 ├──► input-required ──► working
                 ├──► canceled
                 └──► failed
```

---

## Agent Registry

On startup the registry fetches `/.well-known/agent-card.json` from every configured agent URL. It marks agents `online` or `offline` and re-polls every 30 seconds (configurable). If an agent is offline, `POST /agents/{id}/tasks` returns `503`.

---

## Structured Logging

All log output is **JSON** (structlog). Every HTTP request is stamped with a `request_id` correlation ID via `CorrelationIdMiddleware`. The ID is also returned in the `X-Request-ID` response header, making it easy to trace a request end-to-end across logs.

Example log line:

```json
{"event": "task_complete", "agent_id": "echo-agent", "task_id": "abc-123", "state": "completed", "duration_s": 0.412, "request_id": "f3a1...", "timestamp": "2026-03-15T10:00:00Z"}
```

---

## Prometheus Metrics

| Metric | Type | Labels | Description |
|---|---|---|---|
| `mc_tasks_dispatched_total` | Counter | `agent_id` | Tasks dispatched to agents |
| `mc_tasks_completed_total` | Counter | `agent_id` | Tasks completed successfully |
| `mc_tasks_failed_total` | Counter | `agent_id` | Tasks that failed or errored |
| `mc_tasks_cancelled_total` | Counter | `agent_id` | Tasks cancelled by operator |
| `mc_task_duration_seconds` | Histogram | `agent_id` | End-to-end task duration |

Standard FastAPI HTTP metrics (latency, status codes) are also exposed by `prometheus-fastapi-instrumentator`.

---

## Starting the Control Plane

### Prerequisites

- Python 3.11+
- At least one A2A agent running (e.g. the Echo Agent on port 8001)

```bash
# From the repo root
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Run locally

```bash
python -m control_plane.server
```

The control plane starts on **http://localhost:8000**.

Verify it is up:

```bash
curl http://localhost:8000/agents
```

### Run with Docker

```bash
# From the repo root
docker build -f Dockerfile.control-plane -t mc/control-plane .
docker run -p 8000:8000 \
  -e AGENT_URLS=http://host.docker.internal:8001 \
  mc/control-plane
```

### Run via Docker Compose (full stack)

```bash
# From the repo root — starts echo-agent first, then control-plane
docker compose up control-plane
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `AGENT_URLS` | `http://localhost:8001` | Comma-separated list of agent base URLs |
| `LOG_LEVEL` | `INFO` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

### Registering multiple agents

```bash
AGENT_URLS=http://echo-agent:8001,http://summariser:8002,http://classifier:8003 \
  python -m control_plane.server
```

Agent names are derived automatically from the URL hostname (e.g. `echo-agent`, `summariser`).
