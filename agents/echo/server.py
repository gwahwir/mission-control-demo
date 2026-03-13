"""Standalone A2A server for the Echo agent.

Run with:
    python -m agents.echo.server
"""

from __future__ import annotations

import uvicorn
from a2a.server.apps.jsonrpc import A2AFastAPIApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from fastapi import FastAPI

from agents.echo.executor import EchoAgentExecutor

AGENT_PORT = 8001

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


def create_app() -> FastAPI:
    app = FastAPI(title="Echo Agent A2A Server")

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
    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=AGENT_PORT)
