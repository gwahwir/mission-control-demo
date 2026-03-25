"""WriteGraph for the memory agent: extract_entities → resolve_conflicts → store_memories."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Optional, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agents.memory_agent.stores import embed_text, get_neo4j_graph, get_pgvector_pool

logger = logging.getLogger(__name__)

MAX_RETRIES = 3

_openai_client = None


def _get_openai_client():
    global _openai_client
    if _openai_client is None:
        from openai import AsyncOpenAI
        kwargs: dict[str, Any] = {}
        api_key = os.getenv("OPENAI_API_KEY")
        base_url = os.getenv("OPENAI_BASE_URL")
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        _openai_client = AsyncOpenAI(**kwargs)
    return _openai_client


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class MemoryWriteState(TypedDict):
    input: str
    namespace: str
    extracted: Optional[dict]
    retry_count: int
    last_raw: str
    last_error: str
    stored: bool
    entities_added: int
    relationships_added: int
    memories_updated: int
    memories_deleted: int


# ---------------------------------------------------------------------------
# Extraction prompt
# ---------------------------------------------------------------------------

_EXTRACTION_SYSTEM_PROMPT = """\
You are a memory extraction engine. Given a blob of text, extract all key information
and return ONLY a valid JSON object with this exact schema:

{
  "entities": [
    {"name": "Full Name", "type": "person|organization|location|product|concept", "attributes": {}}
  ],
  "relationships": [
    {"subject": "Entity Name", "predicate": "verb e.g. acquired|leads|involves", "object": "Entity Name"}
  ],
  "summary": "2-3 sentence summary of the text"
}

Rules:
- Return ONLY valid JSON — no markdown fences, no commentary.
- Normalize entity names (e.g. "President Biden" and "Joe Biden" → one entry).
- Omit empty arrays (use [] not null).
- Extract implicit relationships where clearly implied.
"""


# ---------------------------------------------------------------------------
# Node 1: extract_entities
# ---------------------------------------------------------------------------

async def extract_entities(
    state: MemoryWriteState, config: RunnableConfig
) -> dict[str, Any]:
    """LLM extraction with self-correcting retry via conditional edge."""
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    executor.check_cancelled(task_id)

    retry_count = state.get("retry_count", 0)

    if retry_count >= MAX_RETRIES:
        logger.warning("[%s] extract_entities: retries exhausted, returning empty extraction", task_id)
        return {
            "extracted": {"entities": [], "relationships": [], "summary": ""},
            "retry_count": retry_count,
        }

    client = _get_openai_client()
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    messages: list[dict] = [{"role": "system", "content": _EXTRACTION_SYSTEM_PROMPT}]

    if retry_count > 0 and state.get("last_raw"):
        corrective = (
            f"Your previous response failed to parse as valid JSON.\n"
            f"Parse error: {state['last_error']}\n"
            f"Your response was:\n{state['last_raw']}\n\n"
            f"Please correct and return ONLY valid JSON matching the schema above.\n\n"
            f"Text to extract from:\n"
        )
        messages.append({"role": "user", "content": corrective + state["input"]})
    else:
        messages.append({"role": "user", "content": f"Text:\n{state['input']}"})

    raw = ""
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.1,
            max_completion_tokens=4096,
            timeout=60,
        )
        raw = response.choices[0].message.content or ""
        parsed = json.loads(raw)
        logger.info(
            "[%s] extract_entities: extracted %d entities, %d relationships",
            task_id,
            len(parsed.get("entities", [])),
            len(parsed.get("relationships", [])),
        )
        return {"extracted": parsed, "retry_count": retry_count, "last_error": ""}

    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("[%s] extract_entities: parse error attempt %d — %s", task_id, retry_count + 1, e)
        return {
            "extracted": None,
            "retry_count": retry_count + 1,
            "last_raw": raw,
            "last_error": str(e),
        }


# ---------------------------------------------------------------------------
# Conflict resolution prompt
# ---------------------------------------------------------------------------

_CONFLICT_SYSTEM_PROMPT = """\
You are a memory conflict resolver. You are given newly extracted information and a list of
existing memory records. Decide what to do with each existing record.

For each existing record, return one of:
  - {"action": "KEEP",   "id": "<uuid>"}
  - {"action": "UPDATE", "id": "<uuid>", "new_content": "<updated text>"}
  - {"action": "DELETE", "id": "<uuid>"}

Rules:
- KEEP: the existing record is still accurate and not superseded.
- UPDATE: the existing record is outdated or incomplete — replace its content with new_content.
- DELETE: the existing record is factually contradicted by the new information.
- Return ONLY a valid JSON array of resolution objects — no markdown, no commentary.
- Only include records that need action. Omit IDs not mentioned.
"""

CONFLICT_SEARCH_LIMIT = 10


# ---------------------------------------------------------------------------
# Node 2: resolve_conflicts
# ---------------------------------------------------------------------------

async def resolve_conflicts(
    state: MemoryWriteState, config: RunnableConfig
) -> dict[str, Any]:
    """Search existing memories for conflicts and apply KEEP/UPDATE/DELETE resolutions."""
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    executor.check_cancelled(task_id)

    extracted = state.get("extracted") or {"entities": [], "relationships": [], "summary": ""}
    namespace = state.get("namespace", "")

    # Build a query string from extracted entities + summary for similarity search
    entity_names = [e.get("name", "") for e in extracted.get("entities", []) if e.get("name")]
    query_text = extracted.get("summary", "") or " ".join(entity_names)

    if not query_text:
        # Nothing to compare against — skip
        return {"extracted": extracted, "memories_updated": 0, "memories_deleted": 0}

    pool = await get_pgvector_pool()
    query_vec = await embed_text(query_text)
    query_vec_str = "[" + ",".join(str(x) for x in query_vec) + "]"

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id::text, content, metadata, "
            "1 - (embedding <=> $1::vector) AS score "
            "FROM memories WHERE namespace = $2 "
            "ORDER BY embedding <=> $1::vector LIMIT $3",
            query_vec_str, namespace, CONFLICT_SEARCH_LIMIT,
        )

    if not rows:
        # No existing memories — no conflicts possible
        return {"extracted": extracted, "memories_updated": 0, "memories_deleted": 0}

    # Build context for the LLM
    existing_summary = "\n".join(
        f'- id={r["id"]} score={r["score"]:.2f}: {r["content"]}'
        for r in rows
    )
    new_info = "\n".join(
        f"- {e.get('type', 'entity')}: {e.get('name', '')}"
        for e in extracted.get("entities", [])
    )
    if extracted.get("summary"):
        new_info += f"\nSummary: {extracted['summary']}"

    client = _get_openai_client()
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    user_msg = (
        f"Newly extracted information:\n{new_info}\n\n"
        f"Existing memory records:\n{existing_summary}\n\n"
        "Return a JSON array of resolution objects for any existing records that need action."
    )

    memories_updated = 0
    memories_deleted = 0

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _CONFLICT_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.0,
            max_completion_tokens=2048,
            timeout=60,
        )
        raw = response.choices[0].message.content or ""
        resolutions = json.loads(raw)

        for resolution in resolutions:
            action = resolution.get("action", "").upper()
            row_id = resolution.get("id", "")
            if not row_id:
                continue

            if action == "UPDATE":
                new_content = resolution.get("new_content", "")
                if not new_content:
                    continue
                try:
                    new_vec = await embed_text(new_content)
                    new_vec_str = "[" + ",".join(str(x) for x in new_vec) + "]"
                    async with pool.acquire() as conn:
                        await conn.execute(
                            "UPDATE memories SET content = $1, embedding = $2::vector, "
                            "updated_at = now() WHERE id = $3::uuid",
                            new_content, new_vec_str, row_id,
                        )
                    memories_updated += 1
                except Exception as e:
                    logger.warning("[%s] resolve_conflicts: UPDATE failed for %s — %s", task_id, row_id, e)

            elif action == "DELETE":
                try:
                    async with pool.acquire() as conn:
                        await conn.execute(
                            "DELETE FROM memories WHERE id = $1::uuid", row_id
                        )
                    # Best-effort Neo4j node delete (match by id stored in metadata)
                    neo4j = get_neo4j_graph()
                    # Find content to match entity name — skip if Neo4j unavailable
                    matching_rows = [r for r in rows if r["id"] == row_id]
                    if matching_rows:
                        metadata_raw = matching_rows[0]["metadata"]
                        try:
                            meta = json.loads(metadata_raw) if isinstance(metadata_raw, str) else metadata_raw
                            for entity_name in meta.get("entities", []):
                                await asyncio.to_thread(
                                    neo4j.query,
                                    "MATCH (e:Entity {name: $name, namespace: $ns}) DETACH DELETE e",
                                    {"name": entity_name, "ns": namespace},
                                )
                        except Exception as e:
                            logger.warning("[%s] resolve_conflicts: Neo4j delete failed — %s", task_id, e)
                    memories_deleted += 1
                except Exception as e:
                    logger.warning("[%s] resolve_conflicts: DELETE failed for %s — %s", task_id, row_id, e)

    except (json.JSONDecodeError, ValueError) as e:
        logger.warning(
            "[%s] resolve_conflicts: LLM returned invalid JSON, skipping conflict resolution — %s",
            task_id, e,
        )

    return {
        "extracted": extracted,
        "memories_updated": memories_updated,
        "memories_deleted": memories_deleted,
    }


# ---------------------------------------------------------------------------
# Routing helper
# ---------------------------------------------------------------------------

def _route_after_extract(state: MemoryWriteState) -> str:
    if state.get("extracted") is not None:
        return "resolve_conflicts"
    if state.get("retry_count", 0) >= MAX_RETRIES:
        return "resolve_conflicts"
    return "extract_entities"


# ---------------------------------------------------------------------------
# Node 3: store_memories
# ---------------------------------------------------------------------------

async def store_memories(
    state: MemoryWriteState, config: RunnableConfig
) -> dict[str, Any]:
    """Write extracted entities and relationships to pgvector and Neo4j."""
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    executor.check_cancelled(task_id)

    extracted = state.get("extracted") or {"entities": [], "relationships": [], "summary": ""}
    namespace = state.get("namespace", "")

    pool = await get_pgvector_pool()
    neo4j = get_neo4j_graph()

    entities_added = 0
    relationships_added = 0
    any_stored = False

    # Store each entity in pgvector
    for entity in extracted.get("entities", []):
        executor.check_cancelled(task_id)
        name = entity.get("name", "")
        if not name:
            continue
        attrs = entity.get("attributes", {})
        content = f"{entity.get('type', 'entity').capitalize()}: {name}"
        if attrs:
            content += ", " + ", ".join(f"{k}: {v}" for k, v in attrs.items())
        metadata = {
            "entities": [name],
            "namespace": namespace,
            "source": "write",
        }
        try:
            embedding = await embed_text(content)
            embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"
            async with pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO memories (namespace, content, embedding, metadata) "
                    "VALUES ($1, $2, $3::vector, $4::jsonb)",
                    namespace, content, embedding_str, json.dumps(metadata),
                )
            entities_added += 1
            any_stored = True
        except Exception as e:
            logger.warning("[%s] store_memories: failed to store entity '%s' — %s", task_id, name, e)

    # Store each entity as a Neo4j node
    for entity in extracted.get("entities", []):
        name = entity.get("name", "")
        if not name:
            continue
        try:
            await asyncio.to_thread(
                neo4j.query,
                "MERGE (e:Entity {name: $name, namespace: $ns}) "
                "ON CREATE SET e.type = $type",
                {"name": name, "ns": namespace, "type": entity.get("type", "unknown")},
            )
        except Exception as e:
            logger.warning("[%s] store_memories: Neo4j entity merge failed '%s' — %s", task_id, name, e)

    # Store each relationship in Neo4j
    for rel in extracted.get("relationships", []):
        executor.check_cancelled(task_id)
        subj = rel.get("subject", "")
        pred = rel.get("predicate", "")
        obj = rel.get("object", "")
        if not all([subj, pred, obj]):
            continue
        try:
            await asyncio.to_thread(
                neo4j.query,
                "MATCH (a:Entity {name: $subj, namespace: $ns}) "
                "MATCH (b:Entity {name: $obj, namespace: $ns}) "
                "MERGE (a)-[:RELATES {predicate: $pred, namespace: $ns}]->(b)",
                {"subj": subj, "obj": obj, "pred": pred, "ns": namespace},
            )
            relationships_added += 1
            any_stored = True
        except Exception as e:
            logger.warning("[%s] store_memories: Neo4j relationship failed '%s %s %s' — %s", task_id, subj, pred, obj, e)

    # Store summary in pgvector
    summary = extracted.get("summary", "")
    if summary:
        entity_names = [e.get("name", "") for e in extracted.get("entities", []) if e.get("name")]
        metadata = {"entities": entity_names, "namespace": namespace, "source": "write"}
        try:
            embedding = await embed_text(summary)
            embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"
            async with pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO memories (namespace, content, embedding, metadata) "
                    "VALUES ($1, $2, $3::vector, $4::jsonb)",
                    namespace, summary, embedding_str, json.dumps(metadata),
                )
            any_stored = True
        except Exception as e:
            logger.warning("[%s] store_memories: failed to store summary — %s", task_id, e)

    logger.info(
        "[%s] store_memories: complete — %d entities, %d relationships, stored=%s",
        task_id, entities_added, relationships_added, any_stored,
    )
    return {
        "stored": any_stored,
        "entities_added": entities_added,
        "relationships_added": relationships_added,
        # memories_updated/deleted come from resolve_conflicts node — pass through unchanged
    }


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_memory_write_graph() -> CompiledStateGraph:
    graph = StateGraph(MemoryWriteState)
    graph.add_node("extract_entities", extract_entities)
    graph.add_node("resolve_conflicts", resolve_conflicts)
    graph.add_node("store_memories", store_memories)
    graph.set_entry_point("extract_entities")
    graph.add_conditional_edges(
        "extract_entities",
        _route_after_extract,
        {
            "extract_entities": "extract_entities",
            "resolve_conflicts": "resolve_conflicts",
        },
    )
    graph.add_edge("resolve_conflicts", "store_memories")
    graph.add_edge("store_memories", END)
    return graph.compile()
