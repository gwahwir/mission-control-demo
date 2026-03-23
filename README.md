# Mission Control

A 3-tier A2A-compliant agent orchestration platform built with FastAPI, LangGraph, and React.

## Overview

Mission Control is a distributed agent orchestration system that enables dynamic task routing, load balancing, and real-time monitoring of LangGraph-based agents. The platform implements the Agent-to-Agent (A2A) communication protocol for standardized inter-agent messaging.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              USER / CLIENT                               │
│                         (Dashboard / API Client)                         │
└────────────────┬───────────────────────────────────┬────────────────────┘
                 │                                   │
                 │ HTTP/WebSocket                    │ Poll /agents, /tasks
                 │                                   │ WebSocket /ws/tasks/:id
                 ▼                                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                          CONTROL PLANE (FastAPI)                         │
│                          http://localhost:8000                           │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  ┌────────────────┐  ┌──────────────┐  ┌─────────────┐  ┌────────────┐ │
│  │    Registry    │  │  Task Store  │  │   Pub/Sub   │  │ A2A Client │ │
│  │                │  │              │  │             │  │            │ │
│  │ • Health check │  │ • In-memory  │  │ • WS fanout │  │ • JSON-RPC │ │
│  │ • Load balance │  │ • PostgreSQL │  │ • Redis pub │  │ • message/ │ │
│  │ • Auto-register│  │              │  │             │  │   send     │ │
│  └────────────────┘  └──────────────┘  └─────────────┘  └────────────┘ │
│                                                                           │
│  Routes:                                                                  │
│  • POST   /agents/:id/tasks  → 202 Accepted (async dispatch)            │
│  • GET    /tasks/:id          → Task status & output                     │
│  • DELETE /tasks/:id          → Cancel task                              │
│  • GET    /agents             → List registered agents                   │
│  • GET    /graph              → Aggregated agent topology                │
│  • WS     /ws/tasks/:id       → Live task updates                        │
│                                                                           │
└────────────────┬──────────────────────────────────────┬─────────────────┘
                 │                                      │
                 │ A2A JSON-RPC                         │ Self-register
                 │ (message/send)                       │ on startup
                 ▼                                      │
┌─────────────────────────────────────────────────────────────────────────┐
│                          AGENT LAYER (LangGraph)                         │
│                       Wrapped with a2a-sdk HTTP servers                  │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────────┐ │
│  │  Echo Agent  │  │ Summarizer   │  │  Relevancy   │  │ Extraction  │ │
│  │  :8001       │  │  :8002       │  │  :8003       │  │  :8004      │ │
│  │              │  │              │  │              │  │             │ │
│  │ • Uppercase  │  │ • LLM        │  │ • LLM        │  │ • LLM       │ │
│  │ • Forward    │  │ • Summarize  │  │ • Relevance  │  │ • Extract   │ │
│  └──────────────┘  └──────────────┘  └──────────────┘  └─────────────┘ │
│                                                                           │
│  ┌──────────────────┐  ┌────────────────┐  ┌────────────────────────┐  │
│  │  Lead Analyst    │  │  Specialist    │  │  Probability Agent     │  │
│  │  :8005           │  │  :8006         │  │  :8007                 │  │
│  │                  │  │                │  │                        │  │
│  │ Multi-instance:  │  │ Multi-agent:   │  │ • Aggregation         │  │
│  │ • 3 leads (A/B/C)│  │ • 16 geopolit. │  │ • Disagreement detect │  │
│  │ • Fan-out to     │◄─┤   intelligence │◄─┤ • Peripheral scan     │  │
│  │   specialists    │  │   specialists  │  │ • Tail-risk reserves  │  │
│  │ • Aggregate      │  │                │  │ • Equal-weighted avg  │  │
│  └──────────────────┘  └────────────────┘  └────────────────────────┘  │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
```

## Quick Start

### Prerequisites
- Python 3.11+
- Node.js 18+
- Docker & Docker Compose (optional)

### Local Development

```bash
# Install Python dependencies
pip install -r requirements.txt

# Start the full stack (control plane + all agents + dashboard)
bash run-local.sh

# Or start components individually:
python -m control_plane.server                    # Control plane on :8000
python -m agents.echo.server                       # Echo agent on :8001
OPENAI_API_KEY=sk-... python -m agents.summarizer.server  # Summarizer on :8002
```

### Docker

```bash
# Start everything
OPENAI_API_KEY=sk-... docker compose up

# Scale an agent horizontally
docker compose up --scale echo-agent=3
```

**Dashboard:** http://localhost:5173
**Control Plane API:** http://localhost:8000
**API Docs:** http://localhost:8000/docs

## Workflows

### 1. Task Lifecycle

```
┌──────────┐
│  CLIENT  │
└────┬─────┘
     │
     │ POST /agents/:id/tasks
     │ {"input": "analyze this"}
     ▼
┌─────────────────┐
│ CONTROL PLANE   │
│                 │
│ 1. Create task  │      ┌──────────────────────────────────┐
│    id: task-123 │      │    TASK STATE MACHINE            │
│    state: submitted    │                                  │
│                 │      │  submitted → working → completed │
│ 2. Return 202   │      │                ↓          ↓      │
└────┬────────────┘      │           input-required  failed │
     │                   │                ↓          ↓      │
     │ 202 Accepted      │             canceled   canceled  │
     │ task_id: task-123 └──────────────────────────────────┘
     ▼
┌──────────┐
│  CLIENT  │─────────┐
└──────────┘         │
                     │ Poll GET /tasks/task-123
                     │ or WebSocket /ws/tasks/task-123
                     ▼
              ┌──────────────┐
              │ LIVE UPDATES │
              │              │
              │ • Node start │
              │ • Progress   │
              │ • Completion │
              └──────────────┘
```

#### State Transitions

```
submitted ──────────────► working ──────────────► completed
                              │                        ▲
                              │                        │
                              ├──► input-required ─────┤
                              │                        │
                              ├──► failed              │
                              │                        │
                              └──► canceled ◄──────────┘
                                        ▲
                                        │
                                   DELETE /tasks/:id
```

### 2. Agent Registration Flow

```
┌─────────────┐                              ┌─────────────────┐
│   AGENT     │                              │ CONTROL PLANE   │
│   STARTUP   │                              │                 │
└──────┬──────┘                              └────────┬────────┘
       │                                              │
       │ 1. Agent starts                              │
       │    Reads CONTROL_PLANE_URL                   │
       │                                              │
       │ 2. POST /agents/register                     │
       │    {                                         │
       │      "agent_id": "echo-001",                 │
       │      "type_id": "echo-agent",                │
       │      "url": "http://localhost:8001",         │
       │      "capabilities": [...]                   │
       │    }                                         │
       ├─────────────────────────────────────────────►│
       │                                              │
       │                                     3. Store in registry
       │                                        Start health check
       │                                              │
       │ 4. 200 OK                                    │
       │◄─────────────────────────────────────────────┤
       │                                              │
       │                                              │
       │         5. Health checks (every 30s)         │
       │         GET /.well-known/agent-card.json     │
       │◄─────────────────────────────────────────────┤
       │                                              │
       │         200 OK                               │
       ├─────────────────────────────────────────────►│
       │                                              │
       │                                              │
       │ 6. Agent shutdown signal                     │
       │    POST /agents/deregister                   │
       │    {"agent_id": "echo-001"}                  │
       ├─────────────────────────────────────────────►│
       │                                              │
       │                                     7. Remove from registry
       │                                        Stop health checks
       │                                              │
       │ 8. 200 OK                                    │
       │◄─────────────────────────────────────────────┤
       │                                              │
```

### 3. Lead Analyst Orchestration

The Lead Analyst demonstrates complex multi-agent orchestration:

```
┌────────────────────────────────────────────────────────────────────┐
│                      LEAD ANALYST WORKFLOW                         │
└────────────────────────────────────────────────────────────────────┘

         ┌──────────────┐
         │    START     │
         │              │
         │ Input: Query │
         │      + Docs  │
         └──────┬───────┘
                │
                ▼
         ┌──────────────┐
         │  route_llm   │
         │              │
         │ • LLM selects│
         │   reasoning  │
         │   mode       │
         └──────┬───────┘
                │
                ▼
         ┌──────────────────────────────────────┐
         │       fan_out_to_specialists         │
         │                                      │
         │  Concurrent A2A calls (up to 8)      │
         │  via asyncio.gather()                │
         │  ┌────────┐  ┌────────┐  ┌────────┐ │
         │  │Spec #1 │  │Spec #2 │  │Spec #N │ │
         │  │:8006   │  │:8006   │  │:8006   │ │
         │  └───┬────┘  └───┬────┘  └───┬────┘ │
         │      │           │           │      │
         │      └───────────┴───────────┘      │
         │      All results gathered in parallel│
         └──────────────────┬───────────────────┘
                            │
                            ▼
         ┌──────────────────────────────────────┐
         │      aggregate_specialist_outputs    │
         │                                      │
         │  • Concat all analyses               │
         │  • Add metadata                      │
         │  • Format for probability agent      │
         └──────────────────┬───────────────────┘
                            │
                            ▼
         ┌──────────────────────────────────────┐
         │     send_to_probability_agent        │
         │                                      │
         │  A2A call to :8007                   │
         │  ├─► Aggregation                     │
         │  ├─► Disagreement detection          │
         │  ├─► Peripheral scan                 │
         │  └─► Structured briefing             │
         └──────────────────┬───────────────────┘
                            │
                            ▼
         ┌──────────────────────────────────────┐
         │             format_output            │
         │                                      │
         │  Return final briefing with:         │
         │  • Executive summary                 │
         │  • Key forecasts                     │
         │  • Methodology notes                 │
         └──────────────────┬───────────────────┘
                            │
                            ▼
                       ┌─────────┐
                       │   END   │
                       └─────────┘
```

#### Specialist Selection

```
Specialist Agent (:8006) hosts 16 geopolitical/intelligence specialists:

┌────────────────────────────────────────────────────────────┐
│  ANALYTICAL METHODOLOGIES                                  │
├────────────────────────────────────────────────────────────┤
│ • ach-red-team                 │  Analysis of Competing    │
│                                │  Hypotheses & red teaming │
│ • behavioral-economics         │  Cognitive biases &       │
│                                │  decision-making patterns │
│ • counterfactual-thinking      │  Alternative history &    │
│                                │  what-if scenarios        │
│ • peripheral-scan              │  Blind spots & overlooked │
│                                │  signals detection        │
└────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────┐
│  INTERNATIONAL RELATIONS FRAMEWORKS                        │
├────────────────────────────────────────────────────────────┤
│ • realist-ir                   │  Power politics & state   │
│                                │  interests (realism)      │
│ • liberal-ir                   │  Institutions & norms     │
│                                │  (liberal IR theory)      │
│ • copenhagen-securitization    │  Security construction &  │
│                                │  speech-act theory        │
└────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────┐
│  DOMAIN EXPERTS                                            │
├────────────────────────────────────────────────────────────┤
│ • asean-security               │  Southeast Asia security  │
│ • climate-security             │  Climate & environmental  │
│                                │  security nexus           │
│ • economic-statecraft          │  Economic tools of power  │
│ • military-strategy-deterrence │  Military doctrine &      │
│                                │  deterrence theory        │
│ • technology-emerging-threats  │  Tech disruption & cyber  │
│                                │  threats                  │
└────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────┐
│  THOUGHT LEADERS                                           │
├────────────────────────────────────────────────────────────┤
│ • bilahari-kausikan            │  Singaporean diplomat &   │
│                                │  strategic realist        │
│ • bridget-welsh                │  Southeast Asia politics  │
│                                │  expert                   │
│ • taleb-antifragile            │  Antifragility, black     │
│                                │  swans, tail risks        │
│ • yergin-energy                │  Energy geopolitics &     │
│                                │  resource security        │
└────────────────────────────────────────────────────────────┘

Lead Analyst selects 3-8 specialists based on query relevance.
Each specialist returns structured JSON analysis with key findings,
evidence, predictions, limitations, and confidence levels.

The system deploys 3 Lead Analyst variants:
• Lead Analyst A: Docker deployment with static specialist URLs
• Lead Analyst B: Local dev with localhost specialist URLs
• Lead Analyst C: Dynamic specialist discovery via control plane
```

### 4. Load Balancing

```
┌─────────────────────────────────────────────────────────────────┐
│                      REGISTRY LOAD BALANCER                     │
└─────────────────────────────────────────────────────────────────┘

Task arrives for type_id: "echo-agent"
              │
              ▼
     ┌────────────────┐
     │  Query registry│
     │  for instances │
     └────────┬───────┘
              │
              ▼
┌─────────────────────────────────────────────────────────┐
│  Found 3 instances:                                     │
│                                                         │
│  echo-001  http://localhost:8001  active_tasks: 2      │
│  echo-002  http://localhost:8002  active_tasks: 5      │
│  echo-003  http://localhost:8003  active_tasks: 1  ← SELECTED
│                                                         │
│  Strategy: Least active tasks                          │
└─────────────────────────────────────────────────────────┘
              │
              ▼
     ┌────────────────┐
     │ Route to       │
     │ echo-003       │
     │ Increment count│
     └────────┬───────┘
              │
              ▼
     ┌────────────────┐
     │ A2A message    │
     │ POST /execute  │
     └────────────────┘
```

## Key Features

### A2A Protocol Compliance
- **JSON-RPC 2.0** message format
- Standard `message/send` method for task submission
- `tasks/cancel` for mid-run cancellation
- Agent cards at `/.well-known/agent-card.json`
- Streaming support via Server-Sent Events (SSE)
- Cross-agent communication via control plane routing

### Async Task Execution
- Tasks return 202 Accepted immediately
- Background execution via asyncio
- Non-blocking agent operations

### Dynamic Agent Discovery
- Self-registration on startup
- Health monitoring (30s intervals)
- Auto-removal on failure
- Manual registration via `AGENT_URLS`

### Real-time Updates
- WebSocket subscriptions per task
- Pub/sub via in-memory queues or Redis
- TaskStatusUpdateEvent at each graph node

### Horizontal Scaling
- Multiple instances per agent type
- Least-active-tasks load balancing
- Stateless agent design

### Task Cancellation
- Mid-run cancellation support
- Asyncio event-based signaling
- Graceful cleanup in graph nodes

### Observability
- **Langfuse integration** for LLM tracing with span nesting
- **OpenAI instrumentation** via LangchainCallbackHandler
- Structured logging with configurable `LOG_LEVEL`
- Graph topology introspection via `/graph` endpoints
- Per-node status updates via TaskStatusUpdateEvent
- WebSocket live streaming for real-time monitoring

## Agent Details

| Agent | Port | Type ID | Description |
|-------|------|---------|-------------|
| **Echo** | 8001 | `echo-agent` | Reference implementation, uppercases input, optional forwarding |
| **Summarizer** | 8002 | `summarizer` | OpenAI-powered text summarization |
| **Relevancy** | 8003 | `relevancy` | Assesses relevance to a question, returns JSON verdict |
| **Extraction** | 8004 | `extraction` | Extracts entities, events, relationships from text |
| **Lead Analyst** | 8005 | per-YAML | Orchestrates 3 lead analysts (A/B/C), fans out to specialists with concurrent A2A calls (up to 8) |
| **Specialist** | 8006 | per-YAML | Hosts 16 geopolitical/intelligence specialists for analytical frameworks and domain expertise |
| **Probability** | 8007 | `probability-forecaster` | Equal-weighted aggregation, disagreement detection, peripheral scanning, tail-risk reserves |

See each agent's README for detailed documentation.

## Environment Variables

### Control Plane

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENT_URLS` | `http://localhost:8001` | Comma-separated agent URLs (`name@url` format supported) |
| `DATABASE_URL` | None | PostgreSQL DSN for persistent task storage |
| `REDIS_URL` | None | Redis URL for multi-instance WebSocket pub/sub |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

### Agents

Each agent has its own URL variable (e.g., `ECHO_AGENT_URL`, `SUMMARIZER_AGENT_URL`), falling back to `AGENT_URL`, then `http://localhost:<port>`.

**Shared variables:**
- `CONTROL_PLANE_URL` - For self-registration (all agents)
- `OPENAI_API_KEY` - Required for LLM-based agents (summarizer, relevancy, extraction, lead analyst, specialist, probability)
- `OPENAI_BASE_URL` - Custom OpenAI-compatible endpoint
- `OPENAI_MODEL` - Default: `gpt-4o-mini`
- `LANGFUSE_PUBLIC_KEY` - Optional, for LLM tracing
- `LANGFUSE_SECRET_KEY` - Optional, for LLM tracing
- `LANGFUSE_HOST` - Optional, Langfuse server URL

**Echo Agent specific:**
- `DOWNSTREAM_AGENT_URL` - Optional URL to forward output to another agent

## API Examples

### Submit a Task

```bash
curl -X POST http://localhost:8000/agents/echo-agent/tasks \
  -H "Content-Type: application/json" \
  -d '{"input": "hello world"}'

# Response: 202 Accepted
{
  "task_id": "task-123",
  "state": "submitted"
}
```

### Get Task Status

```bash
curl http://localhost:8000/tasks/task-123

# Response:
{
  "task_id": "task-123",
  "state": "completed",
  "output": "HELLO WORLD",
  "created_at": "2026-03-23T12:00:00Z",
  "updated_at": "2026-03-23T12:00:05Z"
}
```

### WebSocket Updates

```javascript
const ws = new WebSocket('ws://localhost:8000/ws/tasks/task-123');
ws.onmessage = (event) => {
  const update = JSON.parse(event.data);
  console.log(`State: ${update.state}, Node: ${update.current_node}`);
};
```

### List Agents

```bash
curl http://localhost:8000/agents

# Response:
[
  {
    "agent_id": "echo-001",
    "type_id": "echo-agent",
    "url": "http://localhost:8001",
    "active_tasks": 2,
    "last_health_check": "2026-03-23T12:00:00Z"
  }
]
```

## Testing

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_task_lifecycle.py -v

# Run single test
pytest tests/test_task_lifecycle.py::test_task_submission -v
```

Tests use `pytest-httpx` to mock A2A HTTP calls. See `tests/conftest.py` for fixtures.

## Development

### Adding a New Agent

1. **Create graph** in `agents/<name>/graph.py` with `check_cancelled()` in each node
2. **Create executor** in `agents/<name>/executor.py` (subclass `LangGraphA2AExecutor`)
3. **Create server** in `agents/<name>/server.py` with:
   - A2A HTTP server on new port
   - Lifespan events for register/deregister
   - `/graph` endpoint with `INPUT_FIELDS`
4. **Document** in `agents/<name>/README.md`
5. **Add Docker** config in `Dockerfile.<name>` and `docker-compose.yml`
6. **Update** `run-local.sh`

### Project Structure

```
mission-control/
├── control_plane/          # FastAPI orchestration layer
│   ├── server.py           # Main server + routes
│   ├── registry.py         # Agent registry + load balancer
│   ├── task_store.py       # Task persistence (in-memory/PostgreSQL)
│   ├── pubsub.py           # WebSocket pub/sub (in-memory/Redis)
│   └── a2a_client.py       # A2A JSON-RPC client
├── agents/
│   ├── base/               # Shared base classes
│   │   ├── executor.py     # LangGraphA2AExecutor
│   │   ├── cancellation.py # CancellableMixin
│   │   └── registration.py # Self-registration helpers
│   ├── echo/               # Reference agent
│   ├── summarizer/         # LLM summarization
│   ├── relevancy/          # Relevance assessment
│   ├── extraction_agent/   # Entity extraction
│   ├── lead_analyst/       # Multi-analyst orchestrator
│   ├── specialist_agent/   # 16 LLM specialists
│   └── probability_agent/  # Probability aggregation
├── dashboard/              # React SPA
│   ├── src/
│   │   ├── components/     # UI components
│   │   ├── hooks/          # useApi, useWebSocket
│   │   └── pages/          # TaskList, AgentGraph
│   └── vite.config.js      # Proxy config
├── tests/                  # pytest tests
├── docker-compose.yml      # Full stack deployment
└── run-local.sh            # Local development script
```

## License

MIT

## Contributing

See [CLAUDE.md](CLAUDE.md) for development guidelines and architecture details.
