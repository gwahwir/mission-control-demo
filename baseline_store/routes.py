# baseline_store/routes.py
from __future__ import annotations
import json
import logging
from typing import Any
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
import asyncpg

from baseline_store.stores import get_pgvector_pool, get_embedder

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Pydantic models ──────────────────────────────────────────────────────────

class TopicCreate(BaseModel):
    topic_path: str = Field(
        ...,
        description=(
            "Dot-separated ltree path identifying the topic. Use lowercase with underscores. "
            "Examples: 'geo', 'geo.middle_east', 'geo.middle_east.iran'. "
            "Parent topics must be registered separately before rollup queries work."
        ),
        examples=["geo.middle_east.iran"],
    )
    display_name: str = Field(
        ...,
        description="Human-readable label for the topic shown in listings.",
        examples=["Iran"],
    )


class Citation(BaseModel):
    article_id: str = Field(..., description="Stable unique identifier for the source article.")
    title: str = Field(..., description="Article headline or title.")
    url: str = Field(..., description="Canonical URL of the article.")
    source: str = Field(..., description="Publisher or wire service name (e.g. 'Reuters', 'AP').")
    published_at: str = Field(..., description="ISO 8601 publication timestamp (e.g. '2026-03-28T12:00:00Z').")
    excerpt: str = Field("", description="Short verbatim excerpt from the article that supports the narrative claim.")


class VersionCreate(BaseModel):
    narrative: str = Field(
        ...,
        description=(
            "Plain-text narrative summarising the current state of knowledge for this topic. "
            "The store embeds this text internally — do not pass a vector. "
            "Write in present-tense declarative sentences suitable for use as context in a future analysis prompt."
        ),
        examples=["As of March 2026, Iran has enriched uranium to approximately 60% purity at Fordow and Natanz. International negotiations remain stalled."],
    )
    citations: list[Citation] = Field(
        default=[],
        description="Source articles that support claims in the narrative. Optional but strongly recommended for auditability.",
    )


class DeltaCreate(BaseModel):
    from_version: int | None = Field(
        None,
        description="Version number this delta transitions from. Pass null when recording the first-ever baseline for a topic.",
    )
    to_version: int = Field(
        ...,
        description="Version number this delta transitions to. The version must already exist — write it via POST /versions before recording the delta.",
    )
    article_metadata: dict[str, Any] = Field(
        default={},
        description="Metadata for the article or event that triggered this baseline update (article_id, title, url, source, published_at).",
    )
    delta_summary: str = Field(
        ...,
        description="One or two sentences describing what changed and why the baseline was updated.",
        examples=["Iran agreed to resume indirect talks via Oman, reversing the prior assessment that negotiations were fully stalled."],
    )
    claims_added: list[str] = Field(
        default=[],
        description="New factual claims introduced in the updated baseline that were not present before.",
    )
    claims_superseded: list[str] = Field(
        default=[],
        description="Claims from the previous baseline that are no longer accurate and have been replaced.",
    )


# ── Topic endpoints ──────────────────────────────────────────────────────────

@router.post(
    "/topics",
    status_code=201,
    summary="Register a topic",
    description=(
        "Register a new topic path before writing any baselines for it. "
        "Topic paths use dot-separated ltree format (e.g. `geo.middle_east.iran`). "
        "Each segment of the hierarchy must be registered independently — registering "
        "`geo.middle_east.iran` does not auto-create `geo` or `geo.middle_east`. "
        "Returns 409 if the topic path is already registered."
    ),
    response_description="The newly registered topic record including its generated UUID and creation timestamp.",
)
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


@router.get(
    "/topics",
    summary="List all registered topics",
    description=(
        "Returns all registered topic paths sorted alphabetically by path. "
        "Use this to discover what topics exist before calling baseline endpoints, "
        "or to verify a topic was registered successfully."
    ),
    response_description="Object with a `topics` array. Each entry has id, topic_path, display_name, and created_at.",
)
async def list_topics():
    pool = await get_pgvector_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id::text, topic_path::text, display_name, created_at::text FROM baseline_topics ORDER BY topic_path"
        )
    return {"topics": [dict(r) for r in rows]}


# ── Version endpoints ────────────────────────────────────────────────────────

@router.post(
    "/baselines/{topic_path}/versions",
    status_code=201,
    summary="Write a new baseline version",
    description=(
        "Persist a new versioned narrative for the given topic. "
        "The store generates the embedding from `narrative` internally using the configured model — "
        "callers must not pass a vector. "
        "`version_number` is assigned automatically as MAX(existing) + 1, starting at 1. "
        "After writing a new version, call POST /baselines/{topic_path}/deltas to record what changed. "
        "Returns 404 if the topic is not registered — call POST /topics first. "
        "Returns 409 on a rare version-number race condition — simply retry."
    ),
    response_description="The created version record: id, version_number, and created_at. Use version_number when recording a delta.",
)
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

@router.post(
    "/baselines/{topic_path}/deltas",
    status_code=201,
    summary="Record a delta between two baseline versions",
    description=(
        "Record what changed between two consecutive baseline versions for a topic. "
        "Call this immediately after writing a new version to preserve the audit trail. "
        "`to_version` must already exist — write the version first via POST /versions. "
        "Set `from_version` to null when recording the very first baseline for a topic. "
        "`claims_added` and `claims_superseded` enable downstream agents to perform precise "
        "delta analysis without re-reading full narratives. "
        "Returns 422 if `to_version` does not exist."
    ),
    response_description="The created delta record: id and created_at.",
)
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


# ── IMPORTANT: /similar must be defined before /{topic_path} routes ──────────

@router.get(
    "/baselines/similar",
    summary="Semantic search across all baselines",
    description=(
        "Find the most semantically similar baseline versions across all topics using vector cosine similarity. "
        "The query string is embedded internally using the same model as the stored narratives. "
        "Use this to discover related topics, detect cross-domain signals, or find relevant prior assessments "
        "before starting a new analysis. Results are ranked by similarity score descending. "
        "Only the latest version per topic is searched — historical versions are excluded."
    ),
    response_description=(
        "Object with a `results` array sorted by `score` descending. "
        "Each entry includes topic_path, version_number, narrative, citations, score (0–1), and created_at."
    ),
)
async def similar_baselines(
    query: str = Query(..., description="Free-text query to embed and search against stored baseline narratives."),
    limit: int = Query(5, description="Maximum number of results to return.", ge=1, le=50),
):
    pool = await get_pgvector_pool()
    embed = get_embedder()
    vector = await embed(query)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT topic_path::text, version_number, narrative, citations::text,
                   1 - (embedding <=> $1::vector) AS score,
                   created_at::text
            FROM baseline_versions
            ORDER BY embedding <=> $1::vector
            LIMIT $2
            """,
            str(vector), limit,
        )

    def parse(r):
        d = dict(r)
        d["citations"] = json.loads(d["citations"])
        return d

    return {"results": [parse(r) for r in rows]}


# ── Read endpoints ────────────────────────────────────────────────────────────

@router.get(
    "/baselines/{topic_path}/current",
    summary="Get the current baseline for a topic",
    description=(
        "Fetch the most recent baseline version for a topic. "
        "Call this at the start of an analysis session to retrieve the established context, "
        "so the agent can focus on what has changed rather than re-deriving known facts. "
        "Returns 404 if the topic is not registered or if no versions have been written yet."
    ),
    response_description=(
        "The latest version record: topic_path, version_number, narrative, citations array, and created_at."
    ),
)
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


@router.get(
    "/baselines/{topic_path}/history",
    summary="Get full version and delta history for a topic",
    description=(
        "Retrieve all baseline versions (newest first) and the complete delta log for a topic. "
        "Use this to understand how the baseline has evolved over time, which claims were added or superseded, "
        "and which articles drove each update. "
        "Returns 404 if the topic is not registered. "
        "Returns an empty versions and deltas array if the topic is registered but has no versions yet."
    ),
    response_description=(
        "Object with topic_path, versions array (newest first), and deltas array (newest first). "
        "Each delta entry includes from_version, to_version, delta_summary, claims_added, claims_superseded, and article_metadata."
    ),
)
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


@router.get(
    "/baselines/{topic_path}/rollup",
    summary="Get current baselines for all descendant topics",
    description=(
        "Fetch the most recent baseline version for every topic that is a descendant of the given path. "
        "Descendants are topics whose path starts with `topic_path.` (strict subtree — the ancestor itself is excluded). "
        "Use this when an agent needs a full situational picture across a domain before running a multi-topic analysis. "
        "For example, calling rollup on `geo.middle_east` returns the current baseline for `geo.middle_east.iran`, "
        "`geo.middle_east.israel`, etc. in a single request. "
        "Only descendants that have at least one written version are included. "
        "Returns 404 if the ancestor topic is not registered. "
        "Returns an empty descendants array if no registered descendants have baselines yet."
    ),
    response_description=(
        "Object with ancestor (the requested path) and a descendants array. "
        "Each descendant entry has topic_path, version_number, narrative, citations, and created_at."
    ),
)
async def get_rollup(topic_path: str):
    pool = await get_pgvector_pool()
    async with pool.acquire() as conn:
        topic = await conn.fetchrow(
            "SELECT topic_path FROM baseline_topics WHERE topic_path = $1::ltree",
            topic_path,
        )
        if topic is None:
            raise HTTPException(status_code=404, detail=f"Topic not registered: {topic_path}")

        # Fetch current version per descendant (topic_path strictly below ancestor)
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (v.topic_path)
                v.topic_path::text, v.version_number, v.narrative, v.citations::text, v.created_at::text
            FROM baseline_versions v
            WHERE v.topic_path <@ $1::ltree
              AND v.topic_path != $1::ltree
            ORDER BY v.topic_path, v.version_number DESC
            """,
            topic_path,
        )

    def parse(r):
        d = dict(r)
        d["citations"] = json.loads(d["citations"])
        return d

    return {"ancestor": topic_path, "descendants": [parse(r) for r in rows]}
