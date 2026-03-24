#!/usr/bin/env python
"""Scrape Google News RSS for articles and save full text to sample_articles/.

Usage:
    python scrape_articles.py [--query "US Iran conflict"] [--max 50]
"""

import argparse
import requests
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote_plus

import feedparser
from newspaper import Article


def make_slug(title: str, max_len: int = 60) -> str:
    slug = title.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    slug = slug[:max_len].rstrip("-")
    return slug or "article"


def fetch_article_text(url: str, timeout: int = 10) -> str:
    url = f"https://r.jina.ai/{url}"
    headers = {
        "Authorization": "Bearer jina_24a37f5baf5c44afbd0993b51d6ae73cLxRlA0O2KEAh8JqTpldvz2jMl0r6"
    }
    response = requests.get(url, headers=headers, timeout=45)
    return response.text


def main() -> int:
    parser = argparse.ArgumentParser(description="Scrape Google News articles to txt files")
    parser.add_argument("--query", default="US Iran conflict", help="Search query")
    parser.add_argument("--max", type=int, default=500, dest="max_entries",
                        help="Max feed entries to attempt (default: 50)")
    args = parser.parse_args()

    rss_url = (
        f"https://news.google.com/rss/search"
        f"?q={quote_plus(args.query)}&hl=en-US&gl=US&ceid=US:en"
    )

    print(f"Query : {args.query}")
    print(f"Feed  : {rss_url}")
    print()

    feed = feedparser.parse(rss_url)
    entries = feed.entries[: args.max_entries]

    if not entries:
        print("No entries returned from feed.", file=sys.stderr)
        return 1

    print(f"Feed returned {len(feed.entries)} entries; attempting first {len(entries)}.")
    print()

    output_dir = Path("sample_articles")
    output_dir.mkdir(exist_ok=True)

    saved = 0
    skipped = 0

    for i, entry in enumerate(entries, 1):
        title = entry.get("title", f"article-{i}")
        url = entry.get("link", "")
        source_obj = entry.get("source", {})
        source = source_obj.get("title", "") if isinstance(source_obj, dict) else ""
        published = entry.get("published", "")

        slug = make_slug(title)
        filename = f"{i:03d}_{slug}.txt"
        filepath = output_dir / filename

        print(f"[{i:02d}/{len(entries)}] {title[:80]}")

        # RSS summary fallback (strip HTML tags)
        rss_summary = re.sub(r"<[^>]+>", "", entry.get("summary", "")).strip()

        text = ""
        source_label = "full-text"
        try:
            text = fetch_article_text(url)
        except Exception as exc:
            print(f"       Fetch failed ({exc}), trying RSS summary...")

        if not text.strip():
            if rss_summary:
                text = rss_summary
                source_label = "rss-summary"
            else:
                print(f"       Skipped — no text available")
                skipped += 1
                continue

        content = (
            f"Title: {title}\n"
            f"Source: {source}\n"
            f"Published: {published}\n"
            f"URL: {url}\n"
            f"---\n"
            f"{text}\n"
        )
        filepath.write_text(content, encoding="utf-8")
        print(f"       Saved → {filename} ({len(text):,} chars, {source_label})")
        saved += 1

        if i < len(entries):
            time.sleep(0.5)

    print()
    print(f"Done. {saved} saved, {skipped} skipped.")
    return 0 if saved > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
