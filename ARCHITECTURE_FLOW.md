# Mission Control - High-Level Architecture Flow

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
│  │ • 3 leads (A/B/C)│  │ • 16 analysts  │  │ • Disagreement detect │  │
│  │ • Fan-out to     │◄─┤ • Analytical   │◄─┤ • Peripheral scan     │  │
│  │   specialists    │  │   frameworks   │  │ • Structured output   │  │
│  │ • Aggregate      │  │                │  │                        │  │
│  └──────────────────┘  └────────────────┘  └────────────────────────┘  │
│                                                                           │
│  Each agent exposes:                                                      │
│  • /.well-known/agent-card.json  → Metadata                              │
│  • POST /execute                  → A2A JSON-RPC endpoint                │
│  • GET  /graph                    → Topology & input_fields              │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘


                            ┌────────────────┐
                            │   Data Flow    │
                            └────────────────┘

1. User submits task via Dashboard or API
         │
         ▼
2. Control Plane receives task, stores it (state: submitted)
         │
         ▼
3. Registry routes task to agent using load balancing
         │
         ▼
4. Control Plane sends A2A JSON-RPC message to agent
         │
         ▼
5. Agent executes LangGraph workflow
         │  • Emits TaskStatusUpdateEvent at each node
         │  • Checks for cancellation signals
         │  • May call other agents via A2A
         │
         ▼
6. Control Plane updates task store (state: working → completed/failed)
         │
         ▼
7. Pub/Sub broadcasts updates via WebSocket to Dashboard
         │
         ▼
8. User sees live updates and final result


                         ┌──────────────────┐
                         │  Key Features    │
                         └──────────────────┘

• Async Dispatch:     202 Accepted, background execution
• Load Balancing:     Least-active-tasks routing
• Self-Registration:  Agents register on startup, deregister on shutdown
• Health Monitoring:  30s polling, auto-removal on failure
• Cancellation:       Mid-run task cancellation via asyncio events
• Observability:      Langfuse tracing, WebSocket live updates
• Scalability:        Horizontal agent scaling, Redis pub/sub
• A2A Compliance:     JSON-RPC 2.0, standard message formats
• Dynamic Forms:      Dashboard renders inputs from agent metadata


                         ┌──────────────────┐
                         │  Agent Graph     │
                         └──────────────────┘

Each agent has a LangGraph workflow with cancellable nodes:

┌─────────┐     ┌──────────┐     ┌────────────┐     ┌──────────┐
│  Start  │────►│  Node 1  │────►│   Node 2   │────►│  Output  │
└─────────┘     └──────────┘     └────────────┘     └──────────┘
                     │                  │
                     ├──check_cancelled()
                     └──emit TaskStatusUpdateEvent
