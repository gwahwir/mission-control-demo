#!/usr/bin/env python
"""Iteratively ingest all .txt files from sample_articles/ into the Knowledge Graph agent.

Usage:
    python ingest_articles.py [--agent-url http://localhost:8008] [--dir sample_articles]
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from control_plane.a2a_client import A2AClient, A2AError


async def ingest_file(client: A2AClient, path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    print(f"  Sending {path.name} ({len(text)} chars)...")
    result = await client.send_message(text)
    return result


def extract_output(result: dict) -> str:
    """Pull the text content out of an A2A result."""
    # result may be a Task object; walk parts to find text
    try:
        parts = (
            result.get("status", {})
            .get("message", {})
            .get("parts", [])
        )
        for part in parts:
            if part.get("kind") == "text":
                return part["text"]
    except (AttributeError, KeyError):
        pass
    return json.dumps(result, indent=2)


async def main():
    parser = argparse.ArgumentParser(description="Ingest articles into the Knowledge Graph agent")
    parser.add_argument("--agent-url", default="http://localhost:8008", help="Knowledge Graph agent URL")
    parser.add_argument("--dir", default="sample_articles", help="Directory containing .txt files")
    args = parser.parse_args()

    articles_dir = Path(args.dir)
    if not articles_dir.exists():
        print(f"Error: directory '{articles_dir}' not found.", file=sys.stderr)
        sys.exit(1)

    txt_files = sorted(articles_dir.glob("*.txt"))
    if not txt_files:
        print(f"No .txt files found in '{articles_dir}'.", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(txt_files)} file(s) in '{articles_dir}'")
    print(f"Targeting agent at {args.agent_url}")
    print()

    client = A2AClient(args.agent_url, timeout=600)
    success = 0
    failed = 0

    try:
        for i, path in enumerate(txt_files, 1):
            print(f"[{i}/{len(txt_files)}] {path.name}")
            try:
                result = await ingest_file(client, path)
                output = extract_output(result)
                print(f"  OK: {output[:200]}{'...' if len(output) > 200 else ''}")
                success += 1
            except A2AError as e:
                print(f"  ERROR (A2A): {e}", file=sys.stderr)
                failed += 1
            except Exception as e:
                print(f"  ERROR: {e}", file=sys.stderr)
                failed += 1
            print()
    finally:
        await client.close()

    print(f"Done. {success} succeeded, {failed} failed.")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
