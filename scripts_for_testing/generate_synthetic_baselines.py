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
