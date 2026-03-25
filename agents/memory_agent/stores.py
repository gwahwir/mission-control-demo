"""Lazy singletons for pgvector (asyncpg), Neo4j (langchain_neo4j), and the OpenAI embedder."""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

_pool = None
_neo4j = None
_embedder = None  # tuple: (AsyncOpenAI, model_name)


async def get_pgvector_pool():
    """Return an asyncpg connection pool, creating the memories table on first call."""
    global _pool
    if _pool is None:
        dsn = os.getenv("MEMORY_PG_DSN")
        if not dsn:
            raise EnvironmentError("MEMORY_PG_DSN is required for the memory agent")
        dims = os.getenv("MEMORY_EMBEDDING_DIMS")
        if not dims:
            raise EnvironmentError("MEMORY_EMBEDDING_DIMS is required for the memory agent")
        dims = int(dims)

        import asyncpg
        pool = await asyncpg.create_pool(dsn)
        async with pool.acquire() as conn:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            await conn.execute(f"""
                CREATE TABLE IF NOT EXISTS memories (
                    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    namespace   TEXT NOT NULL,
                    content     TEXT NOT NULL,
                    embedding   vector({dims}),
                    metadata    JSONB DEFAULT '{{}}',
                    created_at  TIMESTAMPTZ DEFAULT now()
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS memories_namespace_idx ON memories (namespace)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS memories_embedding_idx ON memories "
                "USING hnsw (embedding vector_cosine_ops)"
            )
        _pool = pool
    return _pool


def get_neo4j_graph():
    """Return a langchain_neo4j Neo4jGraph instance."""
    global _neo4j
    if _neo4j is None:
        url = os.getenv("MEMORY_NEO4J_URL")
        user = os.getenv("MEMORY_NEO4J_USER")
        password = os.getenv("MEMORY_NEO4J_PASSWORD")
        missing = [
            k for k, v in {
                "MEMORY_NEO4J_URL": url,
                "MEMORY_NEO4J_USER": user,
                "MEMORY_NEO4J_PASSWORD": password,
            }.items()
            if not v
        ]
        if missing:
            raise EnvironmentError(
                f"Required env vars missing for memory agent: {', '.join(missing)}"
            )
        from langchain_neo4j import Neo4jGraph
        _neo4j = Neo4jGraph(url=url, username=user, password=password)
    return _neo4j


def get_embedder():
    """Return (AsyncOpenAI client, model_name) for generating embeddings."""
    global _embedder
    if _embedder is None:
        model = os.getenv("MEMORY_EMBEDDING_MODEL")
        api_key = os.getenv("OPENAI_API_KEY")
        if not model:
            raise EnvironmentError("MEMORY_EMBEDDING_MODEL is required for the memory agent")
        if not api_key:
            raise EnvironmentError("OPENAI_API_KEY is required for the memory agent")
        if "jina" in model:
            from langchain_community.embeddings import JinaEmbeddings
            _embedder = (JinaEmbeddings(model=model, jina_api_key=os.getenv("JINA_API_KEY")), model)
        else:
            from openai import AsyncOpenAI
            kwargs: dict[str, Any] = {"api_key": api_key}
            base_url = os.getenv("OPENAI_BASE_URL")
            if base_url:
                kwargs["base_url"] = base_url
            _embedder = (AsyncOpenAI(**kwargs), model)
    return _embedder


async def embed_text(text: str) -> list[float]:
    """Embed a single string and return a list of floats."""
    client, model = get_embedder()
    if "jina" in model:
        response = client.embed_query(text)
        return response
    else:
        response = await client.embeddings.create(input=text, model=model)
        return response.data[0].embedding
