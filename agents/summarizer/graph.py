"""Summarizer agent built with LangGraph.

Receives text input and uses OpenAI to produce a concise summary.
Designed to be called by upstream agents (e.g. echo-agent) that
forward their output here via A2A.

Nodes:
1. ``summarize``  – send input to OpenAI for summarization
2. ``respond``    – format the final summary
"""

from __future__ import annotations

import os
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph


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
    if base_url:
        openai_kwargs["base_url"] = base_url
    openai_client = AsyncOpenAI(**openai_kwargs)  # uses OPENAI_API_KEY env var

    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

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
        max_tokens=256,
    )

    summary = response.choices[0].message.content or ""
    return {"summary": summary}


async def respond(state: SummarizerState, config: RunnableConfig) -> dict[str, Any]:
    """Format the final output."""
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    executor.check_cancelled(task_id)
    return {"output": state["summary"]}


def build_summarizer_graph() -> StateGraph:
    graph = StateGraph(SummarizerState)
    graph.add_node("summarize", summarize)
    graph.add_node("respond", respond)
    graph.set_entry_point("summarize")
    graph.add_edge("summarize", "respond")
    graph.add_edge("respond", END)
    return graph.compile()
