"""A simple echo agent built with LangGraph.

This is a proof-of-concept agent that demonstrates the A2A + LangGraph
integration pattern.  It has four nodes:

1. ``receive``              – reads and validates input
2. ``process``              – transforms the message (uppercase echo)
3. ``forward_downstream``   – optionally forwards output to a downstream agent via A2A
4. ``respond``              – formats the final output

Each node calls ``check_cancelled()`` to support clean mid-run stops.

Set ``DOWNSTREAM_AGENT_URL`` to have the echo agent forward its output
to another agent (e.g. the summarizer) after processing.
"""

from __future__ import annotations

import os
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph


class EchoState(TypedDict):
    input: str
    processed: str
    output: str


def receive(state: EchoState, config: RunnableConfig) -> dict[str, Any]:
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    executor.check_cancelled(task_id)
    return {"processed": state["input"]}


def process(state: EchoState, config: RunnableConfig) -> dict[str, Any]:
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    executor.check_cancelled(task_id)
    return {"processed": f"ECHO: {state['processed'].upper()}"}


async def forward_downstream(state: EchoState, config: RunnableConfig) -> dict[str, Any]:
    """Forward the processed output to a downstream agent via A2A."""
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    executor.check_cancelled(task_id)

    context_id = config["configurable"].get("context_id")

    downstream_url = os.getenv("DOWNSTREAM_AGENT_URL", "")
    if not downstream_url:
        return {}

    from control_plane.a2a_client import A2AClient

    client = A2AClient(downstream_url)
    try:
        result = await client.send_message(state["processed"], context_id=context_id)
        status = result.get("status", {})
        msg = status.get("message", {})
        parts = msg.get("parts", [])
        text = parts[0].get("text", "") if parts else state["processed"]
        return {"processed": text}
    finally:
        await client.close()


def respond(state: EchoState, config: RunnableConfig) -> dict[str, Any]:
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    executor.check_cancelled(task_id)
    return {"output": state["processed"]}


def build_echo_graph() -> StateGraph:
    graph = StateGraph(EchoState)
    graph.add_node("receive", receive)
    graph.add_node("process", process)
    graph.add_node("forward_downstream", forward_downstream)
    graph.add_node("respond", respond)
    graph.set_entry_point("receive")
    graph.add_edge("receive", "process")
    graph.add_edge("process", "forward_downstream")
    graph.add_edge("forward_downstream", "respond")
    graph.add_edge("respond", END)
    return graph.compile()
