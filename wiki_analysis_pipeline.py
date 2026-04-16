"""
Wiki + Lead Analyst A Pipeline
================================
Compounding intelligence analysis loop:

  1. Query wiki for existing knowledge on the topic → becomes Lead Analyst baselines
  2. Assess report relevance (relevancy agent)
  3. If relevant → run Lead Analyst A (dynamic specialist discovery, ACH, baseline comparison)
  4. Ingest Lead Analyst synthesis back into the wiki (auto-updates pages + disk)
  5. Print summary of what changed

The wiki page on a topic IS the baseline. Each run makes both the analysis and
the wiki richer — the wiki feeds the analyst, and the analyst feeds the wiki.

Run against local stack:
    WIKI_DIR=./wiki python wiki_analysis_pipeline.py

Run against Docker:
    WIKI_AGENT_URL=http://localhost:8011 \
    LEAD_ANALYST_URL=http://localhost:8005 \
    RELEVANCY_URL=http://localhost:8003 \
    python wiki_analysis_pipeline.py
"""

from __future__ import annotations

import asyncio
import json
import os
import textwrap
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

# ── Service URLs (override via env vars) ──────────────────────────────────────
WIKI_AGENT_URL   = os.getenv("WIKI_AGENT_URL",   "http://localhost:8011")
LEAD_ANALYST_URL = os.getenv("LEAD_ANALYST_URL", "http://localhost:8005")
RELEVANCY_URL    = os.getenv("RELEVANCY_URL",    "http://localhost:8003")

# ── Defaults (edit or override at prompts) ────────────────────────────────────
DEFAULT_WIKI_QUERY = (
    "current assessment of Iran nuclear program and diplomatic posture"
)
DEFAULT_KEY_QUESTIONS = (
    "1. Are there new signals of diplomatic engagement or breakdown?\n"
    "2. What is the risk of escalation in the next 30 days?\n"
    "3. What is the impact on South East Asia's energy security?\n"
    "4. What is the impact on South East Asia's supply chain dependencies?"
)
DEFAULT_NAMESPACE = "wiki_geo"

RELEVANCE_THRESHOLD = 0.5

# ── HTTP timeout (lead analyst is slow with many specialists) ─────────────────
TIMEOUT = httpx.Timeout(600.0)


# ─────────────────────────────────────────────────────────────────────────────
# A2A helpers (identical pattern to demo.py)
# ─────────────────────────────────────────────────────────────────────────────

def _a2a_payload(text: str, **metadata: str) -> dict[str, Any]:
    """Build a minimal A2A message/send JSON-RPC payload."""
    msg: dict[str, Any] = {
        "kind": "message",
        "role": "user",
        "messageId": str(uuid.uuid4()),
        "parts": [{"kind": "text", "text": text}],
    }
    if metadata:
        msg["metadata"] = {k: v for k, v in metadata.items() if v}
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "message/send",
        "params": {"message": msg},
    }


def _extract_text(result: dict[str, Any]) -> str:
    """Pull the first text part out of an A2A task result."""
    for part in result.get("parts", []):
        if part.get("kind") == "text":
            return part["text"]
    status = result.get("status", {})
    msg = status.get("message", {})
    for part in msg.get("parts", []):
        if part.get("kind") == "text":
            return part["text"]
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# Pretty print helpers (identical to demo.py)
# ─────────────────────────────────────────────────────────────────────────────

def hr(char: str = "─", width: int = 72) -> None:
    print(char * width)


def section(title: str) -> None:
    print()
    hr("═")
    print(f"  {title}")
    hr("═")


def wrap(text: str, indent: int = 2) -> None:
    prefix = " " * indent
    for para in text.split("\n"):
        if para.strip():
            print(textwrap.fill(para.strip(), width=70, initial_indent=prefix, subsequent_indent=prefix))
        else:
            print()


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — Wiki query (pulls existing knowledge as baselines)
# ─────────────────────────────────────────────────────────────────────────────

async def query_wiki(
    client: httpx.AsyncClient,
    wiki_query: str,
    namespace: str,
) -> tuple[str, list[str]]:
    """
    Query the wiki agent for existing knowledge on the topic.
    Returns (answer_text, citations_list).
    answer_text becomes the `baselines` input for Lead Analyst A.
    """
    payload = _a2a_payload(json.dumps({
        "query": wiki_query,
        "namespace": namespace,
        "limit": 5,
    }))
    try:
        r = await client.post(f"{WIKI_AGENT_URL}/", json=payload)
        r.raise_for_status()
        body = r.json()
        if "error" in body:
            print(f"  [wiki] Query error: {body['error']}")
            return "", []
        result_text = _extract_text(body.get("result", {}))
        if not result_text:
            return "", []
        wiki_result = json.loads(result_text)
        answer = wiki_result.get("answer", "")
        citations = wiki_result.get("citations", [])
        return answer, citations
    except Exception as e:
        print(f"  [wiki] Query failed ({e}) — proceeding with empty baseline")
        return "", []


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 — Relevancy check
# ─────────────────────────────────────────────────────────────────────────────

async def check_relevance(
    client: httpx.AsyncClient,
    report_text: str,
    question: str,
    wiki_answer: str,
) -> dict[str, Any]:
    """
    Call the relevancy agent. Augments the question with current wiki context
    so the agent judges relevance against what we already know.
    """
    full_question = question
    if wiki_answer:
        full_question = (
            f"{question}\n\n"
            f"Current wiki context (assess whether the report updates or contradicts this):\n"
            f"{textwrap.shorten(wiki_answer, width=1500, placeholder='...')}"
        )

    payload = _a2a_payload(
        json.dumps({"text": report_text, "question": full_question})
    )
    try:
        r = await client.post(f"{RELEVANCY_URL}/", json=payload)
        r.raise_for_status()
        body = r.json()
        if "error" in body:
            raise RuntimeError(f"Relevancy agent error: {body['error']}")
        result_text = _extract_text(body.get("result", {}))
        return json.loads(result_text)
    except json.JSONDecodeError:
        return {"relevant": False, "confidence": 0.0, "reasoning": result_text, "error": True}
    except Exception as e:
        print(f"  [relevancy] Check failed ({e}) — assuming relevant")
        return {"relevant": True, "confidence": 1.0, "reasoning": "relevancy check unavailable"}


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 — Lead Analyst A
# ─────────────────────────────────────────────────────────────────────────────

async def run_lead_analyst(
    client: httpx.AsyncClient,
    report_text: str,
    wiki_answer: str,
    key_questions: str,
) -> str:
    """
    Run Lead Analyst A with the wiki answer as the baseline context.
    Returns the final synthesis text.
    """
    input_json = json.dumps({
        "text": report_text,
        "baselines": wiki_answer,
        "key_questions": key_questions,
    })
    payload = _a2a_payload(input_json)
    r = await client.post(f"{LEAD_ANALYST_URL}/", json=payload)
    r.raise_for_status()
    body = r.json()
    if "error" in body:
        raise RuntimeError(f"Lead analyst error: {body['error']}")
    return _extract_text(body.get("result", {}))


# ─────────────────────────────────────────────────────────────────────────────
# Step 4 — Wiki ingest (feeds synthesis back into the wiki)
# ─────────────────────────────────────────────────────────────────────────────

async def ingest_into_wiki(
    client: httpx.AsyncClient,
    synthesis: str,
    namespace: str,
) -> dict[str, Any]:
    """
    Ingest the Lead Analyst synthesis back into the wiki.
    This updates related pages, creates a new source page, writes .md files to disk,
    and updates the baseline_store — so the next run starts richer.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    source_title = f"Lead Analyst A — {today}"

    payload = _a2a_payload(json.dumps({
        "input_text": synthesis,
        "source_title": source_title,
        "source_url": "",
        "namespace": namespace,
    }))
    try:
        r = await client.post(f"{WIKI_AGENT_URL}/", json=payload)
        r.raise_for_status()
        body = r.json()
        if "error" in body:
            print(f"  [wiki] Ingest error: {body['error']}")
            return {}
        result_text = _extract_text(body.get("result", {}))
        if not result_text:
            return {}
        return json.loads(result_text)
    except Exception as e:
        print(f"  [wiki] Ingest failed ({e}) — wiki not updated this run")
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# Main pipeline (one report)
# ─────────────────────────────────────────────────────────────────────────────

async def process_report(
    client: httpx.AsyncClient,
    report_text: str,
    wiki_query: str,
    key_questions: str,
    namespace: str,
) -> bool:
    """Run the full pipeline for one report. Returns True if analysis was run."""

    # ── 1. Wiki query ─────────────────────────────────────────────────────────
    section("STEP 1 — Wiki context query")
    print(f"  Query: {wiki_query}", flush=True)
    print("  Searching wiki...", flush=True)

    wiki_answer, citations = await query_wiki(client, wiki_query, namespace)

    if citations:
        print(f"  Citations ({len(citations)}): {', '.join(citations)}")
        print("  Baseline preview:")
        wrap(textwrap.shorten(wiki_answer, width=400, placeholder="..."))
    else:
        print("  No wiki pages found — this may be the first run. Proceeding with empty baseline.")

    # ── 2. Relevancy check ────────────────────────────────────────────────────
    section("STEP 2 — Relevancy check")
    print("  Calling relevancy agent...", flush=True)

    relevance = await check_relevance(client, report_text, wiki_query, wiki_answer)
    relevant   = relevance.get("relevant", False)
    confidence = relevance.get("confidence", 0.0)
    reasoning  = relevance.get("reasoning", "")

    print(f"  Relevant  : {'YES' if relevant else 'NO'}")
    print(f"  Confidence: {confidence:.0%}")
    wrap(f"Reasoning: {reasoning}")

    if not relevant or confidence < RELEVANCE_THRESHOLD:
        print()
        print("  Report is not sufficiently relevant — skipping analysis.")
        return False

    # ── 3. Lead Analyst A ─────────────────────────────────────────────────────
    section("STEP 3 — Lead Analyst A (dynamic specialist discovery)")
    print("  Dispatching to lead analyst — this may take a few minutes...", flush=True)

    synthesis = await run_lead_analyst(client, report_text, wiki_answer, key_questions)

    print()
    print("  ── Analysis output ──")
    print()
    for line in synthesis.splitlines():
        print(f"  {line}")

    # ── 4. Wiki ingest ────────────────────────────────────────────────────────
    section("STEP 4 — Wiki ingest (auto)")
    print("  Ingesting synthesis into wiki...", flush=True)

    ingest_result = await ingest_into_wiki(client, synthesis, namespace)

    if ingest_result:
        new_page   = ingest_result.get("new_page_path", "—")
        updated    = ingest_result.get("pages_updated", [])
        files      = ingest_result.get("files_written", [])
        in_memory  = ingest_result.get("stored_to_memory", False)
        bv         = ingest_result.get("baseline_versions", {})

        print(f"  New page:      {new_page}")
        print(f"  Pages updated: {', '.join(updated) if updated else 'none'}")
        print(f"  Files written: {len(files)}")
        print(f"  Stored to memory: {'yes' if in_memory else 'no'}")
        if bv:
            print("  Baseline versions:")
            for path, ver in bv.items():
                print(f"    {path} → v{ver}")
    else:
        print("  Wiki ingest returned no result.")

    # ── 5. Summary ────────────────────────────────────────────────────────────
    section("PIPELINE COMPLETE")
    print(f"  Wiki query:    {wiki_query}")
    print(f"  Namespace:     {namespace}")
    if citations:
        print(f"  Baseline from: {', '.join(citations)}")
    if ingest_result:
        print(f"  Wiki updated:  {ingest_result.get('new_page_path', '—')}")
        updated = ingest_result.get("pages_updated", [])
        if updated:
            print(f"  Also updated:  {', '.join(updated)}")
    print()
    print("  Next run will use the updated wiki pages as baseline context.")
    print()

    return True


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

async def main() -> None:
    print()
    hr("═")
    print("  MISSION CONTROL — WIKI + LEAD ANALYST A PIPELINE")
    hr("═")
    print()
    print("  Services:")
    print(f"    Wiki Agent:    {WIKI_AGENT_URL}")
    print(f"    Lead Analyst:  {LEAD_ANALYST_URL}")
    print(f"    Relevancy:     {RELEVANCY_URL}")
    print()
    print("  How it works:")
    print("    1. Wiki is queried for existing knowledge → becomes Lead Analyst baselines")
    print("    2. Lead Analyst runs with wiki context → produces synthesis")
    print("    3. Synthesis is ingested back into the wiki → next run starts richer")
    print()

    # ── Config prompts ────────────────────────────────────────────────────────
    wiki_query = (
        input(f"  Wiki query [{DEFAULT_WIKI_QUERY}]:\n  > ").strip()
        or DEFAULT_WIKI_QUERY
    )

    namespace = (
        input(f"  Namespace [{DEFAULT_NAMESPACE}]: ").strip()
        or DEFAULT_NAMESPACE
    )

    use_default_kq = input(f"  Use default key questions? [Y/n]: ").strip().lower()
    if use_default_kq in ("", "y", "yes"):
        key_questions = DEFAULT_KEY_QUESTIONS
    else:
        print("  Enter key questions (blank line to finish):")
        lines = []
        while True:
            line = input("  > ")
            if not line:
                break
            lines.append(line)
        key_questions = "\n".join(lines)

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        first = True

        while True:
            print()
            if first:
                print("  Paste the incoming report text (blank line to finish):")
            else:
                print("  Paste the next report (blank line to finish), or press Enter to quit:")
            first = False

            lines = []
            while True:
                line = input("  > ")
                if not line:
                    break
                lines.append(line)

            report_text = "\n".join(lines).strip()
            if not report_text:
                print()
                print("  No report text — exiting.")
                break

            await process_report(
                client,
                report_text=report_text,
                wiki_query=wiki_query,
                key_questions=key_questions,
                namespace=namespace,
            )

            cont = input("  Process another report? [Y/n]: ").strip().lower()
            if cont not in ("", "y", "yes"):
                break

    print()
    hr("═")
    print("  Done.")
    hr("═")
    print()


if __name__ == "__main__":
    asyncio.run(main())
