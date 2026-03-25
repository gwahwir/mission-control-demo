"""Probability Forecasting agent server.

Takes concatenated specialist analyses as input, performs probability
aggregation with equal-weighted averaging, disagreement detection,
peripheral intelligence scanning, and generates a structured probability
briefing.

Run with:
    OPENAI_API_KEY=sk-... python -m agents.probability_agent.server

Environment variables:
    CONTROL_PLANE_URL           – Optional. Control plane URL for self-registration.
    PROBABILITY_AGENT_URL       – Optional. This server's externally-reachable URL.
    OPENAI_API_KEY              – Required for LLM-powered analysis.
    OPENAI_BASE_URL             – Optional. Custom OpenAI-compatible base URL.
    OPENAI_MODEL                – Optional. LLM model (default: gpt-4o-mini).
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
from agents.probability_agent.executor import ProbabilityExecutor
from dotenv import load_dotenv

load_dotenv()

AGENT_TYPE = "probability-forecaster"
AGENT_PORT = 8007

INPUT_FIELDS = [
    {
        "name": "text",
        "label": "Concatenated Specialist Analyses",
        "type": "textarea",
        "required": True,
        "placeholder": (
            "Paste the concatenated output from specialist agents "
            "(e.g., lead analyst output combining multiple framework analyses)..."
        ),
    },
]


def _get_agent_url() -> str:
    return os.getenv(
        "PROBABILITY_AGENT_URL",
        os.getenv("AGENT_URL", f"http://localhost:{AGENT_PORT}"),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    agent_url = _get_agent_url()
    await register_with_control_plane(AGENT_TYPE, agent_url)
    yield
    await deregister_from_control_plane(AGENT_TYPE, agent_url)


def create_app() -> FastAPI:
    app = FastAPI(title="Probability Forecasting Agent", lifespan=lifespan)

    executor = ProbabilityExecutor()
    task_store = InMemoryTaskStore()
    request_handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=task_store,
    )

    agent_url = _get_agent_url()
    agent_card = AgentCard(
        name="Probability Forecaster",
        description=(
            "Probability-based scenario forecasting agent. Takes concatenated specialist "
            "analyses as input and produces structured probability briefings with "
            "equal-weighted aggregation, disagreement detection, peripheral signal "
            "scanning, and tail-risk reserves."
        ),
        version="1.0.0",
        url=agent_url,
        capabilities=AgentCapabilities(streaming=True, push_notifications=False),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[
            AgentSkill(
                id="probability-forecast",
                name="Probability Forecasting",
                description=(
                    "Aggregates specialist analyses into scenario probability assessments "
                    "with structured disagreement detection and peripheral signal scanning."
                ),
                tags=["probability", "forecasting", "aggregation", "intelligence", "analysis"],
            ),
        ],
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
