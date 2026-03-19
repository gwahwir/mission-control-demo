"""Relevancy agent built with LangGraph.

Takes a blob of text and a question, checks with an LLM whether the
text is relevant to the question, and returns a structured JSON result.

Nodes:
1. ``parse_input``       – extract text and question from JSON input
2. ``check_relevancy``   – call LLM to assess relevancy
3. ``format_response``   – parse LLM output into structured JSON
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

logger = logging.getLogger(__name__)


class RelevancyState(TypedDict):
    input: str
    text: str
    question: str
    llm_response: str
    output: str


async def parse_input(state: RelevancyState, config: RunnableConfig) -> dict[str, Any]:
    """Extract text and question from the JSON input."""
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    executor.check_cancelled(task_id)

    try:
        data = json.loads(state["input"])
        return {"text": data.get("text", ""), "question": data.get("question", "")}
    except json.JSONDecodeError:
        return {"text": state["input"], "question": ""}


async def check_relevancy(state: RelevancyState, config: RunnableConfig) -> dict[str, Any]:
    """Call LLM to determine if the text is relevant to the question."""
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    executor.check_cancelled(task_id)

    from openai import AsyncOpenAI

    openai_kwargs: dict[str, Any] = {}
    base_url = os.getenv("OPENAI_BASE_URL")
    api_key = os.getenv("OPENAI_API_KEY")
    if base_url:
        openai_kwargs["base_url"] = base_url
    client = AsyncOpenAI(api_key=api_key, **openai_kwargs)

    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a relevancy assessment tool. Given a piece of text and a question, "
                        "determine whether the text is relevant to answering the question.\n\n"
                        "You MUST respond with ONLY a JSON object in this exact format:\n"
                        '{"relevant": true/false, "confidence": 0.0-1.0, "reasoning": "brief explanation"}\n\n'
                        "Do not include any text outside the JSON object."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Text:\n{state['text']}\n\nQuestion:\n{state['question']}",
                },
            ],
            temperature=0.1,
            max_completion_tokens=2048,
        )
    except openai.APIError as e:
        logger.warning("Relevancy LLM call failed (will retry) task_id=%s: %s", task_id, e)
        raise
    raw = response.choices[0].message.content
    try:
        parsed = json.loads(raw)
        result = {
            "relevant": bool(parsed.get("relevant", False)),
            "confidence": float(parsed.get("confidence", 0.0)),
            "reasoning": str(parsed.get("reasoning", "")),
            "error": False
        }
    except (json.JSONDecodeError, ValueError):
        result = {
            "relevant": False,
            "confidence": 0.0,
            "reasoning": f"Failed to parse LLM response: {raw}",
            "error": True
        }

    return {"output": json.dumps(result, indent=2)}


def build_relevancy_graph() -> StateGraph:
    graph = StateGraph(RelevancyState)
    graph.add_node("parse_input", parse_input)
    graph.add_node("check_relevancy", check_relevancy, retry_policy=RetryPolicy(max_attempts=3, initial_interval=1.0, backoff_factor=2.0))
    graph.set_entry_point("parse_input")
    graph.add_edge("parse_input", "check_relevancy")
    graph.add_edge("check_relevancy", END)
    return graph.compile()
