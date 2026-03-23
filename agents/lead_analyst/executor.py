"""A2A executor for the Lead Analyst agent."""

from __future__ import annotations

from typing import Any

from a2a.server.agent_execution import RequestContext
from langgraph.graph.state import CompiledStateGraph

from agents.base import LangGraphA2AExecutor
from agents.lead_analyst.config import LeadAnalystConfig, SubAgentConfig
from agents.lead_analyst.graph import build_lead_analyst_graph


class LeadAnalystExecutor(LangGraphA2AExecutor):
    """Wraps the lead analyst LangGraph in an A2A-compatible executor."""

    def __init__(self, config: LeadAnalystConfig) -> None:
        super().__init__()
        self._config = config

    @property
    def sub_agents(self) -> list[SubAgentConfig]:
        return self._config.sub_agents

    def prepare_input(self, context: RequestContext) -> dict[str, Any]:
        """Extract structured input from A2A message metadata."""
        user_text = context.get_user_input() or ""

        # Extract from metadata if available
        baselines = ""
        key_questions = ""
        if context.message and context.message.metadata:
            baselines = context.message.metadata.get("baselines", "")
            key_questions = context.message.metadata.get("keyQuestions", "")

        return {
            "input": user_text,
            "baselines": baselines,
            "key_questions": key_questions,
        }

    def build_graph(self) -> CompiledStateGraph:
        return build_lead_analyst_graph(
            sub_agents=self._config.sub_agents,
            aggregation_prompt=self._config.aggregation_prompt,
            model=self._config.model,
            temperature=self._config.temperature,
            max_completion_tokens=self._config.max_completion_tokens,
            name=self._config.name,
            dynamic_discovery=self._config.dynamic_discovery,
            control_plane_url=self._config.control_plane_url,
            min_specialists=self._config.min_specialists,
        )
