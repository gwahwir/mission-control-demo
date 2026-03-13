# Mission Control · A2A Agent Platform
## Development Plan · v1.0 · March 2026

> A centralised platform for orchestrating, monitoring, and controlling LangGraph agents via the A2A protocol.

---

## 1. Executive Summary

This document outlines a structured, four-phase plan to build Mission Control — a centralised platform that discovers, orchestrates, monitors, and cancels LangGraph AI agents using the open A2A (Agent-to-Agent) protocol.

The platform consists of three services working in concert: an A2A-compliant wrapper on each agent, a FastAPI Control Plane that acts as the routing and registry layer, and a React dashboard that gives operators full visibility and control at a glance.

| | |
|---|---|
| **Phases** | 4 |
| **Total Duration** | ~12 weeks |
| **Core Services** | 3 |
| **Unified Dashboard** | 1 |

### Architecture Overview

Each LangGraph agent is exposed as a standalone A2A server. The Control Plane acts as an A2A client, discovering agents via their Agent Cards and brokering all task lifecycle operations. The React dashboard connects to the Control Plane over REST and WebSocket.

```
┌─────────────────────────────────────────────────────────┐
│             MISSION CONTROL DASHBOARD (React)           │
│   Agent List  │  Task Monitor  │  Logs  │  Cancel/Stop  │
└────────────────────────┬────────────────────────────────┘
                         │  WebSocket / REST API
         ┌───────────────▼──────────────────┐
         │     CONTROL PLANE (FastAPI)       │
         │  Agent Registry │ Task Router     │
         │  Webhook Handler│ Auth/RBAC       │
         └──────┬──────────┬──────────┬──────┘
                │ A2A      │ A2A      │ A2A
         ┌──────▼──┐  ┌────▼────┐ ┌──▼──────┐
         │Agent 1  │  │Agent 2  │ │Agent 3  │
         │LangGraph│  │LangGraph│ │LangGraph│
         └─────────┘  └─────────┘ └─────────┘
```

---

## 2. Technology Stack

| Layer | Technology | Purpose |
|---|---|---|
| Agent Layer | LangGraph + a2a-sdk | Graph-based agent logic with A2A server wrapping |
| Control Plane | FastAPI + Python | REST API, WebSocket gateway, agent registry |
| A2A Client | a2a-sdk Python client | Talks to each agent's A2A endpoint |
| Task Store | Redis (or Postgres) | Persists task state, history, and artifacts |
| Dashboard | React + TailwindCSS | Operator UI for monitoring and control |
| Realtime | WebSockets (FastAPI) | Live task state pushed from control plane to UI |
| Auth | JWT + RBAC | Protects cancel/stop endpoints |
| Infra | Docker Compose / K8s | Container-based deployment for all services |

---

## 3. Development Phases

### ▸ Phase 1 — A2A-Wrap Your LangGraph Agents · Weeks 1–2

**Goal:** Make every LangGraph agent speak A2A so they can be discovered, invoked, monitored, and cancelled by any A2A-compatible client.

#### Key Tasks

- Install `a2a-sdk[http-server]` alongside each agent
- Create an `AgentExecutor` class that bridges A2A requests to `graph.ainvoke()`
- Add cancellation checks at every LangGraph node (`context.is_cancelled()`)
- Emit `TaskStatusUpdateEvent`s at each node for real-time progress
- Define an `AgentCard` per agent (name, skills, capabilities, `streaming: true`)
- Expose each agent as a standalone HTTP server (uvicorn)
- Verify `/.well-known/agent.json` is accessible for each agent

#### Deliverables

| Component | Deliverable | Notes |
|---|---|---|
| AgentExecutor base class | Reusable wrapper for all agents | Handles A2A ↔ LangGraph translation |
| Cancellation mixin | Per-node cancel check utility | Ensures clean mid-run stops |
| AgentCard definitions | JSON config per agent | Name, skills, streaming capability |
| Docker image per agent | Containerised A2A server | Consistent deployment unit |

---

### ▸ Phase 2 — Build the Control Plane · Weeks 3–6

**Goal:** A FastAPI service that is the single entry point for the dashboard. It discovers agents, routes tasks, stores task history, and exposes clean REST and WebSocket APIs.

#### Key Tasks

- **Agent Registry module** — reads agent URLs from config, fetches Agent Cards on startup
- **Health polling** — periodic GET to each agent's Agent Card; marks agents online/offline
- **A2A client wrapper** — thin async client around a2a-sdk for `message/send`, `tasks/cancel`, `tasks/subscribe`
- **Task Store** — Redis-backed store for task state, logs, and artifacts
- **REST API endpoints:**
  - `GET /agents` — list all registered agents and their status
  - `POST /agents/{id}/tasks` — dispatch a new task to an agent
  - `GET /agents/{id}/tasks/{taskId}` — fetch current task state
  - `DELETE /agents/{id}/tasks/{taskId}` — cancel a running task
  - `GET /tasks` — global task history across all agents
- **WebSocket** `/ws/tasks/{taskId}` — stream live state updates to dashboard
- Subscribe to each running task via `tasks/subscribe` and fan out to WebSocket clients
- Webhook receiver endpoint for long-running async agent tasks
- JWT-based auth middleware — protect mutating endpoints (cancel, dispatch)

#### Deliverables

| Component | Deliverable | Notes |
|---|---|---|
| Agent Registry | Auto-discovery on startup + health polling | Config-driven, hot-reloadable |
| Task Router | Dispatch, track, and cancel tasks | Full A2A lifecycle support |
| WebSocket Gateway | Real-time fan-out to dashboard clients | Handles N subscribers per task |
| Task Store | Persistent history and artifacts | Redis with TTL; Postgres for audit |
| REST API | OpenAPI-documented endpoints | Auto-docs at /docs |
| Auth Middleware | JWT + role-based access control | Separate viewer and operator roles |

---

### ▸ Phase 3 — Build the Mission Control Dashboard · Weeks 7–10

**Goal:** A React single-page application giving operators full visibility and control: see all agents, launch tasks, watch live progress, inspect logs and artifacts, and cancel runaway jobs — all in one place.

#### Key Tasks

- **Agent Overview panel** — grid of agent cards showing name, skills, status (online/offline/busy)
- **Task Launcher** — form to dispatch a new task to any agent with a text prompt
- **Active Tasks board** — Kanban-style columns by state: Queued / Working / Input Required / Done
- **Task Detail drawer** — opens on click; shows live streamed log lines via WebSocket
- **Cancel button** — calls `DELETE /agents/{id}/tasks/{taskId}` with confirmation dialog
- **Global Task History table** — searchable, filterable, sortable across all agents
- **Artifact viewer** — renders text, JSON, and markdown outputs inline
- **Input-Required modal** — surfaces human-in-the-loop prompts when an agent needs input
- **Toast notifications** — real-time alerts for task completions and failures
- **Role-aware UI** — hide cancel/launch controls for viewer-role users

#### Deliverables

| Component | Deliverable | Notes |
|---|---|---|
| Agent Overview Panel | Live status grid for all agents | Polls /agents every 10s |
| Task Board | Kanban by task state | WebSocket-driven, no polling |
| Task Detail Drawer | Streamed logs + artifact viewer | Markdown + JSON rendering |
| Cancel Flow | Confirm dialog → API call → state update | Prevents accidental stops |
| Task History Table | Cross-agent searchable history | Filter by agent, state, date |
| Auth UI | Login, token storage, role gating | Viewer vs operator roles |

---

### ▸ Phase 4 — Hardening, Testing & Deployment · Weeks 11–12

**Goal:** Make the system production-ready: resilient, observable, tested, and deployable as a single Docker Compose stack (or Kubernetes manifests for larger deployments).

#### Key Tasks

- Integration tests — spin up real LangGraph agents and run end-to-end task lifecycle tests
- Cancel reliability tests — verify agents stop cleanly mid-graph at each possible node
- Control Plane load tests — simulate 50+ concurrent tasks across 10 agents
- Structured logging (structlog) with correlation IDs across control plane and agents
- Prometheus metrics endpoint on control plane — task counts, latency, error rates
- Grafana dashboard for ops-level monitoring
- Docker Compose file — one command to run agents + control plane + Redis + dashboard
- Kubernetes Helm chart (optional) for cloud deployment
- CI/CD pipeline — lint, test, build, push images on every commit
- Documentation — API reference, agent onboarding guide, runbook

#### Deliverables

| Component | Deliverable | Notes |
|---|---|---|
| Integration test suite | End-to-end task lifecycle coverage | pytest + httpx async |
| Structured logs | JSON logs with correlation IDs | structlog on all services |
| Metrics + Dashboards | Prometheus + Grafana | Task throughput, error rates |
| Docker Compose stack | One-command local deployment | All services + Redis |
| Helm chart | K8s production deployment | Optional scale-out path |
| Documentation | Onboarding + runbook + API docs | Auto-generated OpenAPI |

---

## 4. Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Agent doesn't stop cleanly on cancel | High | Add `context.is_cancelled()` at every LangGraph node; test in Phase 4 |
| A2A SDK API changes mid-development | Medium | Pin a2a-sdk version; review changelog before upgrading |
| WebSocket fan-out bottleneck at scale | Medium | Use Redis pub/sub as the WebSocket backing store |
| Task state lost on Control Plane restart | Medium | Persist all state in Redis / Postgres from Phase 2 onwards |
| Agent discovery fails if URL unreachable | Low | Registry marks agent offline; retries with exponential back-off |
| JWT tokens leaked via browser storage | Low | Store tokens in httpOnly cookies; add CSRF protection |

---

## 5. Timeline at a Glance

| Week | Phase 1 | Phase 2 | Phase 3 | Phase 4 |
|---|---|---|---|---|
| Week 1  | ████████ | | | |
| Week 2  | ████████ | | | |
| Week 3  | | ████████ | | |
| Week 4  | | ████████ | | |
| Week 5  | | ████████ | | |
| Week 6  | | ████████ | | |
| Week 7  | | | ████████ | |
| Week 8  | | | ████████ | |
| Week 9  | | | ████████ | |
| Week 10 | | | ████████ | |
| Week 11 | | | | ████████ |
| Week 12 | | | | ████████ |

---

## 6. Success Criteria

### Phase 1 Complete When…
- All agents respond to A2A requests at their `/.well-known/agent.json` endpoint
- Agents emit streaming progress events on each graph node transition
- Agents stop cleanly within 2 seconds of a cancel signal

### Phase 2 Complete When…
- Control Plane auto-discovers all registered agents on startup
- Task cancel via REST API reliably halts the target agent
- WebSocket clients receive state updates within 500 ms of agent events

### Phase 3 Complete When…
- Operator can launch, monitor, and cancel any task from the dashboard
- Input-Required tasks surface a prompt to the operator without manual polling
- Task history is searchable across all agents

### Phase 4 Complete When…
- End-to-end test suite passes with 10 concurrent agents and 50 simultaneous tasks
- P99 task dispatch latency is under 1 second
- `docker compose up` starts the entire platform from scratch in under 2 minutes

---

## 7. Recommended Next Steps

To get started immediately:

1. Pick one existing LangGraph agent and complete its A2A wrapper as a proof-of-concept
2. Stand up the Control Plane skeleton (FastAPI + `/agents` endpoint) locally
3. Confirm the Agent Card JSON is correctly surfaced and parseable
4. Run a manual cancel test — confirm the agent stops mid-execution
5. Then scale out the wrapper pattern to remaining agents before building the dashboard

> This plan is designed to be incremental — each phase delivers independently useful capability. Phase 1 alone gives you A2A-compliant agents; Phase 2 alone gives you a programmable control API; the dashboard in Phase 3 is the operator-facing layer built on top of that solid foundation.
