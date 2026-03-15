# Dashboard

The Mission Control Dashboard is a React single-page application built with **Vite** and **Mantine UI**. It gives operators full visibility and control over every registered agent and task — all in one place.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                   React Dashboard (SPA)                  │
│                                                         │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │ AgentPanel  │  │ TaskLauncher │  │  TaskBoard /  │  │
│  │             │  │              │  │  TaskHistory  │  │
│  │ Polls every │  │ POST /agents │  │  Polls every  │  │
│  │ 10 seconds  │  │ /{id}/tasks  │  │  3 seconds    │  │
│  └─────────────┘  └──────────────┘  └───────────────┘  │
│                                                         │
│  ┌──────────────────────────────────────────────────┐   │
│  │              TaskDetailDrawer                    │   │
│  │  Task ID · State · Input · Output · Cancel btn  │   │
│  └──────────────────────────────────────────────────┘   │
└────────────────────────┬────────────────────────────────┘
                         │  REST + WebSocket
                         ▼
              Control Plane  :8000
```

During development, Vite proxies all `/agents`, `/tasks`, and `/ws` requests to the Control Plane so no CORS configuration is needed.

In production (Docker), Nginx serves the static build and proxies API calls to the `control-plane` container.

---

## Component Overview

```
src/
├── App.jsx                        # Root — layout, polling, shared state
├── index.css                      # Global styles (Mantine reset)
├── main.jsx                       # React entry point, MantineProvider
├── hooks/
│   └── useApi.js                  # Fetch/WebSocket helpers (no external lib)
└── components/
    ├── AgentCard.jsx              # Single agent card (name, status, skills)
    ├── AgentPanel.jsx             # Responsive grid of AgentCards
    ├── TaskLauncher.jsx           # Agent selector + prompt form
    ├── TaskBoard.jsx              # Kanban columns by task state
    ├── TaskHistory.jsx            # Searchable, filterable task table
    └── TaskDetailDrawer.jsx       # Slide-out drawer with cancel flow
```

### Data flow

| Component | Data source | Refresh |
|---|---|---|
| `AgentPanel` | `GET /agents` | Every 10 s |
| `TaskBoard` / `TaskHistory` | `GET /tasks` | Every 3 s |
| `TaskDetailDrawer` | WebSocket `/ws/tasks/{id}` | Push (real-time) |

---

## Task Board — Kanban Columns

| Column | State key | Colour |
|---|---|---|
| Queued | `submitted` | Gray |
| Working | `working` | Amber |
| Input Required | `input-required` | Violet |
| Done | `completed` | Green |
| Cancelled | `canceled` | Red |
| Failed | `failed` | Red |

Clicking any task card opens the **TaskDetailDrawer**, which shows the full input/output and a two-click cancel flow (first click shows confirmation, second click calls `DELETE /agents/{id}/tasks/{taskId}`).

---

## Starting the Dashboard

### Prerequisites

- Node.js 18+
- Control Plane running on `http://localhost:8000`

### Run locally (development)

```bash
cd dashboard
npm install
npm run dev
```

The dashboard opens at **http://localhost:5173**.

Vite automatically proxies API calls to the Control Plane — no extra config needed.

### Build for production

```bash
cd dashboard
npm run build
# Output in dashboard/dist/
```

### Run with Docker

```bash
# From the repo root
docker build -f dashboard/Dockerfile -t mc/dashboard dashboard/
docker run -p 3000:80 mc/dashboard
```

The dashboard is served by Nginx on **http://localhost:3000**. Nginx proxies `/agents`, `/tasks`, and `/ws` to the `control-plane` container (hostname resolved inside the Docker network).

### Run via Docker Compose (full stack)

```bash
# From the repo root — starts echo-agent, control-plane, then dashboard
docker compose up
```

Open **http://localhost:3000**.

---

## Environment

| Context | Dashboard URL | API proxy target |
|---|---|---|
| Local dev | `http://localhost:5173` | `http://localhost:8000` (Vite proxy) |
| Docker | `http://localhost:3000` | `http://control-plane:8000` (Nginx proxy) |
