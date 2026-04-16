"""LangGraph ingest pipeline for the wiki agent.

11-node linear graph:
  summarize → extract → find_related → update_pages → create_source_page
  → store_memories → write_baselines → write_files → update_index
  → append_log → finalize → END
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional, TypedDict

import httpx
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class WikiState(TypedDict):
    input_text: str
    source_url: str
    source_title: str
    source_metadata: dict
    namespace: str
    summary: str
    extracted: dict
    related_pages: list[dict]    # [{topic_path, narrative, version, score}]
    updated_pages: list[dict]    # [{topic_path, new_content, delta_summary, is_new, from_version}]
    new_page_path: str
    stored_to_memory: bool
    baseline_versions: dict      # {topic_path: new_version_number}
    files_written: list[str]
    retry_count: int
    last_error: str
    output: str


# ---------------------------------------------------------------------------
# OpenAI client singleton
# ---------------------------------------------------------------------------

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
# Shared helpers
# ---------------------------------------------------------------------------

async def _call_a2a_agent(url: str, text: str, context_id: str = "") -> str:
    """Call an A2A agent and return the first text part of the response."""
    from control_plane.a2a_client import A2AClient
    client = A2AClient(url, timeout=300)
    try:
        result = await client.send_message(text, context_id=context_id or None)
        parts = result.get("status", {}).get("message", {}).get("parts", [])
        if parts:
            return parts[0].get("text", "")
        for part in result.get("parts", []):
            if part.get("kind") == "text":
                return part["text"]
        return ""
    finally:
        await client.close()


async def _baseline_get(path: str) -> dict | None:
    """GET from baseline store; returns parsed JSON or None on 404."""
    base = os.getenv("BASELINE_URL", "http://localhost:8010")
    async with httpx.AsyncClient(timeout=30) as http:
        r = await http.get(f"{base}{path}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()


async def _baseline_post(path: str, body: dict) -> dict:
    """POST to baseline store; returns parsed JSON."""
    base = os.getenv("BASELINE_URL", "http://localhost:8010")
    async with httpx.AsyncClient(timeout=30) as http:
        r = await http.post(f"{base}{path}", json=body)
        r.raise_for_status()
        return r.json()


def _parse_json_response(raw: str) -> dict:
    """Parse a JSON response, stripping markdown code fences if present."""
    raw = raw.strip()
    # Strip ```json ... ``` or ``` ... ``` fences
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw.strip())


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

PAGE_UPDATER_SYSTEM_PROMPT = """You are a wiki editor for a geopolitics/IR intelligence wiki.
You are given an existing wiki page and new information from a recently ingested source.
Update the page to incorporate the new information: add new facts, update outdated claims, note contradictions.
Preserve the page's existing structure and style.
Return ONLY valid JSON (no markdown fences): {"updated_content": "...", "delta_summary": "..."}
- updated_content: the complete updated page in Markdown
- delta_summary: one sentence describing what changed"""

SOURCE_PAGE_WRITER_SYSTEM_PROMPT = """You are a wiki editor for a geopolitics/IR intelligence wiki.
Write a new wiki page summarizing an ingested source.
The page must be in Markdown with these sections:
  # [Title]
  **Source:** [url] | **Date:** [date]
  ## Summary
  ## Key Entities
  ## Key Claims
  ## Related Topics
Return ONLY valid JSON (no markdown fences): {"page_content": "...", "suggested_topic_path": "..."}
- page_content: complete page Markdown
- suggested_topic_path: dotted path like wiki.sources.YYYY-MM-DD-slug (slug = 3-5 words from title, hyphenated, lowercase)"""

INDEX_UPDATER_SYSTEM_PROMPT = """You are a wiki editor maintaining index.md for a geopolitics/IR intelligence wiki.
Update the index to include new pages and reflect updated pages.
Keep the index organized with sections by category: ## Sources, ## Geo, ## Actors, ## Concepts, ## Queries.
Each entry: `- [Page Title](relative/path.md) — one-line description`
Return ONLY the complete updated index.md content as plain Markdown (no JSON wrapper, no code fences)."""


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

async def summarize(state: WikiState, config: RunnableConfig) -> dict[str, Any]:
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    context_id = config["configurable"].get("context_id", "")
    executor.check_cancelled(task_id)

    summarizer_url = os.getenv("SUMMARIZER_URL", "http://localhost:8002")
    payload = json.dumps({"text": state["input_text"]})
    try:
        summary = await _call_a2a_agent(summarizer_url, payload, context_id)
    except Exception as e:
        logger.warning("[%s] summarize: summarizer failed (%s), using truncated input", task_id, e)
        summary = state["input_text"][:500]

    logger.info("[%s] summarize: got %d chars", task_id, len(summary))
    return {"summary": summary}


async def extract(state: WikiState, config: RunnableConfig) -> dict[str, Any]:
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    context_id = config["configurable"].get("context_id", "")
    executor.check_cancelled(task_id)

    extraction_url = os.getenv("EXTRACTION_URL", "http://localhost:8004")
    payload = json.dumps({"text": state["input_text"]})
    try:
        raw = await _call_a2a_agent(extraction_url, payload, context_id)
        extracted = _parse_json_response(raw)
    except Exception as e:
        logger.warning("[%s] extract: extraction failed (%s), using empty dict", task_id, e)
        extracted = {}

    logger.info("[%s] extract: entities=%s", task_id, list((extracted.get("entities") or {}).keys()))
    return {"extracted": extracted}


async def find_related(state: WikiState, config: RunnableConfig) -> dict[str, Any]:
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    context_id = config["configurable"].get("context_id", "")
    executor.check_cancelled(task_id)

    memory_url = os.getenv("MEMORY_AGENT_URL", "http://localhost:8009")
    namespace = state.get("namespace") or "wiki_geo"

    # Build search query from summary + top entities
    entities = state.get("extracted", {}).get("entities", {})
    entity_names: list[str] = []
    if isinstance(entities, dict):
        for v in entities.values():
            if isinstance(v, list):
                entity_names.extend(str(e) for e in v[:3])
    search_query = state.get("summary", "")[:300]
    if entity_names:
        search_query += " " + " ".join(entity_names[:5])

    related_pages: list[dict] = []
    try:
        search_payload = json.dumps({"query": search_query, "namespace": namespace, "limit": 5})
        raw = await _call_a2a_agent(memory_url, search_payload, context_id)
        results = json.loads(raw).get("results", []) if raw else []

        # Fetch full narratives from baseline store for each result
        baseline_base = os.getenv("BASELINE_URL", "http://localhost:8010")
        async with httpx.AsyncClient(timeout=30) as http:
            for r in results:
                executor.check_cancelled(task_id)
                meta = r.get("metadata", {})
                topic_path = meta.get("topic_path", "")
                if not topic_path:
                    continue
                resp = await http.get(f"{baseline_base}/baselines/{topic_path}/current")
                if resp.status_code == 200:
                    data = resp.json()
                    related_pages.append({
                        "topic_path": topic_path,
                        "narrative": data.get("narrative", ""),
                        "version": data.get("version_number"),
                        "score": r.get("score", 0.0),
                    })
    except Exception as e:
        logger.warning("[%s] find_related: search failed (%s)", task_id, e)

    logger.info("[%s] find_related: found %d related pages", task_id, len(related_pages))
    return {"related_pages": related_pages}


async def update_pages(state: WikiState, config: RunnableConfig) -> dict[str, Any]:
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    executor.check_cancelled(task_id)

    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    client = _get_openai_client()
    updated_pages: list[dict] = []

    for page in state.get("related_pages", []):
        executor.check_cancelled(task_id)
        score = page.get("score", 0.0)
        if score < 0.6:
            continue
        topic_path = page.get("topic_path", "")
        existing = page.get("narrative", "")
        if not topic_path or not existing:
            continue

        try:
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": PAGE_UPDATER_SYSTEM_PROMPT},
                    {"role": "user", "content": (
                        f"Existing page ({topic_path}):\n{existing[:2000]}\n\n"
                        f"New summary:\n{state.get('summary', '')[:500]}\n\n"
                        f"New extracted data:\n{json.dumps(state.get('extracted', {}))[:800]}"
                    )},
                ],
                temperature=0.2,
                max_completion_tokens=2048,
                timeout=60,
            )
            raw = response.choices[0].message.content or ""
            parsed = _parse_json_response(raw)
            updated_pages.append({
                "topic_path": topic_path,
                "new_content": parsed.get("updated_content", existing),
                "delta_summary": parsed.get("delta_summary", "Updated from new source."),
                "is_new": False,
                "from_version": page.get("version"),
            })
            logger.info("[%s] update_pages: updated %s", task_id, topic_path)
        except Exception as e:
            logger.warning("[%s] update_pages: failed to update %s — %s", task_id, topic_path, e)

    return {"updated_pages": updated_pages}


async def create_source_page(state: WikiState, config: RunnableConfig) -> dict[str, Any]:
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    executor.check_cancelled(task_id)

    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    client = _get_openai_client()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SOURCE_PAGE_WRITER_SYSTEM_PROMPT},
                {"role": "user", "content": (
                    f"Summary:\n{state.get('summary', '')[:800]}\n\n"
                    f"Extracted:\n{json.dumps(state.get('extracted', {}))[:800]}\n\n"
                    f"Source URL: {state.get('source_url', 'N/A')}\n"
                    f"Source Title: {state.get('source_title', 'Untitled')}\n"
                    f"Date: {today}"
                )},
            ],
            temperature=0.3,
            max_completion_tokens=2048,
            timeout=60,
        )
        raw = response.choices[0].message.content or ""
        parsed = _parse_json_response(raw)
        page_content = parsed.get("page_content", "")
        suggested_path = parsed.get("suggested_topic_path", f"wiki.sources.{today}-source")
    except Exception as e:
        logger.warning("[%s] create_source_page: LLM failed (%s), using fallback", task_id, e)
        title = state.get("source_title", "Untitled")
        page_content = (
            f"# {title}\n\n"
            f"**Date:** {today}\n\n"
            f"## Summary\n\n{state.get('summary', '')}\n"
        )
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower())[:40].strip("-")
        suggested_path = f"wiki.sources.{today}-{slug}"

    updated_pages = list(state.get("updated_pages", []))
    updated_pages.append({
        "topic_path": suggested_path,
        "new_content": page_content,
        "delta_summary": "New source ingested.",
        "is_new": True,
        "from_version": None,
    })

    logger.info("[%s] create_source_page: created %s", task_id, suggested_path)
    return {"new_page_path": suggested_path, "updated_pages": updated_pages}


async def store_memories(state: WikiState, config: RunnableConfig) -> dict[str, Any]:
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    context_id = config["configurable"].get("context_id", "")
    executor.check_cancelled(task_id)

    memory_url = os.getenv("MEMORY_AGENT_URL", "http://localhost:8009")
    namespace = state.get("namespace") or "wiki_geo"

    # Build memory text: summary + entity list + source page path
    entities = state.get("extracted", {}).get("entities", {})
    entity_list: list[str] = []
    if isinstance(entities, dict):
        for v in entities.values():
            if isinstance(v, list):
                entity_list.extend(str(e) for e in v)

    memory_text = state.get("summary", "")
    if entity_list:
        memory_text += "\n\nEntities: " + ", ".join(entity_list[:20])
    new_page_path = state.get("new_page_path", "")
    if new_page_path:
        memory_text += f"\n\nSource page: {new_page_path}"

    # Also include topic_path metadata so find_related can recover it later
    write_payload = json.dumps({
        "text": memory_text,
        "namespace": namespace,
        "metadata": {"topic_path": new_page_path},
    })

    stored = False
    try:
        await _call_a2a_agent(memory_url, write_payload, context_id)
        stored = True
        logger.info("[%s] store_memories: stored to namespace=%s", task_id, namespace)
    except Exception as e:
        logger.warning("[%s] store_memories: memory write failed (%s)", task_id, e)

    return {"stored_to_memory": stored}


async def write_baselines(state: WikiState, config: RunnableConfig) -> dict[str, Any]:
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    executor.check_cancelled(task_id)

    baseline_versions: dict[str, int] = {}

    for page in state.get("updated_pages", []):
        executor.check_cancelled(task_id)
        topic_path = page.get("topic_path", "")
        content = page.get("new_content", "")
        if not topic_path or not content:
            continue

        try:
            # Ensure topic is registered
            existing = await _baseline_get(f"/baselines/{topic_path}/current")
            if existing is None:
                # Try registering the topic
                display_name = topic_path.split(".")[-1].replace("-", " ").title()
                try:
                    await _baseline_post("/topics", {
                        "topic_path": topic_path,
                        "display_name": display_name,
                    })
                except Exception:
                    pass  # May already exist (409)

            # Write new version
            vr = await _baseline_post(
                f"/baselines/{topic_path}/versions",
                {"narrative": content, "citations": []},
            )
            new_version = vr.get("version_number", 1)
            baseline_versions[topic_path] = new_version

            # Write delta for updated (non-new) pages
            if not page.get("is_new") and page.get("from_version") is not None:
                try:
                    await _baseline_post(
                        f"/baselines/{topic_path}/deltas",
                        {
                            "from_version": page["from_version"],
                            "to_version": new_version,
                            "delta_summary": page.get("delta_summary", "Updated."),
                            "claims_added": [],
                            "claims_superseded": [],
                            "article_metadata": {
                                "source": state.get("source_url", ""),
                                "title": state.get("source_title", ""),
                            },
                        },
                    )
                except Exception as e:
                    logger.warning("[%s] write_baselines: delta write failed for %s — %s", task_id, topic_path, e)

            logger.info("[%s] write_baselines: wrote %s v%s", task_id, topic_path, new_version)
        except Exception as e:
            logger.warning("[%s] write_baselines: failed for %s — %s", task_id, topic_path, e)

    return {"baseline_versions": baseline_versions}


async def write_files(state: WikiState, config: RunnableConfig) -> dict[str, Any]:
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    executor.check_cancelled(task_id)

    from agents.wiki_agent.page_writer import topic_path_to_file_path, write_wiki_file

    files_written: list[str] = []
    for page in state.get("updated_pages", []):
        executor.check_cancelled(task_id)
        topic_path = page.get("topic_path", "")
        content = page.get("new_content", "")
        if not topic_path or not content:
            continue
        try:
            file_path = topic_path_to_file_path(topic_path)
            await asyncio.to_thread(write_wiki_file, file_path, content)
            files_written.append(str(file_path))
            logger.info("[%s] write_files: wrote %s", task_id, file_path)
        except Exception as e:
            logger.warning("[%s] write_files: failed to write %s — %s", task_id, topic_path, e)

    return {"files_written": files_written}


async def update_index(state: WikiState, config: RunnableConfig) -> dict[str, Any]:
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    executor.check_cancelled(task_id)

    from agents.wiki_agent import page_writer

    index_path = page_writer.get_wiki_index_path()
    current_index = await asyncio.to_thread(page_writer.read_wiki_file, index_path)
    if not current_index:
        current_index = "# Wiki Index\n\n"

    new_pages = [p["topic_path"] for p in state.get("updated_pages", []) if p.get("is_new")]
    changed_pages = [p["topic_path"] for p in state.get("updated_pages", []) if not p.get("is_new")]

    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    client = _get_openai_client()
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": INDEX_UPDATER_SYSTEM_PROMPT},
                {"role": "user", "content": (
                    f"Current index.md:\n{current_index[:3000]}\n\n"
                    f"New pages added: {', '.join(new_pages) or 'none'}\n"
                    f"Updated pages: {', '.join(changed_pages) or 'none'}"
                )},
            ],
            temperature=0.1,
            max_completion_tokens=2048,
            timeout=60,
        )
        new_index = response.choices[0].message.content or current_index
    except Exception as e:
        logger.warning("[%s] update_index: LLM failed (%s), index not updated", task_id, e)
        new_index = current_index

    try:
        await asyncio.to_thread(page_writer.write_wiki_file, index_path, new_index)
    except Exception as e:
        logger.warning("[%s] update_index: write failed — %s", task_id, e)

    return {}


async def append_log(state: WikiState, config: RunnableConfig) -> dict[str, Any]:
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    executor.check_cancelled(task_id)

    from agents.wiki_agent.page_writer import get_wiki_log_path, append_to_file

    log_path = get_wiki_log_path()
    timestamp = datetime.now(timezone.utc).isoformat()
    source_title = state.get("source_title", "Untitled")
    new_page = state.get("new_page_path", "")
    n_files = len(state.get("files_written", []))
    n_updated = len([p for p in state.get("updated_pages", []) if not p.get("is_new")])

    entry = (
        f"- [{timestamp}] INGEST | "
        f"source='{source_title}' | "
        f"new_page={new_page} | "
        f"pages_updated={n_updated} | "
        f"files_written={n_files}\n"
    )

    try:
        await asyncio.to_thread(append_to_file, log_path, entry)
    except Exception as e:
        logger.warning("[%s] append_log: write failed — %s", task_id, e)

    return {}


async def finalize(state: WikiState, config: RunnableConfig) -> dict[str, Any]:
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    executor.check_cancelled(task_id)

    output = json.dumps({
        "status": "ok",
        "new_page_path": state.get("new_page_path", ""),
        "pages_updated": [
            p["topic_path"] for p in state.get("updated_pages", []) if not p.get("is_new")
        ],
        "files_written": state.get("files_written", []),
        "baseline_versions": state.get("baseline_versions", {}),
        "stored_to_memory": state.get("stored_to_memory", False),
        "summary_preview": (state.get("summary") or "")[:200],
    })
    return {"output": output}


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_wiki_ingest_graph() -> CompiledStateGraph:
    graph = StateGraph(WikiState)

    for name, fn in [
        ("summarize", summarize),
        ("extract", extract),
        ("find_related", find_related),
        ("update_pages", update_pages),
        ("create_source_page", create_source_page),
        ("store_memories", store_memories),
        ("write_baselines", write_baselines),
        ("write_files", write_files),
        ("update_index", update_index),
        ("append_log", append_log),
        ("finalize", finalize),
    ]:
        graph.add_node(name, fn)

    graph.set_entry_point("summarize")
    graph.add_edge("summarize", "extract")
    graph.add_edge("extract", "find_related")
    graph.add_edge("find_related", "update_pages")
    graph.add_edge("update_pages", "create_source_page")
    graph.add_edge("create_source_page", "store_memories")
    graph.add_edge("store_memories", "write_baselines")
    graph.add_edge("write_baselines", "write_files")
    graph.add_edge("write_files", "update_index")
    graph.add_edge("update_index", "append_log")
    graph.add_edge("append_log", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile()
