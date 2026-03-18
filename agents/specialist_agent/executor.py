"""Executor that bridges A2A requests to a parameterized specialist LangGraph."""

from __future__ import annotations

from langgraph.graph.state import CompiledStateGraph

from agents.base.executor import LangGraphA2AExecutor
from agents.specialist_agent.config import SpecialistConfig
from agents.specialist_agent.graph import build_specialist_graph


class SpecialistExecutor(LangGraphA2AExecutor):
    """Runs a specialist graph parameterized by a YAML config."""

    def __init__(self, config: SpecialistConfig) -> None:
        super().__init__()
        self._config = config

    def build_graph(self) -> CompiledStateGraph:
        return build_specialist_graph(
            system_prompt=self._config.system_prompt,
            model=self._config.model,
            temperature=self._config.temperature,
            max_completion_tokens=self._config.max_completion_tokens,
            output_format=self._config.output_format,
            name=self._config.name
        )
