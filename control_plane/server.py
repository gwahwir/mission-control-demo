"""Control Plane FastAPI application.

Run with:
    python -m control_plane.server
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from control_plane.config import load_settings
from control_plane.registry import AgentRegistry
from control_plane.routes import init_routes, router
from control_plane.task_store import TaskStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

settings = load_settings()
registry = AgentRegistry(poll_interval=settings.health_poll_interval_seconds)
task_store = TaskStore()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: discover agents
    logger.info("Discovering %d configured agents…", len(settings.agents))
    await registry.register_all(settings.agents)
    registry.start_polling()

    online = sum(1 for a in registry.agents.values() if a.status.value == "online")
    logger.info(
        "Registry ready: %d/%d agents online", online, len(registry.agents)
    )

    yield

    # Shutdown
    await registry.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Mission Control — Control Plane",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    init_routes(registry, task_store)
    app.include_router(router)
    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host=settings.host, port=settings.port)
