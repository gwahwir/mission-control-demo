"""A2A executor for the Lead Analyst agent."""

from __future__ import annotations

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

    def build_graph(self) -> CompiledStateGraph:
        return build_lead_analyst_graph(
            sub_agents=self._config.sub_agents,
            aggregation_prompt=self._config.aggregation_prompt,
            model=self._config.model,
            temperature=self._config.temperature,
            max_completion_tokens=self._config.max_completion_tokens,
        )
