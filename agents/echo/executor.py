"""A2A executor for the Echo agent."""

from __future__ import annotations

from langgraph.graph.state import CompiledStateGraph

from agents.base import LangGraphA2AExecutor
from agents.echo.graph import build_echo_graph


class EchoAgentExecutor(LangGraphA2AExecutor):
    """Wraps the echo LangGraph in an A2A-compatible executor."""

    def build_graph(self) -> CompiledStateGraph:
        return build_echo_graph()
