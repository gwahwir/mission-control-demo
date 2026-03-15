"""Summarizer agent built with LangGraph.

Demonstrates agent-to-agent composition: the graph calls a peer agent
(e.g. echo-agent) via the A2A protocol, then feeds that agent's output
into an OpenAI LLM call for summarization.

Nodes:
1. ``fetch_upstream``  – call peer agent via A2A, store its output
2. ``summarize``       – send upstream output to OpenAI for summarization
3. ``respond``         – format the final summary
"""

from __future__ import annotations

import os
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph

from control_plane.a2a_client import A2AClient


class SummarizerState(TypedDict):
    input: str
    upstream_agent_url: str
    upstream_output: str
    summary: str
    output: str


async def fetch_upstream(state: SummarizerState, config: RunnableConfig) -> dict[str, Any]:
    """Call the upstream agent via A2A and capture its output."""
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    executor.check_cancelled(task_id)

    agent_url = state.get("upstream_agent_url", "")
    if not agent_url:
        # No upstream agent configured — use the raw input directly
        return {"upstream_output": state["input"]}

    client = A2AClient(agent_url)
    try:
        result = await client.send_message(state["input"])
        # Extract the text from the A2A response
        status = result.get("status", {})
        msg = status.get("message", {})
        parts = msg.get("parts", [])
        text = parts[0].get("text", "") if parts else str(result)
        return {"upstream_output": text}
    finally:
        await client.close()


async def summarize(state: SummarizerState, config: RunnableConfig) -> dict[str, Any]:
    """Send the upstream agent's output to OpenAI for summarization."""
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    executor.check_cancelled(task_id)

    from openai import AsyncOpenAI

    openai_client = AsyncOpenAI()  # uses OPENAI_API_KEY env var

    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    upstream_text = state["upstream_output"]

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
            {"role": "user", "content": upstream_text},
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
    graph.add_node("fetch_upstream", fetch_upstream)
    graph.add_node("summarize", summarize)
    graph.add_node("respond", respond)
    graph.set_entry_point("fetch_upstream")
    graph.add_edge("fetch_upstream", "summarize")
    graph.add_edge("summarize", "respond")
    graph.add_edge("respond", END)
    return graph.compile()
