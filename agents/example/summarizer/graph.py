"""Summarizer agent built with LangGraph.

Receives text input and uses OpenAI to produce a concise summary.
Designed to be called by upstream agents (e.g. echo-agent) that
forward their output here via A2A.

Nodes:
1. ``summarize``  – send input to OpenAI for summarization
2. ``respond``    – format the final summary
"""

from __future__ import annotations

import logging
import os
from typing import Any, TypedDict

import openai
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from langgraph.types import RetryPolicy

logger = logging.getLogger(__name__)


class SummarizerState(TypedDict):
    input: str
    summary: str
    output: str


async def summarize(state: SummarizerState, config: RunnableConfig) -> dict[str, Any]:
    """Send input to OpenAI for summarization."""
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    executor.check_cancelled(task_id)

    from openai import AsyncOpenAI

    openai_kwargs: dict[str, Any] = {}
    base_url = os.getenv("OPENAI_BASE_URL")
    api_key = os.getenv("OPENAI_API_KEY")
    if base_url:
        openai_kwargs["base_url"] = base_url
    if api_key:
        openai_kwargs["api_key"] = api_key
    openai_client = AsyncOpenAI(**openai_kwargs)

    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    try:
        response = await openai_client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a concise summarizer. Summarize the following text "
                        "in 2-3 sentences. Preserve key facts and intent."
                    ),
                },
                {"role": "user", "content": state["input"]},
            ],
            temperature=0.3,
            max_completion_tokens=256,
        )
        content = response.choices[0].message.content
        if content is None:
            logger.warning(
                "Summarizer received None content from LLM (possible content filter) task_id=%s",
                task_id,
            )
            return {"summary": "[No content returned by LLM]"}
        return {"summary": content}
    except openai.RateLimitError as e:
        logger.warning("Summarizer rate limited task_id=%s: %s", task_id, e)
        return {"summary": "[Rate limit reached — retry later]"}
    except openai.APIError as e:
        logger.error("Summarizer OpenAI API error task_id=%s: %s", task_id, e, exc_info=True)
        return {"summary": "[LLM unavailable]"}


async def respond(state: SummarizerState, config: RunnableConfig) -> dict[str, Any]:
    """Format the final output."""
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    executor.check_cancelled(task_id)
    return {"output": state["summary"]}


def build_summarizer_graph() -> StateGraph:
    graph = StateGraph(SummarizerState)
    graph.add_node(
        "summarize",
        summarize,
        retry_policy=RetryPolicy(max_attempts=3, initial_interval=1.0, backoff_factor=2.0),
    )
    graph.add_node("respond", respond)
    graph.set_entry_point("summarize")
    graph.add_edge("summarize", "respond")
    graph.add_edge("respond", END)
    return graph.compile()
