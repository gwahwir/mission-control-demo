"""Standalone A2A server for the Summarizer agent.

Run with:
    python -m agents.summarizer.server

Environment variables:
    OPENAI_API_KEY  – Required. Your OpenAI API key.
    OPENAI_MODEL    – Model to use (default: gpt-4o-mini).
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

import uvicorn
from a2a.server.apps.jsonrpc import A2AFastAPIApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from fastapi import FastAPI

from agents.base.registration import deregister_from_control_plane, register_with_control_plane
from agents.summarizer.executor import SummarizerExecutor
from dotenv import load_dotenv
load_dotenv()

AGENT_TYPE = "summarizer"
AGENT_PORT = 8002

INPUT_FIELDS = [
    {
        "name": "text",
        "label": "Text to Summarize",
        "type": "textarea",
        "required": True,
        "placeholder": "Paste the text to summarize...",
    },
]

agent_card = AgentCard(
    name="Summarizer Agent",
    description=(
        "Receives text and uses OpenAI to produce a concise summary. "
        "Designed to be called by upstream agents that forward their output here."
    ),
    version="0.1.0",
    url=f"http://localhost:{AGENT_PORT}",
    capabilities=AgentCapabilities(
        streaming=True,
        push_notifications=False,
    ),
    default_input_modes=["text/plain"],
    default_output_modes=["text/plain"],
    skills=[
        AgentSkill(
            id="summarize",
            name="Summarize",
            description="Summarizes text using OpenAI after optionally fetching from an upstream A2A agent",
            tags=["summarize", "openai", "llm"],
        ),
    ],
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    agent_url = os.getenv("SUMMARIZER_AGENT_URL", os.getenv("AGENT_URL", f"http://localhost:{AGENT_PORT}"))
    await register_with_control_plane(AGENT_TYPE, agent_url)
    yield
    await deregister_from_control_plane(AGENT_TYPE, agent_url)


def create_app() -> FastAPI:
    app = FastAPI(title="Summarizer Agent A2A Server", lifespan=lifespan)

    executor = SummarizerExecutor()
    task_store = InMemoryTaskStore()
    request_handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=task_store,
    )

    a2a_app = A2AFastAPIApplication(
        agent_card=agent_card,
        http_handler=request_handler,
    )
    a2a_app.add_routes_to_app(app)

    @app.get("/graph")
    async def get_graph():
        topology = executor.get_graph_topology()
        topology["input_fields"] = INPUT_FIELDS
        return topology

    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=AGENT_PORT)
