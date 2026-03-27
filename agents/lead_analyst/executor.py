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
        import json

        user_input = context.get_user_input() or ""

        # Try to parse user input as JSON (dashboard sends structured data)
        text = ""
        baselines = ""
        key_questions = ""

        try:
            data = json.loads(user_input)
            if isinstance(data, dict):
                text = data.get("text", "")
                baselines = data.get("baselines", "")
                key_questions = data.get("key_questions", "")
            else:
                text = user_input
        except (json.JSONDecodeError, ValueError):
            # Not JSON - treat as plain text
            text = user_input

        # Also check metadata (fallback or additional source)
        if context.message and context.message.metadata:
            if not baselines:
                baselines = context.message.metadata.get("baselines", "")
            if not key_questions:
                key_questions = context.message.metadata.get("keyQuestions", "")

        return {
            "input": text,
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
