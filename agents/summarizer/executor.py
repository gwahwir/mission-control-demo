"""Executor that bridges A2A requests to the Summarizer LangGraph."""

from __future__ import annotations

import os
from typing import Any

from a2a.server.agent_execution import RequestContext
from langgraph.graph.state import CompiledStateGraph

from agents.base.executor import LangGraphA2AExecutor
from agents.summarizer.graph import build_summarizer_graph


class SummarizerExecutor(LangGraphA2AExecutor):
    """Runs the summarizer graph: fetch upstream agent → OpenAI summary."""

    def build_graph(self) -> CompiledStateGraph:
        return build_summarizer_graph()

    def prepare_input(self, context: RequestContext) -> dict[str, Any]:
        """Build graph input with the upstream agent URL from env config."""
        user_text = context.get_user_input() or ""
        return {
            "input": user_text,
            "upstream_agent_url": os.getenv("UPSTREAM_AGENT_URL", ""),
        }
