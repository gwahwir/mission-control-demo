"""Standalone A2A server for the Echo agent.

Run with:
    python -m agents.echo.server
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
from agents.echo.executor import EchoAgentExecutor
from dotenv import load_dotenv
load_dotenv()

AGENT_TYPE = "echo-agent"
AGENT_PORT = 8001

INPUT_FIELDS = [
    {
        "name": "text",
        "label": "Message",
        "type": "text",
        "required": True,
        "placeholder": "Type a message to echo...",
    },
]

agent_card = AgentCard(
    name="Echo Agent",
    description="A proof-of-concept agent that echoes messages back in uppercase. "
    "Demonstrates LangGraph + A2A integration with cancellation support.",
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
            id="echo",
            name="Echo",
            description="Echoes the user message back in uppercase",
            tags=["echo", "demo"],
        ),
    ],
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    agent_url = os.getenv("ECHO_AGENT_URL", os.getenv("AGENT_URL", f"http://localhost:{AGENT_PORT}"))
    await register_with_control_plane(AGENT_TYPE, agent_url)
    yield
    await deregister_from_control_plane(AGENT_TYPE, agent_url)


def create_app() -> FastAPI:
    app = FastAPI(title="Echo Agent A2A Server", lifespan=lifespan)

    executor = EchoAgentExecutor()
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
        downstream_url = os.getenv("DOWNSTREAM_AGENT_URL", "")
        if downstream_url:
            topology["downstream"] = {
                "from_node": "forward_downstream",
                "agent_url": downstream_url,
            }
        return topology

    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=AGENT_PORT)
