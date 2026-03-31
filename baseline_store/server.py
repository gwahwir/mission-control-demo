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


_DESCRIPTION = """
Deterministic storage and retrieval layer for **topic baselines** — versioned narrative snapshots
of what is currently understood to be true about a subject.

## Intended use by agents

1. **Before analysis** — call `GET /baselines/{topic}/current` to retrieve the established baseline
   so the analysis focuses on what has *changed*, not re-deriving known facts.
2. **After analysis** — call `POST /baselines/{topic}/versions` to persist the updated narrative,
   then `POST /baselines/{topic}/deltas` to record what changed and why.
3. **Cross-domain discovery** — call `GET /baselines/similar?query=...` to find semantically related
   topics across the full knowledge base.
4. **Multi-topic context** — call `GET /baselines/{topic}/rollup` to fetch current baselines for all
   sub-topics under a parent path in one request.

## Topic paths

Topics use dot-separated `ltree` format: `geo`, `geo.middle_east`, `geo.middle_east.iran`.
Each segment must be registered via `POST /topics` before baselines can be written for it.
"""


def create_app() -> FastAPI:
    app = FastAPI(
        title="Baseline Store",
        description=_DESCRIPTION,
        version="1.0.0",
        lifespan=lifespan,
    )
    app.include_router(router)
    return app


app = create_app()

if __name__ == "__main__":
    port = int(os.getenv("BASELINE_PORT", "8010"))
    uvicorn.run(app, host="0.0.0.0", port=port, timeout_graceful_shutdown=15)
