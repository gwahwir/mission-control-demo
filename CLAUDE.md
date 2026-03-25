# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Setup
```bash
# Copy env template and fill in secrets
cp .env.template .env

# Create and activate virtual environment (or use conda: conda activate mc)
python -m venv .venv && source .venv/bin/activate  # Linux/macOS
python -m venv .venv && .venv\Scripts\activate      # Windows

# Install dependencies
pip install -r requirements.txt
```

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

# Run control plane locally
python -m control_plane.server

# Run individual agents locally
python -m agents.echo.server
OPENAI_API_KEY=sk-... python -m agents.summarizer.server
OPENAI_API_KEY=sk-... python -m agents.relevancy.server
OPENAI_API_KEY=sk-... python -m agents.extraction_agent.server
python -m agents.lead_analyst.server
OPENAI_API_KEY=sk-... python -m agents.specialist_agent.server
OPENAI_API_KEY=sk-... python -m agents.probability_agent.server
python -m agents.knowledge_graph.server
OPENAI_API_KEY=sk-... python -m agents.memory_agent.server
python -m baseline_store.server

# Run everything locally (starts control plane, all agents, and dashboard)
bash run-local.sh
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
OPENAI_API_KEY=sk-... docker compose up  # With OpenAI key for LLM-based agents
```

## Architecture

Mission Control is a 3-tier A2A-compliant agent orchestration platform.

### Control Plane (`control_plane/`)
FastAPI service that orchestrates agents. Key responsibilities:
- **Registry** (`registry.py`): Agents self-register via `POST /agents/register` on startup and deregister on shutdown. Also supports manual registration via `AGENT_URLS` env var. Health-polls every 30s, routes tasks using least-active-tasks load balancing across instances.
- **Async dispatch** (`routes.py`): `POST /agents/{id}/tasks` returns 202 immediately; task runs in a background asyncio task.
- **Task store** (`task_store.py`): Pluggable backends — in-memory (default) or PostgreSQL (when `DATABASE_URL` is set). Task state machine: `submitted → working → completed/failed/canceled`, with `input-required` as an intermediate state.
- **Pub/sub** (`pubsub.py`): WebSocket fan-out via in-memory asyncio queues (single process) or Redis pub/sub (when `REDIS_URL` is set, enables multi-instance scaling).
- **A2A client** (`a2a_client.py`): Thin async JSON-RPC client implementing `message/send`, `tasks/cancel`, and `stream_message`.
- **Graph aggregation** (`routes.py` `GET /graph`): Fetches topology from all agents' `/graph` endpoints and resolves cross-agent edges.

### Agents (`agents/`)
LangGraph agents wrapped with `a2a-sdk` HTTP servers. Each agent:
- Exposes `/.well-known/agent-card.json` with its metadata
- Responds to A2A JSON-RPC requests
- Self-registers with the control plane on startup and deregisters on shutdown (via `agents/base/registration.py`)
- Exposes `GET /graph` returning node topology and `input_fields` for the dashboard
- Must call `executor.check_cancelled(task_id)` in every graph node to support clean mid-run cancellation

**Base classes** (`agents/base/`):
- `LangGraphA2AExecutor` — bridges A2A JSON-RPC → LangGraph graph execution, emits `TaskStatusUpdateEvent` at each node, provides `get_graph_topology()` for introspection
- `CancellableMixin` — per-task `asyncio.Event` for cancellation signals
- `registration.py` — shared `register_with_control_plane()` / `deregister_from_control_plane()` helpers with retry logic

**Agents:**

| Agent | Port | Type ID | Description |
|---|---|---|---|
| Echo (`agents/echo/`) | 8001 | `echo-agent` | Reference implementation: echoes in uppercase, optionally forwards to downstream agent |
| Summarizer (`agents/summarizer/`) | 8002 | `summarizer` | Summarizes text using OpenAI LLM |
| Relevancy (`agents/relevancy/`) | 8003 | `relevancy` | Assesses text relevancy to a question, returns JSON verdict |
| Extraction (`agents/extraction_agent/`) | 8004 | `extraction` | Extracts structured entities/events/relationships from text |
| Lead Analyst (`agents/lead_analyst/`) | 8005 | per-YAML | Multi-instance orchestrator: hosts N lead analysts from YAML configs. Supports static mode (YAML-defined sub-agents fan out in parallel) and dynamic mode (discovers specialists via control plane at runtime using LangGraph `Send()`). After aggregation, always runs a meta-analysis pipeline: peripheral scan → ACH red-team → final synthesis. |
| Specialist (`agents/specialist_agent/`) | 8006 | per-YAML | Multi-agent-per-deployment: hosts 22 LLM specialists (11 theoretical frameworks, 6 domain specialists, 2 regional/individual perspectives, 3 meta-analysis tools) from YAML configs |
| Probability (`agents/probability_agent/`) | 8007 | `probability-forecaster` | Takes concatenated specialist analyses, performs probability aggregation, disagreement detection, peripheral scanning, and generates structured briefings |
| Knowledge Graph (`agents/knowledge_graph/`) | 8008 | `knowledge-graph` | Ingests articles/snippets into a persistent knowledge graph of entities and issues via mem0 (Neo4j + pgvector); returns structured diff + narrative |
| Memory Agent (`agents/memory_agent/`) | 8009 | `memory-agent` | Dual-store memory agent: write (raw text → LLM extraction → pgvector + Neo4j), search (semantic), and traverse (graph walk). No mem0 dependency. |
| Baseline Store (`baseline_store/`) | 8010 | N/A (plain FastAPI) | Deterministic storage/retrieval layer for topic baselines: versioned narratives with ltree hierarchy, pgvector semantic search, and delta log. Not an A2A agent. |

Each agent has its own README.md with detailed docs.

### Dashboard (`dashboard/`)
React SPA (Vite + Mantine UI). Polls `/agents` every 10s and `/tasks` every 3s. Opens `WS /ws/tasks/{id}` for live updates. Shows an interactive React Flow diagram of agent graphs with cross-agent edges. Dynamic input forms render based on each agent's declared `input_fields`. Vite dev server proxies `/agents`, `/tasks`, `/graph`, and `/ws` to `http://localhost:8000`.

## Environment Variables

### Control Plane

| Variable | Default | Description |
|---|---|---|
| `AGENT_URLS` | `http://localhost:8001` | Comma-separated agent URLs, optionally with `name@url` format (fallback for manual registration) |
| `DATABASE_URL` | None | PostgreSQL DSN for persistent task store |
| `REDIS_URL` | None | Redis URL for multi-instance WebSocket pub/sub |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

### Per-Agent URL Variables

Each agent reads its own specific env var for its externally-reachable URL, falling back to `AGENT_URL`, then to `http://localhost:<port>`:

| Variable | Agent | Default |
|---|---|---|
| `ECHO_AGENT_URL` | Echo | `http://localhost:8001` |
| `SUMMARIZER_AGENT_URL` | Summarizer | `http://localhost:8002` |
| `RELEVANCY_AGENT_URL` | Relevancy | `http://localhost:8003` |
| `EXTRACTION_AGENT_URL` | Extraction | `http://localhost:8004` |
| `LEAD_ANALYST_AGENT_URL` | Lead Analyst | `http://localhost:8005` |
| `SPECIALIST_AGENT_URL` | Specialist | `http://localhost:8006` |
| `PROBABILITY_AGENT_URL` | Probability | `http://localhost:8007` |
| `KNOWLEDGE_GRAPH_AGENT_URL` | Knowledge Graph | `http://localhost:8008` |
| `MEMORY_AGENT_URL` | Memory Agent | `http://localhost:8009` |
| `BASELINE_STORE_URL` | Baseline Store | `http://localhost:8010` |

### Shared Agent Variables

| Variable | Default | Description |
|---|---|---|
| `CONTROL_PLANE_URL` | None | Control plane URL for self-registration (all agents) |
| `OPENAI_API_KEY` | None | Required for summarizer, relevancy, and extraction agents |
| `OPENAI_BASE_URL` | OpenAI default | Custom OpenAI-compatible base URL |
| `OPENAI_MODEL` | `gpt-4o-mini` | LLM model for LLM-based agents |
| `OPENAI_SMALL_MODEL` | None | Smaller/cheaper model for knowledge graph agent extraction |
| `OPENAI_EMBEDDING_MODEL` | None | Embedding model for knowledge graph agent (mem0 vector search) |
| `DOWNSTREAM_AGENT_URL` | None | Echo agent only — URL of downstream agent to forward output to |
| `SPECIALIST_AGENT_PORT` | `8006` | Specialist only — port to listen on |
| `MEM0_NEO4J_URL` | None | Neo4j bolt URL (knowledge graph agent only) |
| `MEM0_NEO4J_USER` | None | Neo4j username (knowledge graph agent only) |
| `MEM0_NEO4J_PASSWORD` | None | Neo4j password (knowledge graph agent only) |
| `MEM0_PG_DSN` | None | pgvector-enabled Postgres DSN (knowledge graph agent only, separate from `DATABASE_URL`) |
| `MEMORY_NEO4J_URL` | None | Neo4j bolt URL (memory agent only, required) |
| `MEMORY_NEO4J_USER` | None | Neo4j username (memory agent only, required) |
| `MEMORY_NEO4J_PASSWORD` | None | Neo4j password (memory agent only, required) |
| `MEMORY_PG_DSN` | None | pgvector Postgres DSN (memory agent only, required) |
| `MEMORY_EMBEDDING_MODEL` | None | Embedding model name (memory agent only, required) |
| `MEMORY_EMBEDDING_DIMS` | None | Vector dims — must match model, no default (memory agent only, required) |
| `BASELINE_PG_DSN` | None | pgvector + ltree Postgres DSN (baseline store only, required) |
| `BASELINE_EMBEDDING_MODEL` | None | Embedding model name (baseline store only, required) |
| `BASELINE_EMBEDDING_DIMS` | None | Vector dims — must match model, no default (baseline store only, required) |
| `LANGFUSE_PUBLIC_KEY` | None | Optional — enables Langfuse LLM tracing (all agents via `agents/base/tracing.py`) |
| `LANGFUSE_SECRET_KEY` | None | Optional — Langfuse secret key |
| `LANGFUSE_BASE_URL` | `https://cloud.langfuse.com` | Optional — Langfuse instance URL |

## Tests

Tests live in `tests/` and use `pytest-asyncio` (all tests are async by default via `asyncio_mode = auto`). A2A HTTP calls are mocked with `pytest-httpx` — no real agent process is needed. `conftest.py` provides fixtures for in-memory task store, broker, registry (with one fake echo agent), and an async HTTP client. Use `wait_for_task()` from conftest to poll until a task reaches a terminal state, since dispatch is async (202).

## Adding a New Agent

1. Create `agents/<name>/graph.py` — define a LangGraph graph with `check_cancelled()` in each node
2. Create `agents/<name>/executor.py` — subclass `LangGraphA2AExecutor`
3. Create `agents/<name>/server.py` — instantiate an A2A HTTP server on a new port, include lifespan with register/deregister, `/graph` endpoint with `INPUT_FIELDS`
4. Create `agents/<name>/README.md` — document the agent, its graph, env vars, and I/O
5. Add a `Dockerfile.<name>` and a service entry in `docker-compose.yml` with the per-agent URL env var
6. Add the agent to `run-local.sh`
