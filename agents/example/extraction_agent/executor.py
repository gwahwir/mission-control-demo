"""Executor that bridges A2A requests to the Extraction LangGraph."""

from __future__ import annotations

from langgraph.graph.state import CompiledStateGraph

from agents.base.executor import LangGraphA2AExecutor
from agents.extraction_agent.graph import build_extraction_graph


class ExtractionExecutor(LangGraphA2AExecutor):
    """Runs the extraction graph: parse input → LLM extraction → JSON output."""

    def build_graph(self) -> CompiledStateGraph:
        return build_extraction_graph()
