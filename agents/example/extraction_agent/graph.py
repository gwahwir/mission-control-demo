"""Extraction agent built with LangGraph.

Takes a blob of text and uses an LLM to extract structured information
(entities, events, relationships, metadata) and returns a structured JSON result.

Nodes:
1. ``parse_input``       – extract text from JSON input
2. ``extract_using_llm`` – call LLM to perform structured extraction
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, TypedDict

import openai
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from langgraph.types import RetryPolicy

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

# Module-level client — instantiated once, reused across all requests.
_openai_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _openai_client
    if _openai_client is None:
        kwargs: dict[str, Any] = {"api_key": os.getenv("OPENAI_API_KEY")}
        base_url = os.getenv("OPENAI_BASE_URL")
        if base_url:
            kwargs["base_url"] = base_url
        _openai_client = AsyncOpenAI(**kwargs)
    return _openai_client


class ExtractionState(TypedDict):
    text: str
    input: str
    llm_response: str
    output: str


async def parse_input(state: ExtractionState, config: RunnableConfig) -> dict[str, Any]:
    """Extract text from the JSON input."""
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    executor.check_cancelled(task_id)

    try:
        data = json.loads(state["input"])
        logger.debug("parse_input parsed JSON keys=%s task_id=%s", list(data.keys()), task_id)
        return {
            "text": data.get("text", "")
        }
    except json.JSONDecodeError:
        return {"text": state["input"]}


async def extract_using_llm(state: ExtractionState, config: RunnableConfig) -> dict[str, Any]:
    """Call LLM to extract structured information from the text."""
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    executor.check_cancelled(task_id)

    client = _get_client()
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    logger.debug("extract_using_llm starting task_id=%s model=%s", task_id, model)

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        """
    You are a precise information extraction engine. Given a blob of text (typically a news article or informational
        content), extract all key information into a structured JSON object.

        You MUST respond with ONLY a valid JSON object — no commentary, no markdown fences, no explanation outside the JSON.

        Extract the following fields:

        {
        "title": "Inferred or extracted title/headline of the article",
        "summary": "2-3 sentence summary of the core content",
        "entities": {
            "persons": [
            {"name": "Full Name", "role": "Their role/title if mentioned", "sentiment": "positive|negative|neutral"}
            ],
            "organizations": [
            {"name": "Org Name", "type": "company|government|ngo|media|educational|other"}
            ],
            "locations": [
            {"name": "Place Name", "type": "city|country|region|address|landmark"}
            ],
            "products": [
            {"name": "Product/Service Name", "owner": "Owning entity if known"}
            ]
        },
        "temporal": {
            "publication_date": "YYYY-MM-DD or null if unknown",
            "events": [
            {"description": "What happened", "date": "YYYY-MM-DD or approximate", "is_future": false}
            ]
        },
        "financials": [
            {"amount": "Numeric value", "currency": "USD/EUR/etc", "context": "What the amount refers to"}
        ],
        "topics": ["tag1", "tag2"],
        "categories":
        ["politics|business|technology|science|health|sports|entertainment|environment|legal|conflict|other"],
        "claims": [
            {"statement": "A factual claim made in the text", "attribution": "Who said/claimed it", "verifiable": true}
        ],
        "relationships": [
            {"subject": "Entity A", "predicate": "acquired|partnered_with|sued|appointed|etc", "object": "Entity B"}
        ],
        "metadata": {
            "language": "en",
            "word_count": 0,
            "tone": "formal|informal|urgent|analytical|opinion",
            "confidence": 0.0
        }
        }

        Rules:
        - Omit array fields that have no matches (use empty arrays, not null).
        - Never fabricate information not present or clearly implied in the text.
        - For ambiguous dates, use the most specific format possible ("2026-03" if only month/year is known).
        - Normalize entity names (e.g., "President Biden" and "Joe Biden" → one entry with full name).
        - The confidence field in metadata (0.0–1.0) reflects your overall confidence in extraction accuracy.
        - If financial figures use shorthand (e.g., "$2.5B"), expand to full numeric strings ("2500000000").
        - Extract implicit relationships (e.g., "CEO of Acme Corp" → relationship: person → leads → Acme Corp).
    """
                    ),
                },
                {
                    "role": "user",
                    "content": f"Text:\n{state['text']}",
                },
            ],
            temperature=0.1,
            max_completion_tokens=4096,
            timeout=300,
        )
        raw = response.choices[0].message.content
        logger.debug("extract_using_llm got response task_id=%s", task_id)
        try:
            parsed = json.loads(raw)
            result = parsed
        except (json.JSONDecodeError, ValueError):
            result = {}

        return {"output": json.dumps(result, indent=2)}
    except openai.RateLimitError as e:
        logger.warning("extract_using_llm rate limited task_id=%s: %s", task_id, e)
        return {"output": json.dumps({"error": "Rate limit reached — retry later"})}
    except openai.APIError as e:
        logger.error("extract_using_llm API error task_id=%s: %s", task_id, e, exc_info=True)
        return {"output": json.dumps({"error": str(e)})}
    except Exception as e:
        logger.error("extract_using_llm failed task_id=%s: %s", task_id, e, exc_info=True)
        return {"output": json.dumps({"error": str(e)})}


def build_extraction_graph() -> StateGraph:
    graph = StateGraph(ExtractionState)
    graph.add_node("parse_input", parse_input)
    graph.add_node(
        "extract_using_llm",
        extract_using_llm,
        retry_policy=RetryPolicy(max_attempts=3, initial_interval=1.0, backoff_factor=2.0),
    )
    graph.set_entry_point("parse_input")
    graph.add_edge("parse_input", "extract_using_llm")
    graph.add_edge("extract_using_llm", END)
    return graph.compile()
