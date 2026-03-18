"""A2A executor for the Probability Forecasting agent."""

from __future__ import annotations

from langgraph.graph.state import CompiledStateGraph

from agents.base import LangGraphA2AExecutor
from agents.probability_agent.graph import build_probability_graph


class ProbabilityExecutor(LangGraphA2AExecutor):
    """Wraps the probability forecasting LangGraph in an A2A-compatible executor."""

    def build_graph(self) -> CompiledStateGraph:
        return build_probability_graph()
