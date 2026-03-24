"""Knowledge Graph agent built with LangGraph.

Ingests raw text, extracts entities/issues/relationships via LLM, stores them
in mem0 (Neo4j + pgvector), and returns a structured diff + narrative.

Nodes:
1. extract_entities_and_issues  – LLM extraction with self-correcting retry
2. store_in_mem0                – write to Neo4j + pgvector via mem0, compute diff
3. generate_narrative           – small LLM call producing the dual-format output

The self-correcting retry is implemented as a conditional edge that loops Node 1
back to itself (up to 3 attempts), injecting the previous parse error + raw
output into the prompt on each retry. RetryPolicy is NOT used here because it
cannot inject error context into the retry prompt.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Optional, TypedDict

from langchain.chat_models import init_chat_model
from langchain.embeddings import init_embeddings
from langchain_community.embeddings import JinaEmbeddings
import openai
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph

logger = logging.getLogger(__name__)

KG_USER_ID = "knowledge_graph"
MAX_RETRIES = 3

# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------

_openai_client = None
_mem0_client = None


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


def _get_mem0_client():
    global _mem0_client
    if _mem0_client is None:
        required = {
            "MEM0_NEO4J_URL": os.getenv("MEM0_NEO4J_URL"),
            "MEM0_NEO4J_USER": os.getenv("MEM0_NEO4J_USER"),
            "MEM0_NEO4J_PASSWORD": os.getenv("MEM0_NEO4J_PASSWORD"),
            "MEM0_PG_DSN": os.getenv("MEM0_PG_DSN"),
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            raise EnvironmentError(
                f"Knowledge Graph agent requires these env vars: {', '.join(missing)}"
            )

        from mem0 import Memory
        import urllib.parse

        dsn = required["MEM0_PG_DSN"]
        parsed = urllib.parse.urlparse(dsn)

        langchain_model = init_chat_model(model=os.getenv("OPENAI_SMALL_MODEL"),
                                          api_key=os.getenv("OPENAI_API_KEY"),
                                          model_provider="openai",
                                          base_url=os.getenv("OPENAI_BASE_URL"))
        
        # langchain_embedding_model = init_embeddings(model=os.getenv("OPENAI_EMBEDDING_MODEL"),
        #                                             provider="ollama",
        #                                             #api_key=os.getenv("OPENAI_API_KEY"),
        #                                             base_url="http://localhost:11434",
        #                                             )
        # langchain_embedding_model = init_embeddings(model=os.getenv("OPENAI_EMBEDDING_MODEL"),
        #                                             provider="openai",
        #                                             api_key=os.getenv("OPENAI_API_KEY"),
        #                                             base_url=os.getenv("OPENAI_BASE_URL"))
        langchain_embedding_model = JinaEmbeddings(model_name="jina-embeddings-v5-text-small",
                                                    jina_api_key=os.getenv("JINA_API_KEY"))
        config = {
            "graph_store": {
                "provider": "neo4j",
                "config": {
                    "url": required["MEM0_NEO4J_URL"],
                    "username": required["MEM0_NEO4J_USER"],
                    "password": required["MEM0_NEO4J_PASSWORD"],
                },
            },
            "vector_store": {
                "provider": "pgvector",
                "config": {
                    "dbname": parsed.path.lstrip("/"),
                    "user": parsed.username,
                    "password": parsed.password,
                    "host": parsed.hostname,
                    "port": parsed.port or 5432,
                    "embedding_model_dims": 1024
                },
            },
            "llm": {
                "provider": "langchain",
                "config": {
                    "model": langchain_model
                },
            },
            "embedder": {
                "provider": "langchain",
                "config": {
                    "model": langchain_embedding_model,
                },
            },
        }
        _mem0_client = Memory.from_config(config)
    return _mem0_client


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class KnowledgeGraphState(TypedDict):
    input: str
    extracted: Optional[dict]   # None until extraction succeeds or retries exhausted
    retry_count: int
    last_raw: str
    last_error: str
    diff: dict
    stats: dict
    narrative: str
    output: str


# ---------------------------------------------------------------------------
# Node 1: extract_entities_and_issues
# ---------------------------------------------------------------------------

_EXTRACTION_SYSTEM_PROMPT = """\
You are a knowledge graph extraction engine. Given a blob of text (typically a news article or
informational content), extract all key information and return ONLY a valid JSON object with this exact schema:

{
  "entities": [
    {"name": "Full Name", "type": "person|organization|location|product", "attributes": {}}
  ],
  "issues": [
    {
      "name": "Issue Name",
      "type": "issue",
      "attributes": {
        "domain": "comma-separated domains e.g. technology,policy",
        "severity": "high|medium|low",
        "status": "emerging|ongoing|resolved",
        "summary": "1-2 sentence description of the issue"
      }
    }
  ],
  "relationships": [
    {"subject": "Entity Name", "predicate": "verb e.g. leads|involves|acquired", "object": "Entity or Issue Name"}
  ],
  "source_summary": "2-3 sentence summary of the article"
}

Rules:
- Return ONLY valid JSON — no markdown fences, no commentary outside the JSON.
- Issues are world-interest topics: geopolitical tensions, economic crises, policy debates, tech controversies, etc.
- Normalize entity names (e.g. "President Biden" and "Joe Biden" → one entry with full name).
- Omit empty arrays (use [] not null).
- Extract implicit relationships where clearly implied by context.
"""


async def extract_entities_and_issues(
    state: KnowledgeGraphState, config: RunnableConfig
) -> dict[str, Any]:
    """LLM extraction with self-correcting retry via conditional edge."""
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    executor.check_cancelled(task_id)

    retry_count = state.get("retry_count", 0)
    input_preview = state.get("input", "")[:120].replace("\n", " ")

    # If retries are exhausted, produce empty extraction and log warning
    if retry_count >= MAX_RETRIES:
        logger.warning(
            "[%s] extract_entities_and_issues: retries exhausted after %d attempts, "
            "falling back to empty extraction",
            task_id, MAX_RETRIES,
        )
        return {
            "extracted": {"entities": [], "issues": [], "relationships": [], "source_summary": ""},
            "retry_count": retry_count,
        }

    client = _get_openai_client()
    model = os.getenv("OPENAI_SMALL_MODEL", "gpt-4o-mini")

    attempt_label = f"attempt {retry_count + 1}/{MAX_RETRIES}"
    logger.info(
        "[%s] extract_entities_and_issues: starting LLM extraction (%s) model=%s input_preview='%s...'",
        task_id, attempt_label, model, input_preview,
    )

    # Build messages — inject error context on retries
    messages: list[dict] = [{"role": "system", "content": _EXTRACTION_SYSTEM_PROMPT}]

    if retry_count > 0 and state.get("last_raw"):
        logger.info(
            "[%s] extract_entities_and_issues: retrying after parse error — %s",
            task_id, state["last_error"],
        )
        corrective_prefix = (
            f"Your previous response failed to parse as valid JSON.\n"
            f"Parse error: {state['last_error']}\n"
            f"Your response was:\n{state['last_raw']}\n\n"
            f"Please correct your response and return ONLY valid JSON matching the schema above.\n\n"
            f"Text to extract from:\n"
        )
        messages.append({"role": "user", "content": corrective_prefix + state["input"]})
    else:
        messages.append({"role": "user", "content": f"Text:\n{state['input']}"})

    raw = ""
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.1,
            max_completion_tokens=8192,
            timeout=60,
        )
        raw = response.choices[0].message.content or ""
        parsed = json.loads(raw)
        n_entities = len(parsed.get("entities", []))
        n_issues = len(parsed.get("issues", []))
        n_rels = len(parsed.get("relationships", []))
        logger.info(
            "[%s] extract_entities_and_issues: extraction complete — "
            "%d entities, %d issues, %d relationships",
            task_id, n_entities, n_issues, n_rels,
        )
        return {"extracted": parsed, "retry_count": retry_count, "last_error": ""}

    except (json.JSONDecodeError, ValueError) as e:
        logger.warning(
            "[%s] extract_entities_and_issues: parse error on attempt %d/%d — %s",
            task_id, retry_count + 1, MAX_RETRIES, e,
        )
        return {
            "extracted": None,
            "retry_count": retry_count + 1,
            "last_raw": raw,
            "last_error": str(e),
        }
    except openai.RateLimitError as e:
        logger.warning("[%s] extract_entities_and_issues: rate limited — %s", task_id, e)
        return {"extracted": {"entities": [], "issues": [], "relationships": [], "source_summary": ""}, "retry_count": retry_count}
    except openai.APIError as e:
        logger.error("[%s] extract_entities_and_issues: API error — %s", task_id, e, exc_info=True)
        return {"extracted": {"entities": [], "issues": [], "relationships": [], "source_summary": ""}, "retry_count": retry_count}


def _route_after_extract(state: KnowledgeGraphState) -> str:
    """Route: retry if extraction failed and retries remain, else proceed."""
    if state.get("extracted") is not None:
        return "store_in_mem0"
    # Defensive guard: when retry_count >= MAX_RETRIES, the node itself
    # already sets extracted to an empty dict, so this branch is unreachable
    # in practice but protects against future logic changes.
    if state.get("retry_count", 0) >= MAX_RETRIES:
        return "store_in_mem0"
    return "extract_entities_and_issues"


# ---------------------------------------------------------------------------
# Node 2: store_in_mem0
# ---------------------------------------------------------------------------

async def store_in_mem0(
    state: KnowledgeGraphState, config: RunnableConfig
) -> dict[str, Any]:
    """Write entities, issues, and relationships to mem0; compute diff."""
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    executor.check_cancelled(task_id)

    extracted = state.get("extracted") or {"entities": [], "issues": [], "relationships": [], "source_summary": ""}
    memory = _get_mem0_client()

    n_entities = len(extracted.get("entities", []))
    n_issues = len(extracted.get("issues", []))
    n_rels = len(extracted.get("relationships", []))
    logger.info(
        "[%s] store_in_mem0: writing to mem0 — %d entities, %d issues, %d relationships",
        task_id, n_entities, n_issues, n_rels,
    )

    entities_added: list[str] = []
    entities_updated: list[str] = []
    issues_added: list[str] = []
    issues_updated: list[str] = []
    relationships_added: list[str] = []

    # Write entities
    for i, entity in enumerate(extracted.get("entities", []), 1):
        executor.check_cancelled(task_id)
        name = entity.get("name", "")
        if not name:
            continue
        try:
            existing = await asyncio.to_thread(memory.search, name, user_id=KG_USER_ID, limit=1)
            results = existing.get("results", []) if isinstance(existing, dict) else existing
            already_exists = len(results) > 0

            attrs = entity.get("attributes", {})
            mem_text = f"{entity.get('type', 'entity').capitalize()}: {name}"
            if attrs:
                attr_str = ", ".join(f"{k}: {v}" for k, v in attrs.items())
                mem_text += f", {attr_str}"
            await asyncio.to_thread(memory.add, mem_text, user_id=KG_USER_ID)

            if already_exists:
                entities_updated.append(name)
                logger.info("[%s] store_in_mem0: entity [%d/%d] updated — '%s'", task_id, i, n_entities, name)
            else:
                entities_added.append(name)
                logger.info("[%s] store_in_mem0: entity [%d/%d] added — '%s'", task_id, i, n_entities, name)
        except Exception as e:
            logger.warning("[%s] store_in_mem0: failed to write entity '%s' — %s", task_id, name, e)

    # Write issues
    for i, issue in enumerate(extracted.get("issues", []), 1):
        executor.check_cancelled(task_id)
        name = issue.get("name", "")
        if not name:
            continue
        try:
            existing = await asyncio.to_thread(memory.search, name, user_id=KG_USER_ID, limit=1)
            results = existing.get("results", []) if isinstance(existing, dict) else existing
            already_exists = len(results) > 0

            attrs = issue.get("attributes", {})
            mem_text = f"Issue: {name}"
            if attrs.get("summary"):
                mem_text += f". {attrs['summary']}"
            if attrs.get("domain"):
                mem_text += f" Domain: {attrs['domain']}."
            if attrs.get("severity"):
                mem_text += f" Severity: {attrs['severity']}."
            if attrs.get("status"):
                mem_text += f" Status: {attrs['status']}."
            await asyncio.to_thread(memory.add, mem_text, user_id=KG_USER_ID)

            if already_exists:
                issues_updated.append(name)
                logger.info("[%s] store_in_mem0: issue [%d/%d] updated — '%s'", task_id, i, n_issues, name)
            else:
                issues_added.append(name)
                logger.info("[%s] store_in_mem0: issue [%d/%d] added — '%s'", task_id, i, n_issues, name)
        except Exception as e:
            logger.warning("[%s] store_in_mem0: failed to write issue '%s' — %s", task_id, name, e)

    # Write relationships
    for i, rel in enumerate(extracted.get("relationships", []), 1):
        executor.check_cancelled(task_id)
        subj = rel.get("subject", "")
        pred = rel.get("predicate", "")
        obj = rel.get("object", "")
        if not all([subj, pred, obj]):
            continue
        try:
            rel_text = f"{subj} {pred} {obj}"
            await asyncio.to_thread(memory.add, rel_text, user_id=KG_USER_ID)
            relationships_added.append(rel_text)
            logger.info("[%s] store_in_mem0: relationship [%d/%d] added — '%s'", task_id, i, n_rels, rel_text)
        except Exception as e:
            logger.warning("[%s] store_in_mem0: failed to write relationship '%s %s %s' — %s", task_id, subj, pred, obj, e)

    diff = {
        "entities": {"added": entities_added, "updated": entities_updated},
        "issues": {"added": issues_added, "updated": issues_updated},
        "relationships": {"added": relationships_added},
    }
    stats = {
        "entities_added": len(entities_added),
        "entities_updated": len(entities_updated),
        "issues_added": len(issues_added),
        "issues_updated": len(issues_updated),
        "relationships_added": len(relationships_added),
    }
    logger.info(
        "[%s] store_in_mem0: complete — +%d/~%d entities, +%d/~%d issues, +%d relationships",
        task_id,
        stats["entities_added"], stats["entities_updated"],
        stats["issues_added"], stats["issues_updated"],
        stats["relationships_added"],
    )
    return {"diff": diff, "stats": stats}


# ---------------------------------------------------------------------------
# Node 3: generate_narrative
# ---------------------------------------------------------------------------

async def generate_narrative(
    state: KnowledgeGraphState, config: RunnableConfig
) -> dict[str, Any]:
    """Generate human-readable narrative and serialise dual-format output."""
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    executor.check_cancelled(task_id)

    diff = state.get("diff", {})
    stats = state.get("stats", {})
    source_summary = (state.get("extracted") or {}).get("source_summary", "")

    client = _get_openai_client()
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    logger.info("[%s] generate_narrative: generating summary narrative model=%s", task_id, model)

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a knowledge graph assistant. Given a summary of what was just ingested "
                        "into a knowledge graph, write a single concise paragraph (2-4 sentences) describing "
                        "what was added or updated and how it connects to existing knowledge. "
                        "Be specific about entity names and issues."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Source article summary: {source_summary}\n\n"
                        f"Changes made to the knowledge graph:\n{json.dumps(stats, indent=2)}\n\n"
                        f"Detail of changes:\n{json.dumps(diff, indent=2)}"
                    ),
                },
            ],
            temperature=0.3,
            max_completion_tokens=256,
        )
        narrative = response.choices[0].message.content or ""
        logger.info("[%s] generate_narrative: narrative generated (%d chars)", task_id, len(narrative))
    except Exception as e:
        logger.warning("[%s] generate_narrative: LLM call failed, using fallback — %s", task_id, e)
        narrative = f"Ingested {stats.get('entities_added', 0)} new entities and {stats.get('issues_added', 0)} new issues."

    logger.info("[%s] generate_narrative: task complete", task_id)
    output = json.dumps({
        "diff": diff,
        "narrative": narrative,
        "stats": stats,
    })
    return {"narrative": narrative, "output": output}


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_knowledge_graph_graph() -> StateGraph:
    graph = StateGraph(KnowledgeGraphState)

    graph.add_node("extract_entities_and_issues", extract_entities_and_issues)
    graph.add_node("store_in_mem0", store_in_mem0)
    graph.add_node("generate_narrative", generate_narrative)

    graph.set_entry_point("extract_entities_and_issues")
    graph.add_conditional_edges(
        "extract_entities_and_issues",
        _route_after_extract,
        {
            "extract_entities_and_issues": "extract_entities_and_issues",
            "store_in_mem0": "store_in_mem0",
        },
    )
    graph.add_edge("store_in_mem0", "generate_narrative")
    graph.add_edge("generate_narrative", END)

    return graph.compile()
