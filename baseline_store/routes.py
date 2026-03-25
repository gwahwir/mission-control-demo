# baseline_store/routes.py
from __future__ import annotations
import json
import logging
from typing import Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import asyncpg

from baseline_store.stores import get_pgvector_pool, get_embedder

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Pydantic models ──────────────────────────────────────────────────────────

class TopicCreate(BaseModel):
    topic_path: str
    display_name: str


class Citation(BaseModel):
    article_id: str
    title: str
    url: str
    source: str
    published_at: str
    excerpt: str = ""


class VersionCreate(BaseModel):
    narrative: str
    citations: list[Citation] = []


class DeltaCreate(BaseModel):
    from_version: int | None = None
    to_version: int
    article_metadata: dict[str, Any] = {}
    delta_summary: str
    claims_added: list[str] = []
    claims_superseded: list[str] = []


# ── Topic endpoints ──────────────────────────────────────────────────────────

@router.post("/topics", status_code=201)
async def create_topic(body: TopicCreate):
    pool = await get_pgvector_pool()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO baseline_topics (topic_path, display_name)
                VALUES ($1::ltree, $2)
                RETURNING id::text, topic_path::text, display_name, created_at::text
                """,
                body.topic_path, body.display_name,
            )
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=409, detail=f"Topic already registered: {body.topic_path}")
    return dict(row)


@router.get("/topics")
async def list_topics():
    pool = await get_pgvector_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id::text, topic_path::text, display_name, created_at::text FROM baseline_topics ORDER BY topic_path"
        )
    return {"topics": [dict(r) for r in rows]}


# ── Version endpoints ────────────────────────────────────────────────────────

@router.post("/baselines/{topic_path}/versions", status_code=201)
async def create_version(topic_path: str, body: VersionCreate):
    pool = await get_pgvector_pool()
    embed = get_embedder()

    async with pool.acquire() as conn:
        # 1. Verify topic is registered
        topic = await conn.fetchrow(
            "SELECT topic_path FROM baseline_topics WHERE topic_path = $1::ltree",
            topic_path,
        )
        if topic is None:
            raise HTTPException(
                status_code=404,
                detail=f"Topic not registered: {topic_path} — call POST /topics first",
            )

        # 2. Compute next version number
        max_row = await conn.fetchrow(
            "SELECT MAX(version_number) AS max FROM baseline_versions WHERE topic_path = $1::ltree",
            topic_path,
        )
        next_version = (max_row["max"] or 0) + 1

    # 3. Embed narrative OUTSIDE the connection block — avoids holding a pool
    #    connection open during a potentially slow OpenAI network call.
    vector = await embed(body.narrative)

    # 4. Insert in a fresh connection
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO baseline_versions (topic_path, version_number, narrative, embedding, citations)
                VALUES ($1::ltree, $2, $3, $4::vector, $5::jsonb)
                RETURNING id::text, version_number, created_at::text
                """,
                topic_path, next_version, body.narrative,
                str(vector), json.dumps([c.model_dump() for c in body.citations]),
            )
        except asyncpg.UniqueViolationError:
            raise HTTPException(status_code=409, detail="Version conflict — retry with a fresh version number")

        return dict(row)


# ── Delta endpoints ──────────────────────────────────────────────────────────

@router.post("/baselines/{topic_path}/deltas", status_code=201)
async def create_delta(topic_path: str, body: DeltaCreate):
    pool = await get_pgvector_pool()
    async with pool.acquire() as conn:
        # Validate to_version exists
        row = await conn.fetchrow(
            """
            SELECT version_number FROM baseline_versions
            WHERE topic_path = $1::ltree AND version_number = $2
            """,
            topic_path, body.to_version,
        )
        if row is None:
            raise HTTPException(
                status_code=422,
                detail=f"to_version {body.to_version} does not exist for topic: {topic_path} — write the version before the delta",
            )

        result = await conn.fetchrow(
            """
            INSERT INTO baseline_deltas
                (topic_path, from_version, to_version, article_metadata, delta_summary, claims_added, claims_superseded)
            VALUES ($1::ltree, $2, $3, $4::jsonb, $5, $6::jsonb, $7::jsonb)
            RETURNING id::text, created_at::text
            """,
            topic_path, body.from_version, body.to_version,
            json.dumps(body.article_metadata), body.delta_summary,
            json.dumps(body.claims_added), json.dumps(body.claims_superseded),
        )
    return dict(result)


# ── Read endpoints ────────────────────────────────────────────────────────────

@router.get("/baselines/{topic_path}/current")
async def get_current(topic_path: str):
    pool = await get_pgvector_pool()
    async with pool.acquire() as conn:
        topic = await conn.fetchrow(
            "SELECT topic_path FROM baseline_topics WHERE topic_path = $1::ltree",
            topic_path,
        )
        if topic is None:
            raise HTTPException(status_code=404, detail=f"Topic not registered: {topic_path}")

        row = await conn.fetchrow(
            """
            SELECT topic_path::text, version_number, narrative, citations::text, created_at::text
            FROM baseline_versions
            WHERE topic_path = $1::ltree
            ORDER BY version_number DESC
            LIMIT 1
            """,
            topic_path,
        )
    if row is None:
        raise HTTPException(status_code=404, detail=f"No versions written yet for topic: {topic_path}")

    result = dict(row)
    result["citations"] = json.loads(result["citations"])
    return result


@router.get("/baselines/{topic_path}/history")
async def get_history(topic_path: str):
    pool = await get_pgvector_pool()
    async with pool.acquire() as conn:
        topic = await conn.fetchrow(
            "SELECT topic_path FROM baseline_topics WHERE topic_path = $1::ltree",
            topic_path,
        )
        if topic is None:
            raise HTTPException(status_code=404, detail=f"Topic not registered: {topic_path}")

        versions = await conn.fetch(
            """
            SELECT version_number, narrative, citations::text, created_at::text
            FROM baseline_versions
            WHERE topic_path = $1::ltree
            ORDER BY version_number DESC
            """,
            topic_path,
        )
        deltas = await conn.fetch(
            """
            SELECT from_version, to_version, delta_summary,
                   claims_added::text, claims_superseded::text,
                   article_metadata::text, created_at::text
            FROM baseline_deltas
            WHERE topic_path = $1::ltree
            ORDER BY to_version DESC
            """,
            topic_path,
        )

    def parse_version(r):
        d = dict(r)
        d["citations"] = json.loads(d["citations"])
        return d

    def parse_delta(r):
        d = dict(r)
        d["claims_added"] = json.loads(d["claims_added"])
        d["claims_superseded"] = json.loads(d["claims_superseded"])
        d["article_metadata"] = json.loads(d["article_metadata"])
        return d

    return {
        "topic_path": topic_path,
        "versions": [parse_version(r) for r in versions],
        "deltas": [parse_delta(r) for r in deltas],
    }
