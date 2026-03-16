"""Executor that bridges A2A requests to the Summarizer LangGraph."""

from __future__ import annotations

from langgraph.graph.state import CompiledStateGraph

from agents.base.executor import LangGraphA2AExecutor
from agents.summarizer.graph import build_summarizer_graph


class SummarizerExecutor(LangGraphA2AExecutor):
    """Runs the summarizer graph: input → OpenAI summary."""

    def build_graph(self) -> CompiledStateGraph:
        return build_summarizer_graph()
