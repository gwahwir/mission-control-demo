"""Markdown file I/O helpers for the wiki agent.

All functions are synchronous (meant to be called via asyncio.to_thread).
"""
from __future__ import annotations

import os
from pathlib import Path


def get_wiki_dir() -> Path:
    wiki_dir = os.getenv("WIKI_DIR", "")
    if not wiki_dir:
        raise ValueError("WIKI_DIR environment variable is not set")
    return Path(wiki_dir)


def topic_path_to_file_path(topic_path: str) -> Path:
    """Convert a dotted topic path to a filesystem path under WIKI_DIR.

    Examples:
        "wiki.geo.iran"                      → {WIKI_DIR}/geo/iran.md
        "wiki.sources.2026-04-14-iran"       → {WIKI_DIR}/sources/2026-04-14-iran.md
        "wiki.actors.irgc"                   → {WIKI_DIR}/actors/irgc.md
        "wiki.queries.2026-04-14-what-is"    → {WIKI_DIR}/queries/2026-04-14-what-is.md
    """
    wiki_dir = get_wiki_dir()
    # Strip leading "wiki." prefix
    parts = topic_path.split(".")
    if parts and parts[0] == "wiki":
        parts = parts[1:]
    if not parts:
        raise ValueError(f"Cannot derive file path from topic_path: {topic_path!r}")
    # Last segment becomes the filename, rest become directories
    *dirs, name = parts
    rel = Path(*dirs, name + ".md") if dirs else Path(name + ".md")
    return wiki_dir / rel


def write_wiki_file(path: Path, content: str) -> None:
    """Write content to a wiki file, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def read_wiki_file(path: Path) -> str:
    """Read a wiki file; returns empty string if it does not exist."""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def get_wiki_index_path() -> Path:
    return get_wiki_dir() / "index.md"


def get_wiki_log_path() -> Path:
    return get_wiki_dir() / "log.md"


def list_all_wiki_pages(wiki_dir: Path) -> list[str]:
    """Return all .md file paths relative to wiki_dir (as strings)."""
    if not wiki_dir.exists():
        return []
    return [str(p.relative_to(wiki_dir)) for p in sorted(wiki_dir.rglob("*.md"))]


def append_to_file(path: Path, text: str) -> None:
    """Append text to a file, creating it if it does not exist."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(text)
