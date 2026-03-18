"""Generic parameterized LLM graph for specialist agents.

Each specialist gets its own compiled graph with system_prompt, model,
temperature, and max_completion_tokens captured via closures.
"""

from __future__ import annotations

import os
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph


class SpecialistState(TypedDict):
    input: str
    response: str
    output: str


def build_specialist_graph(
    system_prompt: str,
    model: str | None = None,
    temperature: float = 0.3,
    max_completion_tokens: int = 1024,
    output_format: str | None = None,
    name: str = "Specialized_Agent_Generic",
) -> StateGraph:
    """Return a compiled LangGraph for a specialist with the given LLM params."""

    resolved_model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    async def process(state: SpecialistState, config: RunnableConfig) -> dict[str, Any]:
        """Call the LLM with the specialist's system prompt."""
        executor = config["configurable"]["executor"]
        task_id = config["configurable"]["task_id"]
        context_id = config["configurable"].get("context_id")
        executor.check_cancelled(task_id)

        #from openai import AsyncOpenAI
        from langfuse.openai import AsyncOpenAI

        openai_kwargs: dict[str, Any] = {}
        base_url = os.getenv("OPENAI_BASE_URL")
        api_key = os.getenv("OPENAI_API_KEY")
        if base_url:
            openai_kwargs["base_url"] = base_url
        if api_key:
            openai_kwargs["api_key"] = api_key
        client = AsyncOpenAI(**openai_kwargs)

        user_content = state["input"]
        if output_format:
            user_content = f"{user_content}\n\n## Output Format\n{output_format}"

        resp = await client.chat.completions.create(
            model=resolved_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=temperature,
            max_completion_tokens=max_completion_tokens,
            trace_id=task_id.replace("-", "")[0:32] if task_id else None,
            parent_observation_id=context_id.replace("-", "")[0:16] if context_id else None,
            name=name
        )

        return {"response": resp.choices[0].message.content or ""}

    async def respond(state: SpecialistState, config: RunnableConfig) -> dict[str, Any]:
        """Copy response to output."""
        executor = config["configurable"]["executor"]
        task_id = config["configurable"]["task_id"]
        executor.check_cancelled(task_id)
        return {"output": state["response"]}

    graph = StateGraph(SpecialistState)
    graph.add_node("process", process)
    graph.add_node("respond", respond)
    graph.set_entry_point("process")
    graph.add_edge("process", "respond")
    graph.add_edge("respond", END)
    return graph.compile()
