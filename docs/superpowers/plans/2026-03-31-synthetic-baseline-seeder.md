# Synthetic Baseline Seeder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a CLI script that takes a seed paragraph, calls OpenAI to generate a complete fictitious baseline hierarchy (topics + versions + deltas), and writes it to the baseline store via async httpx.

**Architecture:** Single-call LLM approach — one OpenAI call returns the full JSON plan (topics ordered parents-first, each with N versions and inline delta fields). The script then sequentially writes each topic, version, and delta to the baseline store REST API.

**Tech Stack:** Python, `openai` (async), `httpx` (async), `argparse`, `asyncio`

---

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `scripts_for_testing/generate_synthetic_baselines.py` | Create | Full script: CLI, prompt builder, LLM call, HTTP writes |
| `tests/test_generate_synthetic_baselines.py` | Create | Unit tests for pure functions (prompt builder, delta body builder) |

---

### Task 1: CLI scaffold and prompt builder

**Files:**
- Create: `scripts_for_testing/generate_synthetic_baselines.py`
- Create: `tests/test_generate_synthetic_baselines.py`

- [ ] **Step 1: Write failing tests for `parse_args` and `build_prompt`**

Create `tests/test_generate_synthetic_baselines.py`:

```python
import sys
import pytest


def test_build_prompt_contains_seed():
    from scripts_for_testing.generate_synthetic_baselines import build_prompt
    seed = "US-China trade war escalates over semiconductor tariffs."
    prompt = build_prompt(seed, n_topics=3, n_versions=2)
    assert seed in prompt
    assert "3" in prompt
    assert "2" in prompt


def test_build_prompt_returns_string():
    from scripts_for_testing.generate_synthetic_baselines import build_prompt
    result = build_prompt("some seed", n_topics=2, n_versions=1)
    assert isinstance(result, str)
    assert len(result) > 100


def test_build_delta_body_first_version():
    from scripts_for_testing.generate_synthetic_baselines import build_delta_body
    version_entry = {
        "delta_summary": "Initial baseline established.",
        "claims_added": ["Claim A"],
        "claims_superseded": [],
    }
    body = build_delta_body(version_entry, from_version=None, to_version=1)
    assert body["from_version"] is None
    assert body["to_version"] == 1
    assert body["delta_summary"] == "Initial baseline established."
    assert body["claims_added"] == ["Claim A"]
    assert body["claims_superseded"] == []
    assert body["article_metadata"] == {}


def test_build_delta_body_subsequent_version():
    from scripts_for_testing.generate_synthetic_baselines import build_delta_body
    version_entry = {
        "delta_summary": "Iran resumed talks.",
        "claims_added": ["New claim"],
        "claims_superseded": ["Old claim"],
    }
    body = build_delta_body(version_entry, from_version=1, to_version=2)
    assert body["from_version"] == 1
    assert body["to_version"] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd C:/Projects/mission-control
pytest tests/test_generate_synthetic_baselines.py -v
```

Expected: `ImportError` — module does not exist yet.

- [ ] **Step 3: Create the script with CLI and pure functions**

Create `scripts_for_testing/generate_synthetic_baselines.py`:

```python
#!/usr/bin/env python
"""Generate synthetic baseline data from a seed paragraph and populate the baseline store.

Usage:
    python scripts_for_testing/generate_synthetic_baselines.py \\
        --seed "Iran nuclear negotiations remain stalled..." \\
        --topics 4 \\
        --versions-per-topic 3

    echo "seed text" | python scripts_for_testing/generate_synthetic_baselines.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

import httpx
from openai import AsyncOpenAI


def build_prompt(seed: str, n_topics: int, n_versions: int) -> str:
    return f"""You are generating fictitious but realistic intelligence baseline data for testing purposes.

Given the seed paragraph below, infer an appropriate topic domain and generate a structured JSON object.

Requirements:
- Create exactly 1 parent topic and {n_topics} leaf topics under it (total {n_topics + 1} topics)
- Topic paths: dot-separated ltree format, lowercase letters and underscores only (e.g. "geo.middle_east.iran")
- Topics array MUST be ordered parents before children
- Each topic must have exactly {n_versions} version(s), ordered chronologically (oldest first)
- Versions must show realistic evolution: each subsequent version updates or adds to the previous
- Each version needs 2-3 citations with plausible but fictitious details
- Citation article_id: "art-001" style; url: "https://example-news.com/article-slug" style
- Narratives: 3-5 present-tense declarative sentences
- delta_summary: 1-2 sentences; use "Initial baseline established." for the first version
- claims_added: 2-4 new factual claims per version
- claims_superseded: empty list for version 1, 0-2 items for later versions

Seed paragraph:
{seed}

Return ONLY a valid JSON object with this exact schema:
{{
  "topics": [
    {{
      "topic_path": "string",
      "display_name": "string",
      "versions": [
        {{
          "narrative": "string",
          "citations": [
            {{
              "article_id": "string",
              "title": "string",
              "url": "string",
              "source": "string",
              "published_at": "ISO 8601 string",
              "excerpt": "string"
            }}
          ],
          "delta_summary": "string",
          "claims_added": ["string"],
          "claims_superseded": ["string"]
        }}
      ]
    }}
  ]
}}"""


def build_delta_body(
    version_entry: dict[str, Any],
    from_version: int | None,
    to_version: int,
) -> dict[str, Any]:
    return {
        "from_version": from_version,
        "to_version": to_version,
        "article_metadata": {},
        "delta_summary": version_entry["delta_summary"],
        "claims_added": version_entry["claims_added"],
        "claims_superseded": version_entry["claims_superseded"],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate synthetic baseline data from a seed paragraph"
    )
    parser.add_argument("--seed", help="Seed paragraph (omit to read from stdin)")
    parser.add_argument("--topics", type=int, default=4, help="Number of leaf topics (default: 4)")
    parser.add_argument(
        "--versions-per-topic",
        type=int,
        default=3,
        dest="versions_per_topic",
        help="Versions per topic (default: 3)",
    )
    parser.add_argument(
        "--baseline-url",
        default="http://localhost:8010",
        help="Baseline store URL (default: http://localhost:8010)",
    )
    parser.add_argument("--model", default="gpt-4o-mini", help="OpenAI model (default: gpt-4o-mini)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print generated plan without writing to the store",
    )
    return parser.parse_args()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_generate_synthetic_baselines.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts_for_testing/generate_synthetic_baselines.py tests/test_generate_synthetic_baselines.py
git commit -m "feat: scaffold synthetic baseline seeder CLI and pure helpers"
```

---

### Task 2: LLM call and plan summary printer

**Files:**
- Modify: `scripts_for_testing/generate_synthetic_baselines.py`

- [ ] **Step 1: Add `generate_plan` and `print_plan_summary` functions**

Append to `scripts_for_testing/generate_synthetic_baselines.py` (before the `if __name__ == "__main__"` block — which doesn't exist yet):

```python
async def generate_plan(seed: str, n_topics: int, n_versions: int, model: str) -> dict[str, Any]:
    client = AsyncOpenAI()
    prompt = build_prompt(seed, n_topics, n_versions)
    response = await client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": prompt}],
    )
    return json.loads(response.choices[0].message.content)


def print_plan_summary(plan: dict[str, Any]) -> None:
    topics = plan["topics"]
    print(f"\nGenerated plan: {len(topics)} topic(s)")
    for t in topics:
        versions = t["versions"]
        print(f"  {t['topic_path']} ({t['display_name']}) — {len(versions)} version(s)")
        for i, v in enumerate(versions, 1):
            snippet = v["narrative"][:80].replace("\n", " ")
            print(f"    v{i}: {snippet}...")
    print()
```

- [ ] **Step 2: Run existing tests to confirm nothing broke**

```bash
pytest tests/test_generate_synthetic_baselines.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add scripts_for_testing/generate_synthetic_baselines.py
git commit -m "feat: add LLM call and plan summary printer to baseline seeder"
```

---

### Task 3: HTTP write sequence

**Files:**
- Modify: `scripts_for_testing/generate_synthetic_baselines.py`

- [ ] **Step 1: Add `write_plan` function**

Append to `scripts_for_testing/generate_synthetic_baselines.py`:

```python
async def write_plan(plan: dict[str, Any], baseline_url: str) -> tuple[int, int]:
    """Write plan to baseline store. Returns (topics_written, versions_written)."""
    base = baseline_url.rstrip("/")
    topics_written = 0
    versions_written = 0

    async with httpx.AsyncClient(timeout=30) as client:
        for topic in plan["topics"]:
            path = topic["topic_path"]
            display = topic["display_name"]

            # Register topic
            r = await client.post(
                f"{base}/topics",
                json={"topic_path": path, "display_name": display},
            )
            if r.status_code == 409:
                print(f"  [skip] Topic already exists: {path}")
            elif r.status_code != 201:
                print(f"  [error] POST /topics {path}: {r.status_code} {r.text}", file=sys.stderr)
                sys.exit(1)
            else:
                print(f"  [ok] Registered topic: {path}")
                topics_written += 1

            # Write versions and deltas in order
            prev_version_number: int | None = None
            for i, v in enumerate(topic["versions"]):
                # POST version
                r = await client.post(
                    f"{base}/baselines/{path}/versions",
                    json={"narrative": v["narrative"], "citations": v["citations"]},
                )
                if r.status_code != 201:
                    print(
                        f"  [error] POST /versions {path} entry {i}: {r.status_code} {r.text}",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                version_number: int = r.json()["version_number"]
                print(f"    [ok] Version {version_number} written for {path}")
                versions_written += 1

                # POST delta
                delta_body = build_delta_body(v, from_version=prev_version_number, to_version=version_number)
                r = await client.post(f"{base}/baselines/{path}/deltas", json=delta_body)
                if r.status_code != 201:
                    print(
                        f"  [error] POST /deltas {path} v{version_number}: {r.status_code} {r.text}",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                print(f"    [ok] Delta written for {path} v{version_number}")
                prev_version_number = version_number

    return topics_written, versions_written
```

- [ ] **Step 2: Run existing tests to confirm nothing broke**

```bash
pytest tests/test_generate_synthetic_baselines.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add scripts_for_testing/generate_synthetic_baselines.py
git commit -m "feat: add HTTP write sequence to baseline seeder"
```

---

### Task 4: Wire `main()` and smoke test

**Files:**
- Modify: `scripts_for_testing/generate_synthetic_baselines.py`

- [ ] **Step 1: Append `main()` and entry point**

Append to the end of `scripts_for_testing/generate_synthetic_baselines.py`:

```python
async def main() -> None:
    args = parse_args()

    seed = args.seed
    if not seed:
        if sys.stdin.isatty():
            print("Error: provide --seed or pipe text via stdin", file=sys.stderr)
            sys.exit(1)
        seed = sys.stdin.read().strip()
    if not seed:
        print("Error: seed paragraph is empty", file=sys.stderr)
        sys.exit(1)

    print(f"Generating plan from seed ({len(seed)} chars) using {args.model}...")
    plan = await generate_plan(seed, args.topics, args.versions_per_topic, args.model)
    print_plan_summary(plan)

    if args.dry_run:
        print("[dry-run] Skipping writes.")
        return

    print(f"Writing to {args.baseline_url}...")
    topics_written, versions_written = await write_plan(plan, args.baseline_url)
    print(f"\nDone. {topics_written} topic(s) registered, {versions_written} version(s) written.")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Run all tests**

```bash
pytest tests/test_generate_synthetic_baselines.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 3: Smoke test with --dry-run (requires OPENAI_API_KEY)**

```bash
cd C:/Projects/mission-control
python scripts_for_testing/generate_synthetic_baselines.py \
  --seed "Iran nuclear negotiations remain stalled as enrichment levels approach 60% purity at Fordow." \
  --topics 3 \
  --versions-per-topic 2 \
  --dry-run
```

Expected output:
```
Generating plan from seed (87 chars) using gpt-4o-mini...

Generated plan: 4 topic(s)
  geo.middle_east (Middle East) — 2 version(s)
    v1: ...
    v2: ...
  geo.middle_east.iran (Iran) — 2 version(s)
  ...

[dry-run] Skipping writes.
```

- [ ] **Step 4: Commit**

```bash
git add scripts_for_testing/generate_synthetic_baselines.py
git commit -m "feat: add main() entry point to synthetic baseline seeder"
```
