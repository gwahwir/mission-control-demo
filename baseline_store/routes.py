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
