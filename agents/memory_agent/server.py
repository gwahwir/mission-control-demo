"""Standalone A2A server for the Memory Agent.

Run with:
    python -m agents.memory_agent.server

Environment variables (all required unless noted):
    MEMORY_NEO4J_URL         – Neo4j bolt URL
    MEMORY_NEO4J_USER        – Neo4j username
    MEMORY_NEO4J_PASSWORD    – Neo4j password
    MEMORY_PG_DSN            – pgvector-enabled PostgreSQL DSN
    MEMORY_EMBEDDING_MODEL   – Embedding model name (e.g. text-embedding-3-small)
    MEMORY_EMBEDDING_DIMS    – Vector dimensions matching the embedding model
    OPENAI_API_KEY           – Required for LLM extraction and embeddings
    OPENAI_BASE_URL          – Optional. Custom OpenAI-compatible base URL
    OPENAI_MODEL             – Optional. LLM model (default: gpt-4o-mini)
    CONTROL_PLANE_URL        – Optional. Control plane URL for self-registration
    MEMORY_AGENT_URL         – Optional. This agent's externally-reachable URL
"""
from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager

import uvicorn
from a2a.server.apps.jsonrpc import A2AFastAPIApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from dotenv import load_dotenv
from fastapi import FastAPI

from agents.base.registration import deregister_from_control_plane, register_with_control_plane
from agents.memory_agent.executor import MemoryAgentExecutor

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)

AGENT_TYPE = "memory-agent"
AGENT_PORT = 8009

INPUT_FIELDS = [
    {
        "name": "namespace",
        "label": "Namespace",
        "type": "text",
        "required": True,
        "placeholder": "e.g. lead_analyst",
    },
    {
        "name": "text",
        "label": "Text (write)",
        "type": "textarea",
        "required": False,
        "placeholder": "Raw text to ingest (write operation)",
    },
    {
        "name": "query",
        "label": "Query (search)",
        "type": "text",
        "required": False,
        "placeholder": "Semantic search query",
    },
    {
        "name": "entity",
        "label": "Entity (traverse)",
        "type": "text",
        "required": False,
        "placeholder": "Entity name to traverse from",
    },
    {
        "name": "limit",
        "label": "Limit (search, default 5)",
        "type": "number",
        "required": False,
    },
    {
        "name": "depth",
        "label": "Depth (traverse, default 2)",
        "type": "number",
        "required": False,
    },
]

agent_card = AgentCard(
    name="Memory Agent",
    description=(
        "Dual-store memory agent (pgvector + Neo4j). Supports three operations: "
        "write (raw text → LLM extraction → persisted memories), "
        "search (semantic vector search by namespace), and "
        "traverse (graph walk from a named entity)."
    ),
    version="0.1.0",
    url=f"http://localhost:{AGENT_PORT}",
    capabilities=AgentCapabilities(streaming=True, push_notifications=False),
    default_input_modes=["application/json"],
    default_output_modes=["application/json"],
    skills=[
        AgentSkill(
            id="memory/write",
            name="Write Memory",
            description="Ingest raw text, extract entities and relationships, store in pgvector + Neo4j",
            tags=["memory", "write", "neo4j", "pgvector"],
        ),
        AgentSkill(
            id="memory/search",
            name="Search Memory",
            description="Semantic search over stored memories within a namespace",
            tags=["memory", "search", "pgvector"],
        ),
        AgentSkill(
            id="memory/traverse",
            name="Traverse Graph",
            description="Graph traversal from a named entity within a namespace",
            tags=["memory", "traverse", "neo4j"],
        ),
    ],
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    agent_url = os.getenv(
        "MEMORY_AGENT_URL",
        os.getenv("AGENT_URL", f"http://localhost:{AGENT_PORT}"),
    )
    await register_with_control_plane(AGENT_TYPE, agent_url)
    yield
    await deregister_from_control_plane(AGENT_TYPE, agent_url)


def create_app() -> FastAPI:
    app = FastAPI(title="Memory Agent A2A Server", lifespan=lifespan)
    agent_url = os.getenv(
        "MEMORY_AGENT_URL",
        os.getenv("AGENT_URL", f"http://localhost:{AGENT_PORT}"),
    )

    executor = MemoryAgentExecutor()
    task_store = InMemoryTaskStore()
    request_handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=task_store,
    )

    a2a_app = A2AFastAPIApplication(agent_card=agent_card, http_handler=request_handler)
    a2a_app.add_routes_to_app(app)

    @app.get("/graph")
    async def get_graph():
        topology = executor.get_graph_topology()
        topology["input_fields"] = INPUT_FIELDS
        return topology

    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=AGENT_PORT, timeout_graceful_shutdown=15)
