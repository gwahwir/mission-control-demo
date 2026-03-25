"""Multi-agent specialist server — hosts multiple LLM agents from a single deployment.

Each specialist is defined by a YAML file in the ``agent_cards/`` directory.
Specialists get their own A2A sub-path (e.g. ``/code-reviewer/``) and register
independently with the control plane.

Run with:
    python -m agents.specialist_agent.server

Environment variables:
    SPECIALIST_AGENT_PORT  – Port to listen on (default: 8006)
    SPECIALIST_AGENT_URL   – Externally-reachable base URL
    AGENT_URL              – Fallback base URL
    OPENAI_API_KEY         – Required for LLM calls
    OPENAI_MODEL           – Default model (default: gpt-4o-mini)
    CONTROL_PLANE_URL      – Control plane URL for self-registration
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

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
from agents.specialist_agent.config import SpecialistConfig, load_specialist_configs
from agents.specialist_agent.executor import SpecialistExecutor
from dotenv import load_dotenv

load_dotenv()

AGENT_PORT = int(os.getenv("SPECIALIST_AGENT_PORT", "8006"))
AGENT_CARDS_DIR = Path(__file__).parent / "agent_cards"

# Track mounted specialists for lifespan management
_specialists: list[dict] = []


def _get_base_url() -> str:
    return os.getenv(
        "SPECIALIST_AGENT_URL",
        os.getenv("AGENT_URL", f"http://localhost:{AGENT_PORT}"),
    )


def _build_agent_card(config: SpecialistConfig, base_url: str) -> AgentCard:
    """Build an A2A AgentCard from a SpecialistConfig."""
    skills = [
        AgentSkill(
            id=s.get("id", config.type_id),
            name=s.get("name", config.name),
            description=s.get("description", config.description),
            tags=s.get("tags", []),
        )
        for s in config.skills
    ] or [
        AgentSkill(
            id=config.type_id,
            name=config.name,
            description=config.description,
            tags=[],
        )
    ]

    return AgentCard(
        name=config.name,
        description=config.description,
        version=config.version,
        url=f"{base_url}/{config.type_id}",
        capabilities=AgentCapabilities(
            streaming=True,
            push_notifications=False,
        ),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=skills,
    )


def _mount_specialist(app: FastAPI, config: SpecialistConfig, base_url: str) -> None:
    """Mount a single specialist's A2A routes and graph endpoint onto the app."""
    executor = SpecialistExecutor(config)
    task_store = InMemoryTaskStore()
    request_handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=task_store,
    )

    agent_card = _build_agent_card(config, base_url)
    a2a_app = A2AFastAPIApplication(
        agent_card=agent_card,
        http_handler=request_handler,
    )

    type_id = config.type_id
    a2a_app.add_routes_to_app(
        app,
        agent_card_url=f"/{type_id}/.well-known/agent-card.json",
        rpc_url=f"/{type_id}/",
    )

    # Graph introspection endpoint
    input_fields = list(config.input_fields)
    if not any(f.get("name") == "key_questions" for f in input_fields):
        input_fields.append({
            "name": "key_questions",
            "label": "Key Questions (optional)",
            "type": "textarea",
            "required": False,
            "placeholder": "Specific questions this analysis should address...",
        })

    @app.get(f"/{type_id}/graph", name=f"graph_{type_id}")
    async def get_graph(_executor=executor, _fields=input_fields):
        topology = _executor.get_graph_topology()
        topology["input_fields"] = _fields
        return topology

    _specialists.append({
        "type_id": type_id,
        "name": config.name,
        "description": config.description,
        "url": f"{base_url}/{type_id}",
    })

    logger.info("Mounted %s at /%s/", type_id, type_id)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Register all specialists concurrently
    await asyncio.gather(
        *[register_with_control_plane(spec["type_id"], spec["url"]) for spec in _specialists]
    )
    yield
    # Deregister all concurrently on shutdown
    await asyncio.gather(
        *[deregister_from_control_plane(spec["type_id"], spec["url"]) for spec in _specialists]
    )


def create_app() -> FastAPI:
    _specialists.clear()
    app = FastAPI(title="Specialist Agent Server", lifespan=lifespan)
    base_url = _get_base_url()

    configs = load_specialist_configs(AGENT_CARDS_DIR)
    for config in configs:
        _mount_specialist(app, config, base_url)

    @app.get("/")
    async def list_specialists():
        return {
            "specialists": _specialists,
            "count": len(_specialists),
        }

    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=AGENT_PORT)
