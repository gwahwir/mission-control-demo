"""Standalone A2A server for the Relevancy agent.

Run with:
    python -m agents.relevancy.server

Environment variables:
    OPENAI_API_KEY    – Required. Your OpenAI API key.
    OPENAI_BASE_URL   – Optional. Custom OpenAI-compatible base URL.
    OPENAI_MODEL      – Model to use (default: gpt-4o-mini).
    CONTROL_PLANE_URL – Optional. Control plane URL for self-registration.
    AGENT_URL         – Optional. This agent's externally-reachable URL.
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
from agents.extraction_agent.executor import ExtractionExecutor
from dotenv import load_dotenv
load_dotenv()

AGENT_TYPE = "extraction"
AGENT_PORT = 8004


INPUT_FIELDS = [
    {
        "name": "text",
        "label": "Text",
        "type": "textarea",
        "required": True,
        "placeholder": "Paste the article or text to extract information...",
    }
]

agent_card = AgentCard(
    name="Extraction Agent",
    description=(
        "Extracts and Returns a JSON result with structured data schema"
    ),
    version="0.1.0",
    url=f"http://localhost:{AGENT_PORT}",
    capabilities=AgentCapabilities(
        streaming=True,
        push_notifications=False,
    ),
    default_input_modes=["application/json"],
    default_output_modes=["application/json"],
    skills=[
        AgentSkill(
            id="extraction",
            name="Information Extraction",
            description="Extracts information out from a blob of text",
            tags=["extraction", "llm", "analysis"],
        ),
    ],
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    agent_url = os.getenv("EXTRACTION_AGENT_URL", os.getenv("AGENT_URL", f"http://localhost:{AGENT_PORT}"))
    await register_with_control_plane(AGENT_TYPE, agent_url)
    yield
    await deregister_from_control_plane(AGENT_TYPE, agent_url)


def create_app() -> FastAPI:
    app = FastAPI(title="Extraction Agent A2A Server", lifespan=lifespan)
    agent_url = os.getenv("EXTRACTION_AGENT_URL", os.getenv("AGENT_URL", f"http://localhost:{AGENT_PORT}"))
    logger.info("Agent address: %s", agent_url)

    executor = ExtractionExecutor()
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
