"""Standalone A2A server for the Wiki Agent.

Run with:
    python -m agents.wiki_agent.server

Environment variables:
    WIKI_AGENT_URL      – externally-reachable URL (default: http://localhost:8011)
    WIKI_DIR            – directory where .md files are written (required)
    SUMMARIZER_URL      – summarizer agent URL (default: http://localhost:8002)
    EXTRACTION_URL      – extraction agent URL (default: http://localhost:8004)
    MEMORY_AGENT_URL    – memory agent URL (default: http://localhost:8009)
    BASELINE_URL        – baseline store URL (default: http://localhost:8010)
    OPENAI_API_KEY      – required for LLM page-writing
    OPENAI_BASE_URL     – optional: custom OpenAI-compatible base URL
    OPENAI_MODEL        – optional: LLM model (default: gpt-4o-mini)
    CONTROL_PLANE_URL   – optional: control plane URL for self-registration
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
from agents.wiki_agent.executor import WikiAgentExecutor

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)

AGENT_TYPE = "wiki-agent"
AGENT_PORT = 8011

INPUT_FIELDS = [
    {
        "name": "input_text",
        "label": "Source Text (ingest)",
        "type": "textarea",
        "required": False,
        "placeholder": "Raw source text to ingest into the wiki (triggers ingest operation)",
    },
    {
        "name": "source_url",
        "label": "Source URL (ingest, optional)",
        "type": "text",
        "required": False,
        "placeholder": "https://...",
    },
    {
        "name": "source_title",
        "label": "Source Title (ingest, optional)",
        "type": "text",
        "required": False,
        "placeholder": "Article or report title",
    },
    {
        "name": "namespace",
        "label": "Wiki Namespace (ingest, default: wiki_geo)",
        "type": "text",
        "required": False,
        "placeholder": "wiki_geo",
    },
    {
        "name": "query",
        "label": "Query (query operation)",
        "type": "text",
        "required": False,
        "placeholder": "What is Iran's current nuclear posture? (triggers query operation)",
    },
    {
        "name": "save_as_page",
        "label": "Save Answer as Wiki Page (query only)",
        "type": "checkbox",
        "required": False,
    },
]

agent_card = AgentCard(
    name="Wiki Agent",
    description=(
        "LLM-maintained intelligence wiki for geopolitics/IR topics. "
        "Three operations (inferred from input): "
        "ingest (input_text → summarize → extract → update related pages → write to disk + baseline_store + memory), "
        "query (query → semantic search + LLM synthesis with citations), "
        "lint (no input → health-check: orphan pages, stale pages, contradictions, suggestions)."
    ),
    version="0.1.0",
    url=f"http://localhost:{AGENT_PORT}",
    capabilities=AgentCapabilities(streaming=True, push_notifications=False),
    default_input_modes=["application/json"],
    default_output_modes=["application/json"],
    skills=[
        AgentSkill(
            id="wiki/ingest",
            name="Ingest Source",
            description=(
                "Ingest raw text into the wiki: summarize → extract entities → find related pages → "
                "update/create pages → write .md files to disk → store in baseline_store + memory_agent"
            ),
            tags=["wiki", "ingest", "write"],
        ),
        AgentSkill(
            id="wiki/query",
            name="Query Wiki",
            description="Semantic search over wiki pages + LLM synthesis with inline citations",
            tags=["wiki", "query", "search"],
        ),
        AgentSkill(
            id="wiki/lint",
            name="Lint Wiki",
            description=(
                "Health-check the wiki: find orphan/stale pages, detect potential contradictions, "
                "suggest new pages to create"
            ),
            tags=["wiki", "lint", "health"],
        ),
    ],
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    agent_url = os.getenv(
        "WIKI_AGENT_URL",
        os.getenv("AGENT_URL", f"http://localhost:{AGENT_PORT}"),
    )
    await register_with_control_plane(AGENT_TYPE, agent_url)
    yield
    await deregister_from_control_plane(AGENT_TYPE, agent_url)


def create_app() -> FastAPI:
    app = FastAPI(title="Wiki Agent A2A Server", lifespan=lifespan)

    executor = WikiAgentExecutor()
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
