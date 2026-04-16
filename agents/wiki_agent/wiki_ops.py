"""Query and lint operations for the wiki agent (direct async, no LangGraph)."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared helpers (duplicated from graph.py to keep modules independent)
# ---------------------------------------------------------------------------

def _get_openai_client():
    from openai import AsyncOpenAI
    kwargs: dict[str, Any] = {}
    if os.getenv("OPENAI_API_KEY"):
        kwargs["api_key"] = os.getenv("OPENAI_API_KEY")
    if os.getenv("OPENAI_BASE_URL"):
        kwargs["base_url"] = os.getenv("OPENAI_BASE_URL")
    return AsyncOpenAI(**kwargs)


async def _call_a2a_agent(url: str, text: str, context_id: str = "") -> str:
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


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

async def run_query(executor: Any, task_id: str, input_json: dict[str, Any]) -> dict[str, Any]:
    """
    1. Semantic search memory_agent for relevant pages
    2. Fetch full narratives from baseline_store for top results
    3. LLM synthesize answer with inline citations
    4. Optionally save answer as new wiki page under wiki.queries.*
    """
    executor.check_cancelled(task_id)
    query = input_json.get("query", "")
    namespace = input_json.get("namespace", "wiki_geo")
    save_as_page = input_json.get("save_as_page", False)
    limit = int(input_json.get("limit", 5))

    memory_url = os.getenv("MEMORY_AGENT_URL", "http://localhost:8009")
    baseline_url = os.getenv("BASELINE_URL", "http://localhost:8010")
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    # Search memory for relevant page references
    search_payload = json.dumps({"query": query, "namespace": namespace, "limit": limit})
    raw = await _call_a2a_agent(memory_url, search_payload)
    try:
        results = json.loads(raw).get("results", []) if raw else []
    except (json.JSONDecodeError, AttributeError):
        results = []

    # Fetch full narratives from baseline_store
    page_contents: list[dict] = []
    async with httpx.AsyncClient(timeout=30) as http:
        for r in results:
            executor.check_cancelled(task_id)
            meta = r.get("metadata", {})
            topic_path = meta.get("topic_path", "")
            if not topic_path:
                continue
            resp = await http.get(f"{baseline_url}/baselines/{topic_path}/current")
            if resp.status_code == 200:
                data = resp.json()
                page_contents.append({
                    "topic_path": topic_path,
                    "narrative": data.get("narrative", ""),
                    "score": r.get("score", 0.0),
                })

    executor.check_cancelled(task_id)

    # LLM synthesize answer
    client = _get_openai_client()
    if page_contents:
        pages_text = "\n\n---\n\n".join(
            f"### {p['topic_path']} (relevance: {p['score']:.2f})\n{p['narrative'][:1500]}"
            for p in page_contents
        )
    else:
        pages_text = "_No relevant wiki pages found._"

    system_prompt = (
        "You are a geopolitics/IR intelligence analyst. "
        "Given a query and relevant wiki pages, synthesize a clear, accurate answer "
        "with inline citations to the page paths you used. "
        "Format as Markdown. End with a '## Sources' section listing cited page paths."
    )
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Query: {query}\n\n## Relevant Wiki Pages\n\n{pages_text}"},
            ],
            temperature=0.3,
            max_completion_tokens=2048,
            timeout=60,
        )
        answer = response.choices[0].message.content or "No answer generated."
    except Exception as e:
        logger.warning("[%s] run_query: LLM synthesis failed — %s", task_id, e)
        answer = pages_text

    citations = [p["topic_path"] for p in page_contents]
    result_payload: dict[str, Any] = {"query": query, "answer": answer, "citations": citations}

    # Optionally save as a new wiki page
    if save_as_page:
        executor.check_cancelled(task_id)
        from agents.wiki_agent.page_writer import topic_path_to_file_path, write_wiki_file
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        slug = re.sub(r"[^a-z0-9]+", "-", query.lower())[:40].strip("-")
        topic_path = f"wiki.queries.{today}-{slug}"
        page_content = f"# Query: {query}\n\n_{today}_\n\n{answer}"
        try:
            file_path = topic_path_to_file_path(topic_path)
            await asyncio.to_thread(write_wiki_file, file_path, page_content)
            result_payload["saved_as"] = topic_path
            logger.info("[%s] run_query: saved answer to %s", task_id, topic_path)
        except Exception as e:
            logger.warning("[%s] run_query: save_as_page write failed — %s", task_id, e)

    return result_payload


# ---------------------------------------------------------------------------
# Lint
# ---------------------------------------------------------------------------

async def run_lint(executor: Any, task_id: str, input_json: dict[str, Any]) -> dict[str, Any]:
    """
    1. List all .md files under WIKI_DIR
    2. Find orphans (not referenced in index.md)
    3. Find stale pages (not mentioned in last 30 log lines)
    4. LLM produces structured lint report
    5. Write report to {WIKI_DIR}/lint-{date}.md
    """
    executor.check_cancelled(task_id)

    from agents.wiki_agent.page_writer import (
        read_wiki_file,
        write_wiki_file,
        get_wiki_index_path,
        get_wiki_log_path,
        list_all_wiki_pages,
        get_wiki_dir,
    )

    wiki_dir = get_wiki_dir()
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    index_content = await asyncio.to_thread(read_wiki_file, get_wiki_index_path())
    log_content = await asyncio.to_thread(read_wiki_file, get_wiki_log_path())
    all_pages = await asyncio.to_thread(list_all_wiki_pages, wiki_dir)

    executor.check_cancelled(task_id)

    # Orphan check: .md files not referenced in index.md
    orphans = [p for p in all_pages if p not in (index_content or "") and p not in ("index.md", "log.md")]

    # Stale check: pages not mentioned in last 30 log lines (excluding meta files and sources)
    recent_log = "\n".join((log_content or "").splitlines()[-30:])
    stale = [
        p for p in all_pages
        if p not in recent_log
        and not p.startswith("sources/")
        and p not in ("index.md", "log.md")
        and not p.startswith("lint-")
    ]

    executor.check_cancelled(task_id)

    # LLM lint analysis
    client = _get_openai_client()
    lint_system_prompt = (
        "You are a wiki health auditor for a geopolitics/IR intelligence wiki. "
        "Given the wiki index, recent log, orphan pages, and stale pages, produce a structured lint report. "
        "Include:\n"
        "## Contradictions — pairs of pages that may have conflicting claims\n"
        "## Suggested New Pages — topics that should have their own page but don't\n"
        "## Health Notes — general observations about wiki quality and coverage gaps\n"
        "Be specific and actionable."
    )
    user_content = (
        f"## Index (first 2000 chars)\n{(index_content or '')[:2000]}\n\n"
        f"## Recent Log (last 30 lines)\n{recent_log[:1000]}\n\n"
        f"## Orphan Pages ({len(orphans)})\n" + "\n".join(f"- {p}" for p in orphans[:20]) + "\n\n"
        f"## Stale Pages ({len(stale)})\n" + "\n".join(f"- {p}" for p in stale[:20])
    )

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": lint_system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=0.2,
            max_completion_tokens=2048,
            timeout=60,
        )
        lint_report = response.choices[0].message.content or "No lint report generated."
    except Exception as e:
        logger.warning("[%s] run_lint: LLM failed — %s", task_id, e)
        lint_report = (
            f"## Orphan Pages ({len(orphans)})\n" + "\n".join(f"- {p}" for p in orphans) + "\n\n"
            f"## Stale Pages ({len(stale)})\n" + "\n".join(f"- {p}" for p in stale)
        )

    # Write lint report to disk
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    report_filename = f"lint-{today}.md"
    report_path = wiki_dir / report_filename
    full_report = f"# Wiki Lint Report — {today}\n\n{lint_report}\n"
    report_path_str = ""
    try:
        await asyncio.to_thread(write_wiki_file, report_path, full_report)
        report_path_str = str(report_path)
        logger.info("[%s] run_lint: wrote report to %s", task_id, report_path)
    except Exception as e:
        logger.warning("[%s] run_lint: report write failed — %s", task_id, e)

    return {
        "orphans": orphans,
        "stale": stale,
        "report": lint_report,
        "report_path": report_path_str,
    }
