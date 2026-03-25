# baseline_store/server.py
from __future__ import annotations
import logging
import os
import sys
from contextlib import asynccontextmanager
from fastapi import FastAPI
import uvicorn
from dotenv import load_dotenv
from baseline_store.routes import router

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from baseline_store.stores import get_pgvector_pool
    await get_pgvector_pool()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Baseline Store", lifespan=lifespan)
    app.include_router(router)
    return app


app = create_app()

if __name__ == "__main__":
    port = int(os.getenv("BASELINE_PORT", "8010"))
    uvicorn.run(app, host="0.0.0.0", port=port, timeout_graceful_shutdown=15)
