"""Multi-instance Lead Analyst server — hosts multiple orchestrators from YAML configs.

Each analyst is defined by a YAML file in the ``analyst_configs/`` directory.
Analysts get their own A2A sub-path (e.g. ``/lead-analyst/``) and register
independently with the control plane.

Run with:
    python -m agents.lead_analyst.server

Environment variables:
    CONTROL_PLANE_URL      – Optional. Control plane URL for self-registration.
    LEAD_ANALYST_AGENT_URL – Optional. This server's externally-reachable base URL.
    OPENAI_API_KEY         – Required for LLM-powered aggregation.
"""

from __future__ import annotations

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
from agents.lead_analyst.config import LeadAnalystConfig, load_lead_analyst_configs
from agents.lead_analyst.executor import LeadAnalystExecutor
from dotenv import load_dotenv

load_dotenv()

AGENT_PORT = 8005
ANALYST_CONFIGS_DIR = Path(__file__).parent / "analyst_configs"

# Track mounted analysts for lifespan management
_analysts: list[dict] = []


def _get_base_url() -> str:
    return os.getenv(
        "LEAD_ANALYST_AGENT_URL",
        os.getenv("AGENT_URL", f"http://localhost:{AGENT_PORT}"),
    )


def _build_agent_card(config: LeadAnalystConfig, base_url: str) -> AgentCard:
    """Build an A2A AgentCard from a LeadAnalystConfig."""
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
            tags=["orchestration", "analysis", "fan-out"],
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


def _mount_analyst(app: FastAPI, config: LeadAnalystConfig, base_url: str) -> None:
    """Mount a single analyst's A2A routes and graph endpoint onto the app."""
    executor = LeadAnalystExecutor(config)
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
    input_fields = config.input_fields

    @app.get(f"/{type_id}/graph", name=f"graph_{type_id}")
    async def get_graph(_executor=executor, _fields=input_fields, _config=config):
        topology = _executor.get_graph_topology()
        topology["input_fields"] = _fields

        # Expose downstream connections for cross-agent edge resolution
        downstream_agents = [
            {"from_node": sa.node_id, "agent_url": sa.url}
            for sa in _config.sub_agents
        ]
        if downstream_agents:
            topology["downstream_agents"] = downstream_agents

        return topology

    _analysts.append({
        "type_id": type_id,
        "name": config.name,
        "description": config.description,
        "url": f"{base_url}/{type_id}",
    })

    logger.info("Mounted %s at /%s/", type_id, type_id)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Register each analyst independently
    for analyst in _analysts:
        await register_with_control_plane(analyst["type_id"], analyst["url"])
    yield
    # Deregister all on shutdown
    for analyst in _analysts:
        await deregister_from_control_plane(analyst["type_id"], analyst["url"])


def create_app() -> FastAPI:
    _analysts.clear()
    app = FastAPI(title="Lead Analyst Agent Server", lifespan=lifespan)
    base_url = _get_base_url()

    configs = load_lead_analyst_configs(ANALYST_CONFIGS_DIR)
    for config in configs:
        _mount_analyst(app, config, base_url)

    @app.get("/")
    async def list_analysts():
        return {
            "analysts": _analysts,
            "count": len(_analysts),
        }

    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=AGENT_PORT)
