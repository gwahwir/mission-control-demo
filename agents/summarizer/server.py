"""Standalone A2A server for the Summarizer agent.

Run with:
    python -m agents.summarizer.server

Environment variables:
    OPENAI_API_KEY  – Required. Your OpenAI API key.
    OPENAI_MODEL    – Model to use (default: gpt-4o-mini).
"""

from __future__ import annotations

import uvicorn
from a2a.server.apps.jsonrpc import A2AFastAPIApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from fastapi import FastAPI

from agents.summarizer.executor import SummarizerExecutor

AGENT_PORT = 8002

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


def create_app() -> FastAPI:
    app = FastAPI(title="Summarizer Agent A2A Server")

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
    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=AGENT_PORT)
