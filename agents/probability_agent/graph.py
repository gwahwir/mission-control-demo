"""Probability-based scenario forecasting agent built with LangGraph.

Takes concatenated specialist analyses as input and performs:
1. Parse assessments — extract structured data from specialist outputs
2. Aggregate probabilities — equal-weighted averaging with tail-risk reserve
3. Detect disagreements — structured divergence analysis
4. Scan periphery — identify uncited intelligence signals
5. Generate briefing — produce actionable probability briefing

Nodes:
    ``receive``                → validates input
    ``parse_assessments``      → extracts structured assessments from concatenated text
    ``aggregate_probabilities`` → equal-weighted probability aggregation
    ``detect_disagreements``   → identifies structured divergence between frameworks
    ``scan_periphery``         → finds signals no framework cited
    ``generate_briefing``      → synthesizes final probability briefing
"""

from __future__ import annotations

import json
import logging
import os
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, TypedDict

import openai
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

PARSE_SYSTEM_PROMPT = """\
You are a structured data extraction specialist. You receive concatenated analyses \
from multiple specialist agents, each applying a distinct analytical framework.

Extract each specialist's assessment into a structured JSON array. For each assessment, extract:
- framework_name: The name of the analytical framework used
- summary: Brief summary of the analysis
- key_findings: List of key findings
- evidence_cited: List of specific evidence/intelligence cited
- scenario_adjustments: List of {scenario_name, direction (increase/decrease/neutral), magnitude (major/moderate/minor), reasoning}
- confidence_level: High/Medium/Low
- predictions: List of predictions made
- watch_variables: Dict of variables being tracked and their assessed status

If a field cannot be extracted, use null or empty list. Preserve the analyst's \
original reasoning — do not editorialize."""

PARSE_OUTPUT_FORMAT = """\
Respond with a JSON array of assessments:
[
  {
    "framework_name": "...",
    "summary": "...",
    "key_findings": ["..."],
    "evidence_cited": ["..."],
    "scenario_adjustments": [
      {
        "scenario_name": "...",
        "direction": "increase|decrease|neutral",
        "magnitude": "major|moderate|minor",
        "reasoning": "..."
      }
    ],
    "confidence_level": "High|Medium|Low",
    "predictions": ["..."],
    "watch_variables": {"variable_name": "status"}
  }
]"""

PERIPHERY_SYSTEM_PROMPT = """\
You are a peripheral intelligence scanner. You identify signals, facts, or \
intelligence present in the raw input that NO analyst framework cited in \
their assessment.

These uncited signals may represent:
- Blind spots across all frameworks
- Emerging patterns too early for established frameworks to catch
- Cross-domain signals that fall between analytical lenses
- Weak signals that individually seem insignificant but collectively matter

For each uncited signal, assess:
- What the signal is
- Why it may have been missed (which analytical blind spot)
- Its potential significance (high/medium/low)
- Which scenario(s) it could affect"""

PERIPHERY_OUTPUT_FORMAT = """\
Respond with JSON:
{
  "uncited_signals": [
    {
      "signal": "description of the uncited intelligence",
      "blind_spot_reason": "why frameworks missed this",
      "significance": "high|medium|low",
      "affected_scenarios": ["scenario names"],
      "recommended_action": "what to do about this signal"
    }
  ],
  "coverage_assessment": "overall assessment of how well the frameworks covered the intelligence",
  "coverage_percentage": 85
}"""

BRIEFING_SYSTEM_PROMPT = """\
You are a probability briefing writer producing actionable intelligence \
for decision-makers. You synthesize parsed assessments, aggregated probabilities, \
detected disagreements, and peripheral signals into a structured briefing.

Core principles:
- Lead with what changed and why
- Quantify uncertainty — use the probability numbers
- Highlight disagreements honestly — do not paper over divergence
- Flag peripheral signals that warrant attention
- Be specific and actionable, not vague or academic"""

BRIEFING_OUTPUT_FORMAT = """\
Respond with JSON:
{
  "title": "Brief descriptive title of this assessment cycle",
  "timestamp": "ISO timestamp",
  "executive_summary": "2-3 sentence overview of key changes",
  "scenario_table": [
    {
      "scenario": "name",
      "previous_probability": 0.0,
      "current_probability": 0.0,
      "change": 0.0,
      "direction": "up|down|stable",
      "key_driver": "primary reason for change"
    }
  ],
  "tail_risk_reserve": 5.0,
  "key_developments": ["top developments driving changes"],
  "disagreements": [
    {
      "scenario": "name",
      "nature": "description of the disagreement",
      "frameworks_involved": ["framework names"],
      "magnitude": 0.0,
      "implication": "what this disagreement means for decision-makers"
    }
  ],
  "peripheral_alerts": [
    {
      "signal": "description",
      "significance": "high|medium|low",
      "recommended_action": "what to do"
    }
  ],
  "watch_variables": {"variable": "current status"},
  "confidence_assessment": "overall confidence in this briefing cycle",
  "next_cycle_priorities": ["what to focus on next"]
}"""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Magnitude scale: maps qualitative magnitude to percentage-point change
MAGNITUDE_SCALE = {
    "major": 8.0,
    "moderate": 4.0,
    "minor": 1.5,
}

# Default tail-risk reserve (percentage points held back for unspecified scenarios)
DEFAULT_TAIL_RISK_RESERVE = 5.0

# Standard deviation threshold for flagging structured disagreement
DISAGREEMENT_THRESHOLD = 5.0


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class ProbabilityState(TypedDict):
    input: str
    assessments: list[dict[str, Any]]
    scenario_probabilities: list[dict[str, Any]]
    disagreements: list[dict[str, Any]]
    periphery: dict[str, Any]
    output: str


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

async def _llm_call(system_prompt: str, user_prompt: str, temperature: float = 0.2) -> str:
    """Make an LLM call using OpenAI-compatible API."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for the probability agent")

    from openai import AsyncOpenAI

    openai_kwargs: dict[str, Any] = {"api_key": api_key}
    base_url = os.getenv("OPENAI_BASE_URL")
    if base_url:
        openai_kwargs["base_url"] = base_url
    client = AsyncOpenAI(**openai_kwargs)

    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            max_completion_tokens=4096,
            response_format={"type": "json_object"},
        )
        return resp.choices[0].message.content or "{}"
    except openai.RateLimitError:
        logger.warning("_llm_call rate limited")
        raise
    except openai.APIError as e:
        logger.error("_llm_call API error: %s", e, exc_info=True)
        raise


def _parse_json_safe(text: str, task_id: str = "") -> Any:
    """Parse JSON from LLM output, handling markdown fences."""
    text = text.strip()
    candidates = [text]
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        candidates.append("\n".join(lines).strip())

    for attempt in candidates:
        try:
            return json.loads(attempt)
        except json.JSONDecodeError:
            continue

    logger.warning("_parse_json_safe could not parse response task_id=%s", task_id)
    return {}


def _build_scenario_adjustments(
    assessments: list[dict[str, Any]],
) -> dict[str, list[tuple[str, float]]]:
    """Build a {scenario_name: [(framework, pp_change), ...]} dict from assessments."""
    adjustments_by_scenario: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for assessment in assessments:
        framework = assessment.get("framework_name", "unknown")
        for adj in assessment.get("scenario_adjustments", []):
            scenario = adj.get("scenario_name", "")
            if not scenario:
                continue
            magnitude = MAGNITUDE_SCALE.get(adj.get("magnitude", "minor"), 1.5)
            direction = adj.get("direction", "neutral")
            if direction == "increase":
                pp_change = magnitude
            elif direction == "decrease":
                pp_change = -magnitude
            else:
                pp_change = 0.0
            adjustments_by_scenario[scenario].append((framework, pp_change))
    return adjustments_by_scenario


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

async def receive(state: ProbabilityState, config: RunnableConfig) -> dict[str, Any]:
    """Validate input and initialize state."""
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    executor.check_cancelled(task_id)
    return {
        "assessments": [],
        "scenario_probabilities": [],
        "disagreements": [],
        "periphery": {},
    }


async def parse_assessments(state: ProbabilityState, config: RunnableConfig) -> dict[str, Any]:
    """Parse concatenated specialist analyses into structured assessments."""
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    executor.check_cancelled(task_id)

    user_prompt = (
        f"# Concatenated Specialist Analyses\n\n"
        f"{state['input']}\n\n"
        f"# Extraction Instructions\n\n{PARSE_OUTPUT_FORMAT}"
    )

    try:
        raw = await _llm_call(PARSE_SYSTEM_PROMPT, user_prompt, temperature=0.1)
    except Exception as e:
        logger.error("parse_assessments LLM call failed task_id=%s: %s", task_id, e)
        return {"assessments": []}

    parsed = _parse_json_safe(raw, task_id)

    # Handle both direct array and wrapped object
    if isinstance(parsed, list):
        assessments = parsed
    elif isinstance(parsed, dict) and "assessments" in parsed:
        assessments = parsed["assessments"]
    else:
        assessments = [parsed] if parsed else []

    return {"assessments": assessments}


async def aggregate_probabilities(state: ProbabilityState, config: RunnableConfig) -> dict[str, Any]:
    """Aggregate probability adjustments using equal weighting with tail-risk reserve."""
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    executor.check_cancelled(task_id)

    assessments = state.get("assessments", [])
    adjustments_by_scenario = _build_scenario_adjustments(assessments)
    all_scenarios = set(adjustments_by_scenario.keys())

    # Calculate equal-weighted average per scenario
    num_scenarios = max(len(all_scenarios), 1)
    base_prob = (100.0 - DEFAULT_TAIL_RISK_RESERVE) / num_scenarios

    scenario_probs = []
    for scenario in sorted(all_scenarios):
        adjustments = adjustments_by_scenario.get(scenario, [])
        if adjustments:
            pp_changes = [change for _, change in adjustments]
            avg_change = statistics.mean(pp_changes)
            contributors = [agent for agent, _ in adjustments]
        else:
            avg_change = 0.0
            contributors = []

        previous = base_prob
        adjusted = previous + avg_change

        scenario_probs.append({
            "scenario_name": scenario,
            "previous_probability": round(previous, 2),
            "adjusted_probability": round(adjusted, 2),
            "change": round(avg_change, 2),
            "contributing_agents": contributors,
        })

    # Renormalize to sum to (100 - tail_risk_reserve)
    target_sum = 100.0 - DEFAULT_TAIL_RISK_RESERVE
    current_sum = sum(sp["adjusted_probability"] for sp in scenario_probs)
    if current_sum > 0:
        scale = target_sum / current_sum
        for sp in scenario_probs:
            sp["adjusted_probability"] = round(sp["adjusted_probability"] * scale, 2)
            sp["change"] = round(sp["adjusted_probability"] - sp["previous_probability"], 2)

    return {"scenario_probabilities": scenario_probs}


async def detect_disagreements(state: ProbabilityState, config: RunnableConfig) -> dict[str, Any]:
    """Detect structured disagreements between frameworks."""
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    executor.check_cancelled(task_id)

    assessments = state.get("assessments", [])
    adjustments_by_scenario = _build_scenario_adjustments(assessments)

    disagreements = []
    for scenario, adjustments in adjustments_by_scenario.items():
        if len(adjustments) < 2:
            continue

        pp_changes = [change for _, change in adjustments]
        std_dev = statistics.stdev(pp_changes)

        if std_dev > DISAGREEMENT_THRESHOLD:
            agent_positions = {
                agent: "increase" if change > 2 else "decrease" if change < -2 else "neutral"
                for agent, change in adjustments
            }
            disagreements.append({
                "scenario_name": scenario,
                "disagreement_magnitude": round(std_dev, 2),
                "agent_positions": agent_positions,
                "explanation": (
                    f"Agents diverged by {std_dev:.1f} percentage points on '{scenario}'. "
                    f"Positions: {json.dumps(agent_positions)}"
                ),
            })

    return {"disagreements": disagreements}


async def scan_periphery(state: ProbabilityState, config: RunnableConfig) -> dict[str, Any]:
    """Scan for intelligence signals not cited by any framework."""
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    executor.check_cancelled(task_id)

    assessments = state.get("assessments", [])

    # Collect all evidence cited across frameworks
    all_cited = []
    for a in assessments:
        all_cited.extend(a.get("evidence_cited", []))

    cited_summary = "\n".join(f"- {c}" for c in all_cited) if all_cited else "(no evidence cited)"

    user_prompt = (
        f"# Original Input\n\n{state['input']}\n\n"
        f"# Evidence Already Cited by Analysts\n\n{cited_summary}\n\n"
        f"# Your Task\n\nIdentify signals in the original input that were NOT cited "
        f"by any analyst.\n\n{PERIPHERY_OUTPUT_FORMAT}"
    )

    try:
        raw = await _llm_call(PERIPHERY_SYSTEM_PROMPT, user_prompt, temperature=0.3)
        periphery = _parse_json_safe(raw, task_id)
    except Exception as e:
        logger.error("scan_periphery LLM call failed task_id=%s: %s", task_id, e)
        periphery = {}

    return {"periphery": periphery}


async def generate_briefing(state: ProbabilityState, config: RunnableConfig) -> dict[str, Any]:
    """Generate the final probability briefing."""
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    executor.check_cancelled(task_id)

    # Build context for the briefing writer
    context_parts = [
        "# Probability Briefing Input",
        "",
        "## Parsed Assessments",
        json.dumps(state.get("assessments", []), indent=2),
        "",
        "## Aggregated Scenario Probabilities",
        json.dumps(state.get("scenario_probabilities", []), indent=2),
        "",
        "## Structured Disagreements",
        json.dumps(state.get("disagreements", []), indent=2),
        "",
        "## Peripheral Intelligence Scan",
        json.dumps(state.get("periphery", {}), indent=2),
        "",
        f"## Tail Risk Reserve: {DEFAULT_TAIL_RISK_RESERVE}%",
        "",
        f"## Timestamp: {datetime.now(timezone.utc).isoformat()}",
        "",
        "# Instructions",
        "",
        BRIEFING_OUTPUT_FORMAT,
    ]

    user_prompt = "\n".join(context_parts)

    try:
        raw = await _llm_call(BRIEFING_SYSTEM_PROMPT, user_prompt, temperature=0.3)
    except Exception as e:
        logger.error("generate_briefing LLM call failed task_id=%s: %s", task_id, e)
        return {"output": json.dumps({"error": str(e)})}

    try:
        briefing = _parse_json_safe(raw, task_id)
        return {"output": json.dumps(briefing, indent=2)}
    except json.JSONDecodeError:
        return {"output": raw}


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_probability_graph() -> StateGraph:
    """Build and compile the probability forecasting graph."""
    graph = StateGraph(ProbabilityState)

    graph.add_node("receive", receive)
    graph.add_node("parse_assessments", parse_assessments)
    graph.add_node("aggregate_probabilities", aggregate_probabilities)
    graph.add_node("detect_disagreements", detect_disagreements)
    graph.add_node("scan_periphery", scan_periphery)
    graph.add_node("generate_briefing", generate_briefing)

    graph.set_entry_point("receive")
    graph.add_edge("receive", "parse_assessments")

    # After parsing, aggregate and scan periphery can run in parallel
    graph.add_edge("parse_assessments", "aggregate_probabilities")
    graph.add_edge("parse_assessments", "detect_disagreements")
    graph.add_edge("parse_assessments", "scan_periphery")

    # All three converge into briefing generation
    graph.add_edge("aggregate_probabilities", "generate_briefing")
    graph.add_edge("detect_disagreements", "generate_briefing")
    graph.add_edge("scan_periphery", "generate_briefing")

    graph.add_edge("generate_briefing", END)

    return graph.compile()
