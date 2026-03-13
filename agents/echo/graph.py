"""A simple echo agent built with LangGraph.

This is a proof-of-concept agent that demonstrates the A2A + LangGraph
integration pattern.  It has three nodes:

1. ``receive`` – reads and validates input
2. ``process`` – transforms the message (uppercase echo)
3. ``respond`` – formats the final output

Each node calls ``check_cancelled()`` to support clean mid-run stops.
"""

from __future__ import annotations

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


def respond(state: EchoState, config: RunnableConfig) -> dict[str, Any]:
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    executor.check_cancelled(task_id)
    return {"output": state["processed"]}


def build_echo_graph() -> StateGraph:
    graph = StateGraph(EchoState)
    graph.add_node("receive", receive)
    graph.add_node("process", process)
    graph.add_node("respond", respond)
    graph.set_entry_point("receive")
    graph.add_edge("receive", "process")
    graph.add_edge("process", "respond")
    graph.add_edge("respond", END)
    return graph.compile()
