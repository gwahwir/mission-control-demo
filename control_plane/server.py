"""Control Plane FastAPI application.

Run with:
    python -m control_plane.server
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from control_plane.config import load_settings
from control_plane.log import CorrelationIdMiddleware, configure_logging, get_logger
from control_plane.metrics import instrument_app
from control_plane.pubsub import InMemoryBroker, RedisBroker
from control_plane.registry import AgentRegistry
from control_plane.routes import init_routes, router
from control_plane.task_store import PostgresTaskStore, TaskStore

configure_logging(log_level=os.getenv("LOG_LEVEL", "INFO"))
logger = get_logger(__name__)

settings = load_settings()
registry = AgentRegistry(poll_interval=settings.health_poll_interval_seconds)

# Task store: Postgres if DATABASE_URL is set, in-memory otherwise
if settings.database_url:
    task_store = PostgresTaskStore()
    logger.info("task_store_backend", backend="postgresql")
else:
    task_store = TaskStore()
    logger.info("task_store_backend", backend="in-memory")

# Pub/sub broker: Redis if REDIS_URL is set, in-memory otherwise
if settings.redis_url:
    broker = RedisBroker(settings.redis_url)
    logger.info("pubsub_backend", backend="redis")
else:
    broker = InMemoryBroker()
    logger.info("pubsub_backend", backend="in-memory")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Postgres
    if isinstance(task_store, PostgresTaskStore):
        await task_store.init(settings.database_url)
        logger.info("postgres_connected", url=settings.database_url.split("@")[-1])

    # Redis
    if isinstance(broker, RedisBroker):
        await broker.init()
        logger.info("redis_connected", url=settings.redis_url)

    # Agent discovery
    logger.info("startup", agent_count=len(settings.agents))
    await registry.register_all(settings.agents)
    registry.start_polling()

    online = sum(
        1
        for t in registry.agents.values()
        for i in t.instances
        if i.status.value == "online"
    )
    total = sum(len(t.instances) for t in registry.agents.values())
    logger.info("registry_ready", online=online, total=total)

    yield

    await registry.close()
    if isinstance(task_store, PostgresTaskStore):
        await task_store.close()
    await broker.close()
    logger.info("shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Mission Control — Control Plane",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(CorrelationIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    init_routes(registry, task_store, broker)
    app.include_router(router)
    instrument_app(app)

    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host=settings.host, port=settings.port)
