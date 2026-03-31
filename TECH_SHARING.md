# Mission Control — Tech Sharing

> A walkthrough of the architecture decisions behind our A2A agent orchestration platform.

---

## What Is Mission Control?

Mission Control is a **3-tier agent orchestration platform** built on the [A2A (Agent-to-Agent) protocol](https://github.com/google/A2A). It lets you deploy, discover, and orchestrate LLM-powered agents at scale — each agent runs as an independent HTTP service, and the control plane coordinates them.

```
┌──────────────────────────────────────────────────────────┐
│                        Dashboard                         │
│              React SPA  ·  Vite  ·  Mantine UI           │
└───────────────────────────┬──────────────────────────────┘
                            │ REST / WebSocket
┌───────────────────────────▼──────────────────────────────┐
│                     Control Plane                        │
│        FastAPI  ·  Registry  ·  Task Store  ·  Pub/Sub   │
└──────┬──────────────┬──────────────┬──────────────┬──────┘
       │ A2A JSON-RPC │              │              │
┌──────▼──────┐ ┌─────▼──────┐ ┌────▼────┐ ┌──────▼──────┐
│ Lead Analyst│ │ Specialist │ │Summary  │ │  Knowledge  │
│   :8005     │ │  :8006 ×22 │ │  :8002  │ │  Graph:8008 │
└─────────────┘ └────────────┘ └─────────┘ └─────────────┘
```

---

## Key Architectural Decisions

### 1. Control Plane as Agent Registry

The control plane is not a hard-coded router. It is a **live registry** — agents announce themselves, and the control plane tracks their health and availability.

**How it works:**

```
Agent boots up
    │
    ▼
POST /agents/register  ─────────────►  Control Plane
    │                                       │
    │                                  Fetches agent card
    │                                  /.well-known/agent-card.json
    │                                       │
    │                                  Marks instance ONLINE
    │
Agent shuts down
    │
    ▼
POST /agents/deregister ────────────►  Control Plane removes instance
```

The registry groups instances by `type_id`, enabling horizontal scaling. When a task arrives, the registry picks the **least-active instance** across all online instances of that type:

```python
def pick(self) -> AgentInstance | None:
    online = [i for i in self.instances if i.status == AgentStatus.ONLINE]
    return min(online, key=lambda i: i.active_tasks)
```

Health is polled every 30 seconds. If an agent fails to return its agent card, it is marked `OFFLINE` and excluded from dispatch.

**Pros:**
- Zero downtime deployments — new instances register before old ones deregister
- Horizontal scaling is automatic — spin up more containers and they self-join
- The control plane never needs a config change when an agent moves or scales
- Task dispatch is always load-balanced without a separate load balancer

**Cons:**
- Single point of failure if the control plane goes down (mitigated by PostgreSQL-backed state + Redis pub/sub)
- Agents must implement the A2A protocol (adds a thin wrapper layer per agent)
- Registration races on startup require retry logic with exponential backoff

---

### 2. Self-Registration and Discovery

Every agent calls `register_with_control_plane()` in its **lifespan startup hook** and `deregister_from_control_plane()` on shutdown. This is entirely handled by shared base code in `agents/base/registration.py` — new agents inherit this for free.

```python
# agents/base/registration.py (simplified)
async def register_with_control_plane(agent_url: str, control_plane_url: str):
    await client.post(f"{control_plane_url}/agents/register", json={
        "type_id": ...,
        "url": agent_url,
    })
```

The agent card (`/.well-known/agent-card.json`) is the **source of truth** for what an agent can do. It includes:
- Name and description
- Skills (typed capabilities with tags)
- Input field schema (rendered dynamically in the dashboard)

**Dynamic Discovery Mode** takes this further. The Lead Analyst can query the control plane for all online agents and use an LLM to select the most relevant specialists for a given task at runtime — no YAML wiring required:

```
Lead Analyst
    │
    ├─► GET /agents  (control plane — list all online specialists)
    │
    ├─► LLM selects N most relevant based on task description
    │
    └─► Fans out to selected specialists in parallel via LangGraph Send()
```

**Pros:**
- No topology changes needed when adding a new specialist — it auto-appears in the dashboard and becomes selectable
- The dashboard input forms render themselves from the agent card, no frontend changes needed
- Decoupled deployment: agents can be on different hosts, containers, or regions

**Cons:**
- Dynamic discovery adds an LLM call on the critical path (latency + cost)
- If the control plane is unavailable, agents still run but cannot be discovered
- Agent cards must stay in sync with actual capabilities

---

### 3. Extensible Specialist Agents — One Process, Many Agents

The `specialist_agent` server demonstrates a powerful pattern: **one process hosts N independent agents**, each defined by a YAML file.

```
specialist_agent/
├── server.py           ← mounts all agents, handles registration
├── graph.py            ← single generic LangGraph (reused by all)
├── executor.py         ← bridges A2A ↔ LangGraph
└── agent_cards/        ← one YAML per specialist
    ├── realist_ir.yaml
    ├── black_swan.yaml
    └── ... (22 total)
```

Each YAML produces a fully independent agent:

```yaml
name: Realist IR Analyst
description: Power politics, security dilemmas, balance of power
system_prompt_file: realist_ir.md
model: gpt-4o-mini
temperature: 0.3
output_format: |
  Respond as JSON with keys: assessment, key_risks, ...
```

The server auto-discovers all YAML files at startup, mounts each at its own sub-path (e.g., `/realist-ir/`), and registers each as a distinct agent with the control plane. From the outside, they look like 22 separate agents.

**Current specialist categories (22 agents):**

| Category | Count | Examples |
|---|---|---|
| Theoretical Frameworks | 11 | Realist IR, Liberal IR, Black Swan, Behavioral Economics |
| Domain Specialists | 6 | Military Strategy, Economic Statecraft, Climate Security |
| Regional Perspectives | 2 | ASEAN Security, Singapore Small-State Strategy |
| Meta-Analysis Tools | 3 | ACH Red Team, Peripheral Scan, Baseline Comparison |

**Adding a new specialist = 1 YAML file + restart:**

```yaml
# agents/specialist_agent/agent_cards/my_new_analyst.yaml
name: My New Analyst
description: What this analyst does
system_prompt: You are an expert in...
```

**Pros:**
- New analytical perspectives cost ~10 lines of YAML, no Python
- All specialists share infrastructure (LangGraph, A2A, health checks, cancellation)
- Each specialist gets its own task store, no ID collisions
- Categories scale independently — add domain specialists without touching frameworks

**Cons:**
- All specialists in one process — a crash affects all 22 agents
- No per-specialist resource isolation (CPU/memory)
- Restarting to add a specialist causes brief unavailability for all

---

### 4. A2A Wrapper Over LangGraph

LangGraph handles graph execution. A2A handles network protocol, task lifecycle, and agent discoverability. The `LangGraphA2AExecutor` base class is the seam between the two — it translates inbound A2A JSON-RPC calls into LangGraph `astream()` invocations and translates graph state updates back out as A2A `TaskStatusUpdateEvent`s.

**What the wrapper does:**

```
A2A JSON-RPC request
        │
        ▼
LangGraphA2AExecutor.execute()
  ├── Resolves task_id (control plane ID takes precedence over SDK-assigned ID)
  ├── Registers task for cancellation tracking
  ├── Emits TaskState.working
  ├── Calls graph.astream() — iterates node by node
  │       ├── After each node: check_cancelled(task_id) ← cooperative cancellation
  │       ├── Emits working status: "Running node: <name>"
  │       └── Emits NODE_OUTPUT::<node>::<state_update_json>  ← dashboard live view
  └── Emits TaskState.completed with final artifact
        │   (or TaskState.canceled / TaskState.failed on exception)
        ▼
A2A TaskStatusUpdateEvent stream
```

The cancellation model is **cooperative**: LangGraph has no native cancel-at-node boundary, so each node must call `executor.check_cancelled(task_id)` at its top. The `CancellableMixin` stores a per-task `asyncio.Event`; the cancel endpoint sets it; the next node check raises `asyncio.CancelledError`, which the executor catches and converts to a clean A2A `canceled` event.

**Subclassing is minimal.** A new agent only needs to implement one method:

```python
class MyAgentExecutor(LangGraphA2AExecutor):
    def build_graph(self) -> CompiledStateGraph:
        return my_compiled_graph   # that's it
```

Everything else — task ID resolution, status streaming, cancellation wiring, Langfuse tracing, error handling — is handled by the base class.

**Graph introspection for the dashboard** is a side-benefit of the wrapper. The executor's `get_graph_topology()` builds a fresh graph instance, walks LangGraph's internal drawable representation, and serialises nodes + edges. The dashboard uses this to render the live execution flow diagram for each agent without any agent-specific frontend code.

**Pros:**

- **Clean separation of concerns** — LangGraph owns execution logic; A2A owns transport and task lifecycle. Neither layer bleeds into the other.
- **Free infrastructure for every agent** — cancellation, status streaming, error handling, Langfuse tracing, and task ID correlation are implemented once in the base class and inherited by all agents.
- **Protocol-level interoperability** — because agents speak A2A over HTTP, any A2A-compatible client (or another agent) can call them. LangGraph is an implementation detail invisible to callers.
- **Live graph visualisation at zero cost** — `get_graph_topology()` introspects the compiled graph automatically; no manual node/edge registration needed.
- **Testability** — A2A HTTP calls are easy to mock (`pytest-httpx`); no real agent process needed in tests. LangGraph graphs can be tested independently of A2A plumbing.
- **Horizontal scaling without code changes** — the wrapper passes a control-plane-assigned `task_id` through the graph config, so multiple instances of the same agent handle tasks independently without coordination logic in graph code.

**Cons:**

- **Cooperative cancellation requires discipline** — every graph node must call `check_cancelled()` manually. Forgetting it means a cancelled task keeps running until it finishes naturally. There is no enforced contract at the framework level.
- **Streaming granularity is node-level, not token-level** — the wrapper streams a status event per node completion. Token-by-token streaming from the LLM is not surfaced to the A2A caller; you get progress updates, not a live token stream.
- **Task ID duality** — the control plane assigns its own task ID; A2A SDK assigns a separate one. The wrapper resolves this by preferring the control plane ID from message metadata, but the dual-ID model adds cognitive overhead and requires the control plane to inject `controlPlaneTaskId` into every outbound A2A message.
- **Graph state is not persisted** — if the agent process crashes mid-graph, the task is lost. LangGraph supports checkpointing, but the wrapper does not wire it up; resumability requires additional work.
- **Single-graph-per-executor assumption** — the base class builds and caches one graph per executor instance. Agents that need different graphs per task variant must either override `build_graph()` dynamically or use separate executor instances.

---

### 5. Generating New Agents with Claude's Custom Skills

The platform includes a Claude Code skill (`mission-control-new-agent`) that scaffolds all the boilerplate for a new LangGraph A2A agent. This collapses what would be a multi-hour integration task into a guided conversation.

**What the skill generates:**

```
agents/<name>/
├── graph.py       ← LangGraph graph with check_cancelled() in each node
├── executor.py    ← subclasses LangGraphA2AExecutor
├── server.py      ← FastAPI app, lifespan hooks, /graph endpoint
└── README.md      ← agent documentation
Dockerfile.<name>
docker-compose.yml  ← new service entry
run-local.sh        ← added to local runner
```

**What makes this tractable:**

1. **Conventions as constraints** — the A2A pattern is rigid enough that most boilerplate is templatable. The skill knows the exact shape.
2. **CLAUDE.md as ground truth** — the project's `CLAUDE.md` documents every convention (ports, env vars, registration pattern, graph node requirements) so the skill can reason about it accurately.
3. **Specialist agents as the fast path** — if the new agent is LLM-based with a system prompt, a YAML card in `agent_cards/` is all that's needed. The skill handles this as the trivial case.

**The development loop:**

```
Describe your agent in natural language
        │
        ▼
Claude reads CLAUDE.md + existing agents for context
        │
        ▼
Skill generates all files following established patterns
        │
        ▼
Developer reviews, adjusts system prompt / graph logic
        │
        ▼
Agent self-registers on first run — visible in dashboard immediately
```

**Pros:**
- Dramatically lowers the activation energy for adding new agents
- Enforces architectural consistency — no drift from conventions
- Non-Python contributors can add analytical specialists (YAML only)
- Documentation is generated alongside code

**Cons:**
- Generated code still requires human review — LLM output is a starting point
- Works best for agents that fit the established LangGraph + A2A mold
- Custom graph logic (multi-step, stateful agents) needs manual work

---

## The Full Pipeline — How a Task Flows

```
User submits task via Dashboard
        │
        ▼
POST /agents/{type_id}/tasks          (Control Plane)
        │
        ├─► Returns 202 immediately
        │
        ▼
Background asyncio task dispatches to agent via A2A JSON-RPC
        │
        ▼
Agent executes LangGraph
  ├── Each node emits TaskStatusUpdateEvent
  ├── Each node calls check_cancelled() for clean mid-run cancellation
  └── Final node writes output
        │
        ▼
Control Plane publishes events to WebSocket subscribers
        │
        ▼
Dashboard receives live updates, renders final result
```

For the Lead Analyst, this fans out to N specialists in parallel, then runs:

```
Domain Specialists (parallel)
        │
        ▼
Peripheral Scan (catch blind spots BEFORE consensus forms)
        │
        ▼
Aggregation (LLM meta-analysis)
        │
        ▼
ACH Red Team (challenge consensus with alternative hypotheses)
        │
        ▼
Baseline Comparison (delta from prior assessments, if provided)
        │
        ▼
Final Synthesis (balanced assessment for decision-makers)
```

---

## Scalability Knobs

| Concern | Mechanism |
|---|---|
| Multiple agent instances | Self-register multiple times; registry load-balances automatically |
| Persistent task history | Set `DATABASE_URL` → PostgreSQL task store |
| Multi-instance control plane | Set `REDIS_URL` → Redis pub/sub for WebSocket fan-out |
| Cross-host agents | Each agent reads its own `*_AGENT_URL` env var for its external address |

---

## What We'd Do Differently (Known Gaps)

| Gap | Plan |
|---|---|
| No auth on control plane routes | Auth middleware + API keys (planned) |
| No rate limiting or circuit breaker | Production hardening pass (planned) |
| Task TTL not enforced | Needs background sweep |
| `INPUT_REQUIRED` state defined but not handled | Partial implementation in routes |
| Specialist process is a single point of failure | Could split into separate containers per category |

---

## Summary

| Pattern | What it solves |
|---|---|
| **Control plane as registry** | Decouples deployment from routing; enables zero-config scaling |
| **Self-registration** | Agents own their lifecycle; control plane stays stateless |
| **Dynamic discovery** | Orchestrators can select tools at runtime without hardcoded wiring |
| **YAML-driven specialists** | New analytical perspectives with no Python, no infra changes |
| **Claude skill scaffolding** | Collapses multi-hour agent onboarding into a guided conversation |
