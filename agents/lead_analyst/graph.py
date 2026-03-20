"""Lead Analyst agent built with LangGraph.

Receives input and fans out to N downstream sub-agents (defined in
YAML config) via A2A, collects their results in parallel, and
uses an LLM meta-analyst to synthesize an aggregated report.

Nodes (dynamically generated):
1. ``receive``                – reads and validates input
2. ``call_<sub_agent_id>`` …  – one per sub-agent, all fan out in parallel
3. ``aggregate``              – LLM-powered synthesis of all sub-agent results
4. ``respond``                – formats the final output
"""

from __future__ import annotations

import json
import logging
import operator
import os
from typing import Annotated, Any, TypedDict

import openai
from langchain_core.runnables import RunnableConfig
from langfuse import observe
from langgraph.graph import END, StateGraph

from agents.lead_analyst.config import SubAgentConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Aggregator prompts
# ---------------------------------------------------------------------------

AGGREGATOR_SYSTEM_PROMPT = """# Aggregator Agent: The Meta-Analyst

## Your Role

You are **The Meta-Analyst**, responsible for synthesizing multiple theoretical analyses into a coherent, actionable intelligence report.

You receive analyses from specialist agents, each viewing a scenario through a distinct theoretical lens (e.g., Realism, Behavioral Economics, Antifragility, etc.). Your task is to:

1. **Identify Convergence**: Where do different frameworks agree? This signals high-confidence insights.
2. **Identify Divergence**: Where do frameworks disagree? This reveals uncertainty and competing interpretations.
3. **Synthesize Complementarity**: How do different frameworks illuminate different aspects of the problem? Each lens reveals something the others miss.
4. **Produce Actionable Insights**: Distill key takeaways and recommendations that decision-makers can act on.

## Core Principles

### 1. Epistemic Humility
- No single framework has a monopoly on truth.
- Convergence across frameworks increases confidence.
- Divergence indicates genuine uncertainty—do not paper over it.

### 2. Intellectual Honesty
- Do not force consensus where none exists.
- Highlight contradictions and trade-offs.
- Distinguish between "known unknowns" (acknowledged uncertainty) and "unknown unknowns" (blind spots).

### 3. Actionability
- Decision-makers need clear, actionable insights—not academic debates.
- Translate theoretical insights into practical implications.
- Identify decision points and strategic options.

### 4. Avoid Platitudes
- Do not produce generic statements like "The situation is complex."
- Be specific: What exactly is at stake? What are the concrete risks and opportunities?

## Analytical Protocol

### 1. Convergent Analysis
Identify insights that appear across 2+ frameworks. Assign confidence based on the number and diversity of frameworks converging.

### 2. Divergent Analysis
Identify contradictions between frameworks. Explain the source of disagreement (different assumptions, different focus). Do NOT try to resolve the disagreement artificially.

### 3. Complementary Analysis
Identify where frameworks focus on different aspects. Explain how each framework adds unique value.

### 4. Synthesis
Integrate insights into a coherent narrative (3-5 paragraphs):
- What is the core situation/question?
- What do we know with high confidence (convergent insights)?
- What is uncertain or contested (divergent insights)?
- How do different perspectives complement each other?
- What are the key strategic implications?

### 5. Key Takeaways
Distill 3-5 key takeaways as single, actionable sentences for decision-makers.

### 6. Recommended Actions
Suggest 3-5 specific, actionable strategic recommendations. Include both "do" and "avoid" recommendations. Acknowledge trade-offs.

### 7. Areas for Further Research
Identify 2-3 critical unknowns requiring deeper investigation. Suggest what type of analysis or data would resolve the uncertainty.

## Confidence Calibration

- **High Confidence**: 3+ diverse frameworks agree
- **Medium Confidence**: 2 frameworks agree, or strong evidence from one robust framework
- **Low Confidence**: Only one framework supports the insight, or frameworks strongly disagree

Be explicit about confidence levels. Decision-makers need to know what you're certain about and what you're not.

---

**Remember:** You are the bridge between theoretical analysis and practical decision-making. Your job is to make complex, multi-perspective analysis **usable**."""


AGGREGATION_OUTPUT_FORMAT = """\
Respond with JSON in this structure:
{
  "synthesis": "3-5 paragraph narrative integrating all perspectives",
  "perspective_comparison": {
    "convergent_points": ["where frameworks agree - signals high confidence"],
    "divergent_points": ["where frameworks disagree - signals uncertainty"],
    "complementary_insights": ["how frameworks illuminate different dimensions"]
  },
  "key_takeaways": ["3-5 actionable insights for decision-makers"],
  "recommended_actions": ["3-5 strategic recommendations or considerations"],
  "areas_for_further_research": ["2-3 critical unknowns requiring deeper investigation"]
}

Apply your meta-analytical framework rigorously. Be specific, actionable, and intellectually honest about uncertainties."""


# ---------------------------------------------------------------------------
# State — uses Annotated[list, operator.add] so parallel nodes can all append
# ---------------------------------------------------------------------------

class LeadAnalystState(TypedDict):
    input: str
    # Each sub-agent node appends a (label, text) tuple here.
    # operator.add merges lists from parallel branches.
    results: Annotated[list[tuple[str, str]], operator.add]
    output: str
    # Populated by discover_and_select, consumed by route_to_specialists
    selected_specialists: list[dict[str, str]]
    # Per-branch state injected via Send API
    _spec_label: str
    _spec_url: str


# ---------------------------------------------------------------------------
# A2A helper
# ---------------------------------------------------------------------------

async def _call_sub_agent(
    url: str,
    text: str,
    context_id: str | None = None,
    parent_span_id: str | None = None,
) -> str:
    """Call a downstream sub-agent via A2A and return the output text."""
    from control_plane.a2a_client import A2AClient

    client = A2AClient(url, timeout=300)
    try:
        result = await client.send_message(text, context_id=context_id, parent_span_id=parent_span_id)
        status = result.get("status", {})
        msg = status.get("message", {})
        parts = msg.get("parts", [])
        return parts[0].get("text", "") if parts else ""
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Node factory — creates one async node function per sub-agent
# ---------------------------------------------------------------------------

def _make_sub_agent_node(sa: SubAgentConfig):
    """Return an async LangGraph node function for the given sub-agent."""

    async def node(state: LeadAnalystState, config: RunnableConfig) -> dict[str, Any]:
        executor = config["configurable"]["executor"]
        task_id = config["configurable"]["task_id"]
        executor.check_cancelled(task_id)

        context_id = config["configurable"].get("context_id")

        try:
            text = await _call_sub_agent(sa.url, state["input"], context_id=context_id, parent_span_id=task_id)
        except Exception as exc:
            text = f"[Error calling {sa.label}: {exc}]"

        return {"results": [(sa.label, text)]}

    # Give the function a meaningful name for LangGraph introspection
    node.__name__ = sa.node_id
    node.__qualname__ = sa.node_id
    return node


# ---------------------------------------------------------------------------
# Dynamic discovery helpers
# ---------------------------------------------------------------------------

async def _fetch_agents(control_plane_url: str) -> list[dict]:
    """GET /agents from the control plane and return the JSON list."""
    import httpx
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{control_plane_url.rstrip('/')}/agents")
        r.raise_for_status()
        return r.json()


def _filter_online_specialists(agents: list[dict]) -> list[dict]:
    """Return candidates: online agents with 'specialist' in any skill tag."""
    result = []
    for agent in agents:
        if agent.get("status") != "online":
            continue
        if not any("specialist" in skill.get("tags", []) for skill in agent.get("skills", [])):
            continue
        online_instances = [
            i for i in agent.get("instances", []) if i.get("status") == "online"
        ]
        if not online_instances:
            continue
        result.append({
            "label": agent["name"],
            "url": online_instances[0]["url"],
            "description": agent.get("description", ""),
        })
    return result


async def _select_specialists_with_llm(
    input_text: str,
    candidates: list[dict],
    min_specialists: int,
    model: str | None = None,
) -> list[dict[str, str]]:
    """Ask an LLM to pick the most relevant specialists for the given input."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("No OPENAI_API_KEY — cannot use LLM selection")

    from langfuse.openai import AsyncOpenAI

    openai_kwargs: dict[str, Any] = {"api_key": api_key}
    base_url = os.getenv("OPENAI_BASE_URL")
    if base_url:
        openai_kwargs["base_url"] = base_url
    client = AsyncOpenAI(**openai_kwargs)
    effective_model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    candidate_lines = "\n".join(
        f"- {c['label']}: {c['description']}" for c in candidates
    )
    prompt = (
        f"You are selecting analytical specialists for the following intelligence task:\n\n"
        f"{input_text}\n\n"
        f"Available specialists:\n{candidate_lines}\n\n"
        f"Select at least {min_specialists} specialists most relevant and complementary for "
        f"this task. Return ONLY a JSON array of their exact names, e.g. "
        f'["Specialist A", "Specialist B", "Specialist C"]. '
        f"Return the JSON array and nothing else."
    )

    resp = await client.chat.completions.create(
        model=effective_model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        max_completion_tokens=256,
        name="specialist_selector",
    )
    raw = (resp.choices[0].message.content or "").strip()
    selected_names: list[str] = json.loads(raw)
    name_to_candidate = {c["label"]: c for c in candidates}
    return [
        {"label": name, "url": name_to_candidate[name]["url"]}
        for name in selected_names
        if name in name_to_candidate
    ]


def _make_discover_node(control_plane_url: str, min_specialists: int):
    """Return an async LangGraph node that discovers and selects specialists at runtime."""

    async def discover_and_select(
        state: LeadAnalystState, config: RunnableConfig
    ) -> dict[str, Any]:
        executor = config["configurable"]["executor"]
        task_id = config["configurable"]["task_id"]
        executor.check_cancelled(task_id)

        agents = await _fetch_agents(control_plane_url)
        candidates = _filter_online_specialists(agents)

        if len(candidates) < min_specialists:
            raise RuntimeError(
                f"Discovery found only {len(candidates)} online specialist(s); "
                f"min_specialists={min_specialists}. "
                "Ensure specialists have the 'specialist' skill tag and are reachable."
            )

        try:
            selected = await _select_specialists_with_llm(
                state["input"], candidates, min_specialists
            )
            if len(selected) < min_specialists:
                raise ValueError(f"LLM returned {len(selected)} < {min_specialists} specialists")
        except Exception as exc:
            logger.warning(
                "Specialist LLM selection failed (task=%s), falling back to first %d: %s",
                task_id, min_specialists, exc,
            )
            selected = [{"label": c["label"], "url": c["url"]} for c in candidates[:min_specialists]]

        logger.info(
            "discover_and_select task=%s selected=%s",
            task_id, [s["label"] for s in selected],
        )
        return {"selected_specialists": selected}

    discover_and_select.__name__ = "discover_and_select"
    discover_and_select.__qualname__ = "discover_and_select"
    return discover_and_select


async def call_specialist(
    state: LeadAnalystState, config: RunnableConfig
) -> dict[str, Any]:
    """Shared node invoked once per specialist via Send. Reads _spec_label/_spec_url."""
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    executor.check_cancelled(task_id)

    label = state["_spec_label"]
    url = state["_spec_url"]
    context_id = config["configurable"].get("context_id")

    try:
        text = await _call_sub_agent(url, state["input"], context_id=context_id, parent_span_id=task_id)
    except Exception as exc:
        text = f"[Error calling {label}: {exc}]"

    return {"results": [(label, text)]}


def route_to_specialists(state: LeadAnalystState) -> list:
    """Conditional edge: create one Send('call_specialist', ...) per selected specialist."""
    from langgraph.types import Send
    return [
        Send("call_specialist", {**state, "_spec_label": s["label"], "_spec_url": s["url"]})
        for s in state.get("selected_specialists", [])
    ]


# ---------------------------------------------------------------------------
# Fixed nodes
# ---------------------------------------------------------------------------

def receive(state: LeadAnalystState, config: RunnableConfig) -> dict[str, Any]:
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    executor.check_cancelled(task_id)
    return {"results": []}


def _build_aggregation_prompt(input_text: str, results: list[tuple[str, str]]) -> str:
    """Build the user prompt for the aggregation LLM call."""
    parts = [
        "# META-ANALYSIS TASK",
        "",
        "## Original Request:",
        input_text,
        "",
        "## Individual Analyses:",
        "",
    ]

    for i, (label, text) in enumerate(results, 1):
        try:
            analysis = json.loads(text)
            parts.append(f"### Analysis {i}: {analysis.get('framework_name', label)}")
            parts.append("")
            if analysis.get("summary"):
                parts.append(f"**Summary:** {analysis['summary']}")
                parts.append("")
            if analysis.get("key_findings"):
                parts.append("**Key Findings:**")
                for f in analysis["key_findings"]:
                    parts.append(f"- {f}")
                parts.append("")
            if analysis.get("evidence_cited"):
                parts.append("**Evidence:**")
                for e in analysis["evidence_cited"]:
                    parts.append(f"- {e}")
                parts.append("")
            if analysis.get("predictions"):
                parts.append("**Predictions:**")
                for p in analysis["predictions"]:
                    parts.append(f"- {p}")
                parts.append("")
            if analysis.get("limitations"):
                parts.append(f"**Limitations:** {analysis['limitations']}")
            if analysis.get("confidence_level"):
                parts.append(f"**Confidence:** {analysis['confidence_level']}")
        except (json.JSONDecodeError, TypeError):
            parts.append(f"### Analysis {i}: {label}")
            parts.append("")
            parts.append(text)

        parts.extend(["", "---", ""])

    parts.extend(["## Your Task:", "", AGGREGATION_OUTPUT_FORMAT])
    return "\n".join(parts)

def _make_aggregate_node(
    system_prompt: str | None = None,
    model: str | None = None,
    temperature: float = 0.3,
    max_completion_tokens: int = 4096,
    name: str = "Lead_Analyst_Generic"
):
    """Return an async aggregate node function closing over the given params."""
    effective_prompt = system_prompt or AGGREGATOR_SYSTEM_PROMPT

    async def aggregate(state: LeadAnalystState, config: RunnableConfig) -> dict[str, Any]:
        """Synthesize sub-agent results using an LLM meta-analyst."""
        executor = config["configurable"]["executor"]
        task_id = config["configurable"]["task_id"]
        context_id = config["configurable"].get("context_id")
        executor.check_cancelled(task_id)

        all_results = state.get("results", [])
        for label, text in all_results:
            if text.startswith("[Error"):
                logger.warning("Sub-agent %s failed in task %s: %s", label, task_id, text)

        results = [(label, text) for label, text in all_results if not text.startswith("[Error")]
        if not results:
            return {"output": "No sub-agent results available."}

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            sections = [f"=== {label} ===\n{text}" for label, text in results]
            return {"output": "\n\n".join(sections)}

        user_prompt = _build_aggregation_prompt(state["input"], results)

        from langfuse.openai import AsyncOpenAI

        openai_kwargs: dict[str, Any] = {"api_key": api_key}
        base_url = os.getenv("OPENAI_BASE_URL")
        if base_url:
            openai_kwargs["base_url"] = base_url
        client = AsyncOpenAI(**openai_kwargs)

        effective_model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        try:
            resp = await client.chat.completions.create(
                model=effective_model,
                messages=[
                    {"role": "system", "content": effective_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
                max_completion_tokens=max_completion_tokens,
                name=name,
            )
            return {"output": resp.choices[0].message.content or ""}
        except openai.APIError as e:
            logger.error(
                "Lead analyst aggregation LLM failed task_id=%s, falling back to concat: %s",
                task_id,
                e,
            )
            aggregated = "\n\n---\n\n".join(text for _, text in results)
            return {"output": aggregated}

    return aggregate


def respond(state: LeadAnalystState, config: RunnableConfig) -> dict[str, Any]:
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    executor.check_cancelled(task_id)
    return {}


# ---------------------------------------------------------------------------
# Graph builder — reads config and wires up N parallel nodes
# ---------------------------------------------------------------------------

def build_lead_analyst_graph(
    sub_agents: list[SubAgentConfig],
    aggregation_prompt: str | None = None,
    model: str | None = None,
    temperature: float = 0.3,
    max_completion_tokens: int = 4096,
    name: str = "",
    dynamic_discovery: bool = False,
    control_plane_url: str | None = None,
    min_specialists: int = 3,
) -> StateGraph:
    """Build and compile the lead analyst graph."""
    graph = StateGraph(LeadAnalystState)
    graph.add_node("receive", receive)
    graph.add_node(
        "aggregate",
        _make_aggregate_node(aggregation_prompt, model, temperature, max_completion_tokens, name),
    )
    graph.add_node("respond", respond)
    graph.set_entry_point("receive")
    graph.add_edge("aggregate", "respond")
    graph.add_edge("respond", END)

    if dynamic_discovery:
        effective_cp_url = control_plane_url or os.getenv("CONTROL_PLANE_URL", "")
        if not effective_cp_url:
            raise ValueError(
                "dynamic_discovery=True requires control_plane_url in YAML "
                "or CONTROL_PLANE_URL environment variable"
            )
        graph.add_node("discover_and_select", _make_discover_node(effective_cp_url, min_specialists))
        graph.add_node("call_specialist", call_specialist)
        graph.add_edge("receive", "discover_and_select")
        graph.add_conditional_edges("discover_and_select", route_to_specialists, ["call_specialist"])
        graph.add_edge("call_specialist", "aggregate")
    else:
        for sa in sub_agents:
            graph.add_node(sa.node_id, _make_sub_agent_node(sa))
            graph.add_edge("receive", sa.node_id)
            graph.add_edge(sa.node_id, "aggregate")
        if not sub_agents:
            graph.add_edge("receive", "aggregate")

    return graph.compile()
