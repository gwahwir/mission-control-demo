# baseline_store/stores.py
"""Lazy singletons for asyncpg connection pool and OpenAI embedder.

Call get_pgvector_pool() to get the pool (runs DDL on first call).
Call get_embedder() to get an async embed(text) -> list[float] callable.
Both raise EnvironmentError if required env vars are missing.
"""
from __future__ import annotations

import asyncio
import os
import logging
import threading
from typing import Callable, Optional

import asyncpg
from openai import AsyncOpenAI
from langchain_community.embeddings import JinaEmbeddings

logger = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None
_embedder: Optional[Callable] = None

_pool_lock: asyncio.Lock | None = None
_embedder_lock = threading.Lock()


def _get_pool_lock() -> asyncio.Lock:
    global _pool_lock
    if _pool_lock is None:
        _pool_lock = asyncio.Lock()
    return _pool_lock


def _get_dims() -> int:
    raw = os.getenv("BASELINE_EMBEDDING_DIMS")
    if not raw:
        raise EnvironmentError("BASELINE_EMBEDDING_DIMS is required and must match the embedding model output dimensions")
    try:
        dims = int(raw)
    except ValueError:
        raise EnvironmentError(
            f"BASELINE_EMBEDDING_DIMS must be a positive integer, got: {raw!r}"
        )
    if dims <= 0:
        raise EnvironmentError(
            f"BASELINE_EMBEDDING_DIMS must be a positive integer, got: {dims}"
        )
    return dims


# DDL — executed once on first pool access
def _build_ddl(dims: int) -> str:
    return f"""
CREATE EXTENSION IF NOT EXISTS ltree;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS baseline_topics (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    topic_path   ltree NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    created_at   TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS baseline_topics_path_gist
    ON baseline_topics USING GIST (topic_path);

CREATE TABLE IF NOT EXISTS baseline_versions (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    topic_path     ltree NOT NULL,
    version_number INTEGER NOT NULL,
    narrative      TEXT NOT NULL,
    embedding      vector({dims}),
    citations      JSONB DEFAULT '[]',
    created_at     TIMESTAMPTZ DEFAULT now(),
    UNIQUE (topic_path, version_number)
);
CREATE INDEX IF NOT EXISTS baseline_versions_topic_gist
    ON baseline_versions USING GIST (topic_path);
CREATE INDEX IF NOT EXISTS baseline_versions_topic_version
    ON baseline_versions (topic_path, version_number DESC);
CREATE INDEX IF NOT EXISTS baseline_versions_embedding
    ON baseline_versions USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS baseline_deltas (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    topic_path        ltree NOT NULL,
    from_version      INTEGER,
    to_version        INTEGER NOT NULL,
    article_metadata  JSONB DEFAULT '{{}}',
    delta_summary     TEXT NOT NULL,
    claims_added      JSONB DEFAULT '[]',
    claims_superseded JSONB DEFAULT '[]',
    created_at        TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS baseline_deltas_topic_gist
    ON baseline_deltas USING GIST (topic_path);
"""


async def get_pgvector_pool() -> asyncpg.Pool:
    global _pool
    if _pool is not None:
        return _pool
    async with _get_pool_lock():
        if _pool is not None:   # re-check inside lock
            return _pool
        dsn = os.getenv("BASELINE_PG_DSN")
        if not dsn:
            raise EnvironmentError("BASELINE_PG_DSN is required")
        dims = _get_dims()
        pool = await asyncpg.create_pool(dsn)
        async with pool.acquire() as conn:
            await conn.execute(_build_ddl(dims))
        _pool = pool
        logger.info("asyncpg pool created and DDL applied (dims=%d)", dims)
        return _pool


def get_embedder() -> Callable:
    global _embedder
    if _embedder is not None:
        return _embedder
    with _embedder_lock:
        if _embedder is not None:   # re-check inside lock
            return _embedder
        model = os.getenv("BASELINE_EMBEDDING_MODEL")
        if "jina" in model:
            api_key = os.getenv("JINA_API_KEY")
        else:
            api_key = os.getenv("OPENAI_API_KEY")
        if not model:
            raise EnvironmentError("BASELINE_EMBEDDING_MODEL is required")
        if not api_key:
            raise EnvironmentError("OPENAI_API_KEY or JINA_API_KEY is required")
        base_url = os.getenv("OPENAI_BASE_URL")

        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        if "jina" in model:
            _client = JinaEmbeddings(model_name=model, jina_api_key=api_key)
        else:
            _client = AsyncOpenAI(**kwargs)

        async def embed(text: str) -> list[float]:
            if "jina" in model:
                response = _client.embed_query(text=text)
                return response
            else:
                response = await _client.embeddings.create(model=model, input=text)
                return response.data[0].embedding

        _embedder = embed
        return _embedder
