"""
Mission Control Demo Pipeline
==============================
Orchestrates the full report-ingestion loop:

  1. Fetch current baseline from baseline_store
  2. Assess report relevance (relevancy agent)
  3. If relevant → run lead analyst (dynamic specialist discovery, ACH, baseline comparison)
  4. Write updated baseline back to baseline_store (new version + delta)
  5. Print a summary of what changed

Run against Docker:
    python demo.py

Run against a different stack:
    RELEVANCY_URL=http://localhost:8003 \
    LEAD_ANALYST_URL=http://localhost:8005 \
    BASELINE_URL=http://localhost:8010 \
    python demo.py

The script loops: after processing a report it asks whether to feed in another one,
reusing the freshly-written baseline as context for the next relevancy check and analysis.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import textwrap
import uuid
from typing import Any

import httpx

# ── Service URLs (override via env vars) ─────────────────────────────────────
RELEVANCY_URL   = os.getenv("RELEVANCY_URL",    "http://localhost:8003")
LEAD_ANALYST_URL = os.getenv("LEAD_ANALYST_URL", "http://localhost:8005")
BASELINE_URL    = os.getenv("BASELINE_URL",     "http://localhost:8010")

# ── Demo defaults (edit or override at the prompts) ───────────────────────────
DEFAULT_TOPIC        = "geo.middle_east.iran"
DEFAULT_TOPIC_LABEL  = "Iran"
DEFAULT_QUESTION     = "What is the current status of Iran's nuclear programme and diplomatic posture?"
DEFAULT_KEY_QUESTIONS = (
    "1. Are there new signals of diplomatic engagement or breakdown?\n"
    "2. What is the risk of escalation in the next 30 days?\n"
    "3. What is the impact on South East Asia's energy security?\n"
    "4. What is the impact on South East Asia's supply chain dependencies?"
)

RELEVANCE_THRESHOLD = 0.5   # minimum confidence to treat a report as relevant

# ── HTTP timeout (lead analyst can be slow with many specialists) ─────────────
TIMEOUT = httpx.Timeout(600.0)


# ─────────────────────────────────────────────────────────────────────────────
# A2A helper
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
    # Some agents nest output under status.message
    status = result.get("status", {})
    msg = status.get("message", {})
    for part in msg.get("parts", []):
        if part.get("kind") == "text":
            return part["text"]
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — Baseline retrieval
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_or_seed_baseline(
    client: httpx.AsyncClient,
    topic: str,
    topic_label: str,
) -> tuple[str, int | None]:
    """
    Return (narrative, version_number).
    If the topic doesn't exist yet, register it and return ("", None).
    """
    # Try to get the current baseline
    r = await client.get(f"{BASELINE_URL}/baselines/{topic}/current")
    if r.status_code == 200:
        data = r.json()
        return data["narrative"], data["version_number"]

    if r.status_code == 404:
        # Check if the topic itself is missing vs just no versions written
        tr = await client.get(f"{BASELINE_URL}/topics")
        existing = [t["topic_path"] for t in tr.json().get("topics", [])]
        if topic not in existing:
            print(f"  [baseline] Topic '{topic}' not registered — registering now...")
            reg = await client.post(
                f"{BASELINE_URL}/topics",
                json={"topic_path": topic, "display_name": topic_label},
            )
            if reg.status_code not in (200, 201, 409):
                reg.raise_for_status()
            print(f"  [baseline] Registered '{topic}'.")
        else:
            print(f"  [baseline] Topic '{topic}' registered but no versions yet.")
        return "", None

    r.raise_for_status()
    return "", None   # unreachable


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 — Relevancy check
# ─────────────────────────────────────────────────────────────────────────────

async def check_relevance(
    client: httpx.AsyncClient,
    report_text: str,
    question: str,
    baseline: str,
) -> dict[str, Any]:
    """
    Call the relevancy agent. Augments the question with current baseline context
    so the agent can judge relevance against *what we already know*.
    """
    full_question = question
    if baseline:
        full_question = (
            f"{question}\n\n"
            f"Current baseline context (assess whether the report updates or contradicts this):\n"
            f"{textwrap.shorten(baseline, width=1500, placeholder='...')}"
        )

    payload = _a2a_payload(
        json.dumps({"text": report_text, "question": full_question})
    )
    r = await client.post(f"{RELEVANCY_URL}/", json=payload)
    r.raise_for_status()
    body = r.json()
    if "error" in body:
        raise RuntimeError(f"Relevancy agent error: {body['error']}")

    result = body.get("result", {})
    raw_text = _extract_text(result)
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        return {"relevant": False, "confidence": 0.0, "reasoning": raw_text, "error": True}


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 — Lead analyst (dynamic mode)
# ─────────────────────────────────────────────────────────────────────────────

async def run_lead_analyst(
    client: httpx.AsyncClient,
    report_text: str,
    baseline: str,
    key_questions: str,
) -> str:
    """
    Send the report to the lead analyst agent (dynamic specialist discovery).
    Returns the final synthesis text.
    """
    # Lead analyst expects JSON with text/baselines/key_questions
    input_json = json.dumps({
        "text": report_text,
        "baselines": baseline,
        "key_questions": key_questions,
    })
    payload = _a2a_payload(input_json)
    r = await client.post(f"{LEAD_ANALYST_URL}/", json=payload)
    r.raise_for_status()
    body = r.json()
    if "error" in body:
        raise RuntimeError(f"Lead analyst error: {body['error']}")

    result = body.get("result", {})
    return _extract_text(result)


# ─────────────────────────────────────────────────────────────────────────────
# Step 4 — Baseline write-back
# ─────────────────────────────────────────────────────────────────────────────

async def write_baseline(
    client: httpx.AsyncClient,
    topic: str,
    old_version: int | None,
    new_narrative: str,
    delta_summary: str,
    claims_added: list[str],
    claims_superseded: list[str],
    article_metadata: dict[str, Any],
) -> int:
    """
    Write a new baseline version + delta. Returns the new version number.
    """
    # Write new version
    vr = await client.post(
        f"{BASELINE_URL}/baselines/{topic}/versions",
        json={"narrative": new_narrative, "citations": []},
    )
    vr.raise_for_status()
    new_version = vr.json()["version_number"]

    # Record delta
    dr = await client.post(
        f"{BASELINE_URL}/baselines/{topic}/deltas",
        json={
            "from_version": old_version,
            "to_version": new_version,
            "delta_summary": delta_summary,
            "claims_added": claims_added,
            "claims_superseded": claims_superseded,
            "article_metadata": article_metadata,
        },
    )
    dr.raise_for_status()
    return new_version


# ─────────────────────────────────────────────────────────────────────────────
# Baseline extraction helpers
# ─────────────────────────────────────────────────────────────────────────────

def extract_updated_baseline(analysis_output: str, old_baseline: str) -> str:
    """
    Pull the updated narrative out of the lead analyst output.
    Looks for a 'Baseline Change Summary' section; falls back to appending
    the executive summary to the old baseline.
    """
    lines = analysis_output.splitlines()

    # Try to find a dedicated updated-baseline section
    for marker in ["## Updated Baseline", "## Baseline Change Summary", "## Baseline Update"]:
        try:
            start = next(i for i, l in enumerate(lines) if marker.lower() in l.lower())
            # Collect until the next ## heading or end of text
            section_lines = []
            for line in lines[start + 1:]:
                if line.startswith("## ") and section_lines:
                    break
                section_lines.append(line)
            section = "\n".join(section_lines).strip()
            if section:
                return section
        except StopIteration:
            continue

    # Fallback: grab the executive summary and prepend to old baseline
    for marker in ["## Executive Summary", "## Primary Assessment"]:
        try:
            start = next(i for i, l in enumerate(lines) if marker.lower() in l.lower())
            section_lines = []
            for line in lines[start + 1:]:
                if line.startswith("## ") and section_lines:
                    break
                section_lines.append(line)
            summary = "\n".join(section_lines).strip()
            if summary:
                if old_baseline:
                    return f"{summary}\n\n[Prior baseline]\n{old_baseline}"
                return summary
        except StopIteration:
            continue

    # Last resort: use the full output (truncated)
    return textwrap.shorten(analysis_output, width=3000, placeholder="\n[truncated]")


def extract_delta_fields(
    analysis_output: str,
) -> tuple[str, list[str], list[str]]:
    """
    Extract delta_summary, claims_added, claims_superseded from the lead analyst output.
    Looks for the 'Baseline Change Summary' / 'Baseline Comparison' appendix section.
    Returns plain-text fallbacks if the structured section isn't found.
    """
    lines = analysis_output.splitlines()

    # Locate baseline comparison section
    start_idx = None
    for marker in ["Baseline Change Summary", "Baseline Comparison", "Appendix: Baseline"]:
        try:
            start_idx = next(i for i, l in enumerate(lines) if marker.lower() in l.lower())
            break
        except StopIteration:
            continue

    section_text = ""
    if start_idx is not None:
        section_lines = []
        for line in lines[start_idx + 1:]:
            if line.startswith("# ") and section_lines:
                break
            section_lines.append(line)
        section_text = "\n".join(section_lines).strip()

    # Build a one-sentence delta summary from the section or the whole output
    summary_source = section_text or analysis_output
    first_para = next(
        (p.strip() for p in summary_source.split("\n\n") if len(p.strip()) > 40),
        "",
    )
    delta_summary = textwrap.shorten(
        first_para or "Baseline updated following new report analysis.",
        width=300,
        placeholder="...",
    )

    # Simple heuristic: bullet lines with "confirmed", "updated", "new" → claims_added
    # bullet lines with "challenged", "superseded", "no longer" → claims_superseded
    claims_added: list[str] = []
    claims_superseded: list[str] = []
    for line in (section_text or analysis_output).splitlines():
        stripped = line.lstrip("•-* ").strip()
        if not stripped or len(stripped) < 20:
            continue
        lower = stripped.lower()
        if any(kw in lower for kw in ("confirmed", "updated", "new signal", "new development", "added")):
            claims_added.append(stripped)
        elif any(kw in lower for kw in ("challenged", "superseded", "no longer", "reversed", "contradicted")):
            claims_superseded.append(stripped)

    return delta_summary, claims_added[:10], claims_superseded[:10]


# ─────────────────────────────────────────────────────────────────────────────
# Pretty print helpers
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
# Main pipeline
# ─────────────────────────────────────────────────────────────────────────────

async def process_report(
    client: httpx.AsyncClient,
    report_text: str,
    topic: str,
    topic_label: str,
    question: str,
    key_questions: str,
) -> tuple[bool, str, int | None]:
    """
    Run the full pipeline for one report.
    Returns (was_relevant, new_baseline_narrative, new_version_number).
    """

    # ── 1. Fetch current baseline ─────────────────────────────────────────────
    section("STEP 1 — Fetching current baseline")
    print(f"  Topic : {topic}")
    baseline_narrative, baseline_version = await fetch_or_seed_baseline(
        client, topic, topic_label
    )
    if baseline_narrative:
        print(f"  Current version : v{baseline_version}")
        print(f"  Narrative preview :")
        wrap(textwrap.shorten(baseline_narrative, width=400, placeholder="..."))
    else:
        print("  No baseline yet — this will be the first version.")

    # ── 2. Relevancy check ────────────────────────────────────────────────────
    section("STEP 2 — Relevancy check")
    print("  Calling relevancy agent...", flush=True)
    relevance = await check_relevance(client, report_text, question, baseline_narrative)

    relevant   = relevance.get("relevant", False)
    confidence = relevance.get("confidence", 0.0)
    reasoning  = relevance.get("reasoning", "")

    print(f"  Relevant  : {'YES' if relevant else 'NO'}")
    print(f"  Confidence: {confidence:.0%}")
    wrap(f"Reasoning: {reasoning}")

    if not relevant or confidence < RELEVANCE_THRESHOLD:
        print()
        print("  ✗ Report is not sufficiently relevant — skipping analysis.")
        return False, baseline_narrative, baseline_version

    # ── 3. Lead analyst ───────────────────────────────────────────────────────
    section("STEP 3 — Lead analyst (dynamic specialist discovery)")
    print("  Dispatching to lead analyst — this may take a few minutes...", flush=True)
    analysis = await run_lead_analyst(client, report_text, baseline_narrative, key_questions)

    print()
    print("  ── Analysis output ──")
    print()
    # Print full output with light indentation
    for line in analysis.splitlines():
        print(f"  {line}")

    # ── 4. Baseline write-back ────────────────────────────────────────────────
    section("STEP 4 — Baseline write-back")
    print("  Extracting updated narrative and delta fields...")

    new_narrative                         = extract_updated_baseline(analysis, baseline_narrative)
    delta_summary, claims_added, claims_superseded = extract_delta_fields(analysis)

    print(f"  Delta summary     : {delta_summary}")
    print(f"  Claims added      : {len(claims_added)}")
    print(f"  Claims superseded : {len(claims_superseded)}")

    print("  Writing new baseline version...", flush=True)
    new_version = await write_baseline(
        client,
        topic,
        old_version=baseline_version,
        new_narrative=new_narrative,
        delta_summary=delta_summary,
        claims_added=claims_added,
        claims_superseded=claims_superseded,
        article_metadata={"source": "demo_report", "title": report_text[:80]},
    )
    print(f"  ✓ Baseline updated: v{baseline_version} → v{new_version}")

    # ── 5. Summary ────────────────────────────────────────────────────────────
    section("PIPELINE COMPLETE")
    print(f"  Topic           : {topic}")
    print(f"  Baseline version: v{new_version}")
    print(f"  Claims added    : {len(claims_added)}")
    if claims_added:
        for c in claims_added:
            wrap(f"+ {c}", indent=4)
    print(f"  Claims superseded: {len(claims_superseded)}")
    if claims_superseded:
        for c in claims_superseded:
            wrap(f"- {c}", indent=4)
    print()

    return True, new_narrative, new_version


async def main() -> None:
    print()
    hr("═")
    print("  MISSION CONTROL — DEMO PIPELINE")
    hr("═")
    print()
    print("  Services:")
    print(f"    Relevancy agent  : {RELEVANCY_URL}")
    print(f"    Lead analyst     : {LEAD_ANALYST_URL}")
    print(f"    Baseline store   : {BASELINE_URL}")
    print()

    # ── Configurable parameters ───────────────────────────────────────────────
    topic       = input(f"  Topic path [{DEFAULT_TOPIC}]: ").strip() or DEFAULT_TOPIC
    topic_label = input(f"  Topic label [{DEFAULT_TOPIC_LABEL}]: ").strip() or DEFAULT_TOPIC_LABEL
    question    = input(f"  Relevancy question\n  [{DEFAULT_QUESTION}]\n  > ").strip() or DEFAULT_QUESTION
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
        current_baseline = ""
        current_version: int | None = None
        first = True

        while True:
            print()
            if first:
                print("  Paste the incoming report text (blank line to finish):")
            else:
                print("  Paste the next report (blank line to finish), or just press Enter to quit:")
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

            was_relevant, current_baseline, current_version = await process_report(
                client,
                report_text=report_text,
                topic=topic,
                topic_label=topic_label,
                question=question,
                key_questions=key_questions,
            )

            cont = input("  Process another report against the updated baseline? [Y/n]: ").strip().lower()
            if cont not in ("", "y", "yes"):
                break

    print()
    hr("═")
    print("  Done.")
    hr("═")
    print()


if __name__ == "__main__":
    asyncio.run(main())
