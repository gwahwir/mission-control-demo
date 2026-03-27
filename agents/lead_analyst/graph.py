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

### 5. Baseline Evaluation (when provided)
- If baseline assessments are provided, your primary task is to evaluate **changes**.
- Identify where specialist analyses **confirm**, **challenge**, or **update** the baselines.
- Be explicit about what has changed, what remains stable, and what is now uncertain.
- Do not simply restate baselines—focus on **delta analysis** (what's new or different).

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
    baselines: str  # Current baseline assessments (used for specialist selection and aggregation)
    key_questions: str  # Specific analytical questions (sent to specialists)
    # Each sub-agent node appends a (label, text) tuple here.
    # operator.add merges lists from parallel branches.
    results: Annotated[list[tuple[str, str]], operator.add]
    output: str
    # Populated by discover_and_select, consumed by route_to_specialists
    selected_specialists: list[dict[str, str]]
    # Maps specialist label → reason it was selected; included in aggregation prompt
    selection_reasoning: dict[str, str]
    # Per-branch state injected via Send API
    _spec_label: str
    _spec_url: str
    # Sequential analysis fields
    peripheral_findings: str  # Output from peripheral_scan specialist
    aggregated_consensus: str  # Initial aggregation (domain + peripheral, before ACH)
    ach_analysis: str  # Output from ach_red_team specialist
    baseline_comparison: str  # Output from baseline_comparison specialist


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

        # Build sub-agent input as JSON so specialist agents receive key_questions as a named field
        sub_agent_input = json.dumps({
            "text": state["input"],
            "key_questions": state.get("key_questions", ""),
        })

        lf_span = None
        parent_span_id: str | None = None
        if os.getenv("LANGFUSE_PUBLIC_KEY"):
            from langfuse import Langfuse
            from langfuse.types import TraceContext
            lf_span = Langfuse().start_observation(
                trace_context=TraceContext(trace_id=context_id.replace("-", "") if context_id else ""),
                name="call_sub_agent",
                input={"agent": sa.label},
            )
            parent_span_id = lf_span.id

        try:
            text = await _call_sub_agent(sa.url, sub_agent_input, context_id=context_id, parent_span_id=parent_span_id)
        except Exception as exc:
            text = f"[Error calling {sa.label}: {exc}]"
        finally:
            if lf_span:
                lf_span.end()

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
    """Return candidates: online domain specialists only (excludes meta-specialists).

    Meta-specialists (peripheral_scan, ach_red_team) are tagged with specialist_L2 or specialist_L3
    and are called sequentially in the graph, not selected by LLM.
    """
    META_SPECIALIST_TAGS = {"specialist_L2", "specialist_L3"}
    result = []
    for agent in agents:
        if agent.get("status") != "online":
            continue

        # Check if agent has specialist skill AND is not a meta-specialist
        has_specialist = False
        is_meta_specialist = False

        for skill in agent.get("skills", []):
            tags = set(skill.get("tags", []))
            if "specialist" in tags:
                has_specialist = True
            if tags & META_SPECIALIST_TAGS:  # Intersection with meta tags
                is_meta_specialist = True
                break

        if not has_specialist or is_meta_specialist:
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


def _normalize_specialist_name(name: str) -> str:
    """Normalize specialist name for matching: convert various dashes/hyphens to standard hyphen."""
    import unicodedata
    # Normalize unicode (e.g., en-dash, em-dash → hyphen)
    normalized = unicodedata.normalize('NFKC', name)
    # Replace various dash characters with standard hyphen-minus
    dash_chars = ['\u2010', '\u2011', '\u2012', '\u2013', '\u2014', '\u2015']  # various dashes
    for dash in dash_chars:
        normalized = normalized.replace(dash, '-')
    return normalized.strip()


def _validate_llm_selection(
    raw: str,
    name_to_candidate: dict[str, dict],
    min_specialists: int,
) -> list[dict]:
    """Parse and validate LLM selection output.

    Raises ``ValueError`` with a descriptive message if the response:
    - is not valid JSON
    - is not a JSON array
    - contains items missing ``name`` or ``reasoning``
    - references unknown specialist names
    - returns fewer than ``min_specialists`` valid items
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Not valid JSON: {exc}") from exc

    if not isinstance(parsed, list):
        raise ValueError(f"Expected a JSON array, got {type(parsed).__name__}")

    # Build normalized lookup: normalized_name -> original_name
    normalized_lookup = {_normalize_specialist_name(k): k for k in name_to_candidate.keys()}

    valid: list[dict] = []
    for i, item in enumerate(parsed):
        if not isinstance(item, dict):
            raise ValueError(f"Item {i} is not an object: {item!r}")
        name = item.get("name", "").strip()
        reasoning = item.get("reasoning", "").strip()
        if not name:
            raise ValueError(f"Item {i} is missing a non-empty 'name'")
        if not reasoning:
            raise ValueError(f"Item {i} ({name!r}) is missing a non-empty 'reasoning'")

        # Try normalized matching first
        normalized_name = _normalize_specialist_name(name)
        if normalized_name in normalized_lookup:
            # Replace with canonical name
            item["name"] = normalized_lookup[normalized_name]
            valid.append(item)
        elif name in name_to_candidate:
            # Exact match (shouldn't happen if normalization worked, but safe fallback)
            valid.append(item)
        else:
            raise ValueError(f"Item {i} references unknown specialist {name!r} (normalized: {normalized_name!r})")

    if len(valid) < min_specialists:
        raise ValueError(
            f"Only {len(valid)} valid specialist(s) returned; need at least {min_specialists}"
        )

    return valid


async def _select_specialists_with_llm(
    input_text: str,
    baselines: str,
    candidates: list[dict],
    min_specialists: int,
    model: str | None = None,
    max_retries: int = 3,
) -> list[dict[str, str]]:
    """Ask an LLM to pick the most relevant specialists for the given input.

    Retries up to ``max_retries`` times if the response is not a valid JSON
    array or any item is missing ``reasoning``.  On each retry the bad
    response and the validation error are fed back to the model so it can
    self-correct.
    """
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

    name_to_candidate = {c["label"]: c for c in candidates}
    candidate_lines = "\n".join(
        f"- {c['label']}: {c['description']}" for c in candidates
    )
    system_prompt = (
        f"You are selecting analytical specialists for the following intelligence task:\n\n"
        f"{input_text}\n\n"
    )

    # Include baselines if provided to inform specialist selection
    if baselines:
        system_prompt += (
            f"## Current Baseline Assessments:\n"
            f"{baselines}\n\n"
            f"Select specialists who can best evaluate changes, challenges, or updates to these baselines.\n\n"
        )

    system_prompt += (
        f"Available specialists:\n{candidate_lines}\n\n"
        f"Select at least {min_specialists} and at most {len(candidates)} specialists most relevant and complementary for "
        f"this task. For each selected specialist, provide a concise reason (1-2 sentences) "
        f"explaining why they are suited to this specific task.\n\n"
        f"Return ONLY a JSON array of objects with 'name' and 'reasoning' fields, e.g. "
        f'[{{"name": "Specialist A", "reasoning": "Chosen because..."}}]. '
        f"Return the JSON array and nothing else."
    )

    messages: list[dict[str, str]] = [{"role": "user", "content": system_prompt}]
    last_exc: Exception = RuntimeError("No attempts made")

    for attempt in range(max_retries):
        resp = await client.chat.completions.create(
            model=effective_model,
            messages=messages,
            temperature=0.0,
            max_completion_tokens=1024,
            name="select_specialist"
        )
        raw = (resp.choices[0].message.content or "").strip()

        try:
            valid_items = _validate_llm_selection(raw, name_to_candidate, min_specialists)
        except ValueError as exc:
            last_exc = exc
            logger.warning(
                "LLM selection attempt %d/%d failed validation: %s",
                attempt + 1, max_retries, exc,
            )
            if attempt < max_retries - 1:
                # Feed the bad response + error back so the model can self-correct
                messages.append({"role": "assistant", "content": raw})
                messages.append({
                    "role": "user",
                    "content": (
                        f"Your previous response was invalid: {exc}. "
                        "Please correct it and return only the JSON array with "
                        "'name' and 'reasoning' fields for each specialist."
                    ),
                })
            continue

        return [
            {
                "label": item["name"],
                "url": name_to_candidate[item["name"]]["url"],
                "reasoning": item["reasoning"],
            }
            for item in valid_items
        ]

    raise ValueError(
        f"LLM selection failed after {max_retries} attempt(s): {last_exc}"
    )


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
                state["input"],
                state.get("baselines", ""),
                candidates,
                min_specialists
            )
            if len(selected) < min_specialists:
                raise ValueError(f"LLM returned {len(selected)} < {min_specialists} specialists")
        except Exception as exc:
            logger.warning(
                "Specialist LLM selection failed (task=%s), falling back to first %d: %s",
                task_id, min_specialists, exc,
            )
            selected = [{"label": c["label"], "url": c["url"], "reasoning": ""} for c in candidates[:min_specialists]]

        logger.info(
            "discover_and_select task=%s selected=%s",
            task_id, [s["label"] for s in selected],
        )
        selection_reasoning = {s["label"]: s.get("reasoning", "") for s in selected}
        return {"selected_specialists": selected, "selection_reasoning": selection_reasoning}

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

    # Build specialist input as JSON so specialist agents receive key_questions as a named field
    specialist_input = json.dumps({
        "text": state["input"],
        "key_questions": state.get("key_questions", ""),
    })

    lf_span = None
    parent_span_id: str | None = None
    if os.getenv("LANGFUSE_PUBLIC_KEY"):
        from langfuse import Langfuse
        from langfuse.types import TraceContext
        lf_span = Langfuse().start_observation(
            trace_context=TraceContext(trace_id=context_id.replace("-", "") if context_id else ""),
            name="call_specialist",
            input={"specialist": label},
        )
        parent_span_id = lf_span.id

    try:
        text = await _call_sub_agent(url, specialist_input, context_id=context_id, parent_span_id=parent_span_id)
    except Exception as exc:
        text = f"[Error calling {label}: {exc}]"
    finally:
        if lf_span:
            lf_span.end()

    return {"results": [(label, text)]}


def route_to_specialists(state: LeadAnalystState) -> list:
    """Conditional edge: create one Send('call_specialist', ...) per selected specialist."""
    from langgraph.types import Send
    return [
        Send("call_specialist", {**state, "_spec_label": s["label"], "_spec_url": s["url"]})
        for s in state.get("selected_specialists", [])
    ]



async def call_peripheral_scan(
    state: LeadAnalystState, config: RunnableConfig
) -> dict[str, Any]:
    """Call peripheral scanner before aggregation to catch weak signals early.

    Peripheral scan identifies weak signals, blind spots, and uncited intelligence
    that domain specialists may have missed. Runs BEFORE aggregation so findings
    can be integrated into the consensus rather than added post-hoc.
    """
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    context_id = config["configurable"].get("context_id")
    executor.check_cancelled(task_id)

    # Build input: raw document + key questions + domain specialist summaries
    domain_results = state.get("results", [])
    specialist_summaries = "\n\n".join([
        f"**{label}**: {text[:200]}..."
        for label, text in domain_results
        if not text.startswith("[Error")
    ])

    text_content = f"""
{state["input"]}

---
## KEY QUESTIONS (from user):
{state.get("key_questions", "None provided")}

---
## DOMAIN SPECIALIST ANALYSES (for reference - identify what they missed):

{specialist_summaries}

---
## YOUR TASK:
Apply peripheral scan methodology to identify what domain specialists missed:
1. Uncited intelligence that NO domain specialist referenced (especially relevant to key questions)
2. Weak signals and anomalies (prioritize those addressing key questions)
3. Cross-domain connections
4. Framework blind spots preventing key questions from being fully addressed
5. Any other significant gaps (even if not directly related to key questions)

Your findings will be integrated into the aggregated consensus, so focus on HIGH-SIGNAL insights that would materially change the analysis.
"""

    # Wrap as JSON with separate fields (consistent with call_specialist pattern)
    peripheral_input = json.dumps({
        "text": text_content,
        "key_questions": state.get("key_questions", ""),
    })

    # TODO: Make specialist agent URL configurable via env var or discovery
    specialist_agent_url = os.getenv("SPECIALIST_AGENT_URL", "http://specialist-agent:8006")
    peripheral_scan_url = f"{specialist_agent_url}/peripheral-scan"

    try:
        peripheral_output = await _call_sub_agent(
            peripheral_scan_url,
            peripheral_input,
            context_id=context_id,
        )
    except Exception as exc:
        peripheral_output = f"[Error calling peripheral_scan: {exc}]"
        logger.warning("Peripheral scan failed in task %s: %s", task_id, exc)

    return {"peripheral_findings": peripheral_output}


async def call_ach_red_team(
    state: LeadAnalystState, config: RunnableConfig
) -> dict[str, Any]:
    """Call ACH red team to challenge aggregated consensus.

    ACH (Analysis of Competing Hypotheses) generates alternative hypotheses
    and identifies disconfirming evidence for the consensus assessment.
    """
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    executor.check_cancelled(task_id)

    context_id = config["configurable"].get("context_id")

    text_content = f"""
## AGGREGATED CONSENSUS TO CHALLENGE:

{state["aggregated_consensus"]}

---
## KEY QUESTIONS (from user):
{state.get("key_questions", "None provided")}

---
## PERIPHERAL SCAN FINDINGS (weak signals that may support alternatives):

{state.get("peripheral_findings", "None identified")}

---
## YOUR TASK:

Apply ACH (Analysis of Competing Hypotheses) methodology to challenge the consensus above:

1. **Identify the Consensus Hypothesis (H1)** regarding the key questions above
2. **Generate 3-4 Alternative Hypotheses (H2, H3, H4)** that answer the key questions differently
3. **Identify Disconfirming Evidence**: What evidence contradicts H1?
4. **Evaluate Peripheral Signals**: Do weak signals support any alternative hypotheses?
5. **Challenge the Questions Themselves**: Are the key questions framing the problem correctly, or should decision-makers be asking different questions?
6. **Pre-Mortem Analysis**: If consensus is wrong, what did we miss?

Be adversarial. Your job is to find flaws in both the consensus AND the framing of the questions.
"""

    # Wrap as JSON with separate fields (consistent with call_specialist pattern)
    ach_input = json.dumps({
        "text": text_content,
        "key_questions": state.get("key_questions", ""),
    })

    # TODO: Make specialist agent URL configurable
    specialist_agent_url = os.getenv("SPECIALIST_AGENT_URL", "http://specialist-agent:8006")
    ach_red_team_url = f"{specialist_agent_url}/ach-red-team"

    try:
        ach_output = await _call_sub_agent(
            ach_red_team_url,
            ach_input,
            context_id=context_id,
        )
    except Exception as exc:
        ach_output = f"[Error calling ach_red_team: {exc}]"
        logger.warning("ACH red team failed in task %s: %s", task_id, exc)

    return {"ach_analysis": ach_output}


async def call_baseline_comparison(
    state: LeadAnalystState, config: RunnableConfig
) -> dict[str, Any]:
    """Compare aggregated consensus against baselines to detect changes.

    Only runs if baselines were provided. Compares aggregated_consensus
    with baselines to identify confirmations, challenges, updates.
    """
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    context_id = config["configurable"].get("context_id")
    executor.check_cancelled(task_id)

    baselines = state.get("baselines", "").strip()
    if not baselines:
        # No baselines provided - skip comparison
        return {"baseline_comparison": ""}

    # Build comparison input as structured text with all context sections
    text_content = f"""## BASELINE ASSESSMENTS:

{baselines}

---
## NEW ANALYSIS (Aggregated Consensus):

{state["aggregated_consensus"]}

---
## ACH RED TEAM CHALLENGES (for confidence calibration):

{state.get("ach_analysis", "ACH analysis not available")}

**Context:** The ACH Red Team has challenged the aggregated consensus by identifying alternative hypotheses, disconfirming evidence, and blind spots in the consensus view.

**Use ACH Insights to Calibrate Confidence:**

When comparing baseline against new analysis, assess confidence levels based on ACH challenges:

1. **High Confidence Changes:**
   - Confirmed: Consensus supports baseline AND ACH doesn't challenge this point
   - Challenged: Consensus contradicts baseline AND ACH agrees baseline was wrong
   - Updated: Consensus refines baseline AND ACH supports the refinement

2. **Uncertain/Tentative Changes:**
   - Confirmed (Tentative): Consensus supports baseline BUT ACH raises doubts about consensus
   - Challenged (Uncertain): Consensus contradicts baseline BUT ACH defends baseline assumptions
   - Updated (Uncertain): Consensus refines baseline BUT ACH questions the refinement

3. **Meta-Insights:**
   - If ACH suggests BOTH baseline and consensus miss something fundamental, note this
   - If ACH's alternative hypotheses invalidate the baseline-consensus comparison framing, flag it

---
## YOUR TASK:

Compare the new analysis against the baseline assessments. Use ACH insights to calibrate confidence in your change assessment. Identify what has been confirmed, challenged, updated, or what remains stable, with confidence qualifications based on ACH challenges.

Provide structured JSON output as specified, with confidence indicators where ACH creates uncertainty.
"""

    # Wrap as JSON with separate fields (consistent with call_specialist pattern)
    comparison_input = json.dumps({
        "text": text_content,
        "baselines": baselines,
        "key_questions": state.get("key_questions", ""),
    })

    specialist_agent_url = os.getenv("SPECIALIST_AGENT_URL", "http://specialist-agent:8006")
    baseline_comparison_url = f"{specialist_agent_url}/baseline-comparison"

    try:
        comparison_output = await _call_sub_agent(
            baseline_comparison_url,
            comparison_input,
            context_id=context_id,
        )
    except Exception as exc:
        comparison_output = f"[Error calling baseline_comparison: {exc}]"
        logger.warning("Baseline comparison failed in task %s: %s", task_id, exc)

    return {"baseline_comparison": comparison_output}


async def final_synthesis(
    state: LeadAnalystState, config: RunnableConfig
) -> dict[str, Any]:
    """Integrate ACH red team challenges into final output.

    Produces a balanced assessment that presents both the consensus view
    and credible alternative hypotheses identified by ACH red team.
    """
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    executor.check_cancelled(task_id)

    # If no OpenAI key, just concatenate consensus + ACH + baseline comparison
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        parts = [state["aggregated_consensus"]]
        if state.get("ach_analysis"):
            ach_formatted = _format_specialist_output(state["ach_analysis"], "ACH Red Team Challenge")
            parts.append(f"\n\n---\n\n## APPENDIX A: ACH RED TEAM CHALLENGE\n\n{ach_formatted}")
        if state.get("baseline_comparison") and not state["baseline_comparison"].startswith("[Error"):
            baseline_formatted = _format_specialist_output(state["baseline_comparison"], "Baseline Change Analysis")
            parts.append(f"\n\n---\n\n## APPENDIX B: BASELINE CHANGE ANALYSIS\n\n{baseline_formatted}")
        return {"output": "".join(parts)}

    # Use LLM to integrate ACH challenges + baseline comparison into balanced assessment
    # Format ACH for readability in the prompt
    ach_formatted_for_llm = _format_specialist_output(state["ach_analysis"], "ACH Red Team Challenge")

    synthesis_prompt = f"""
You are producing a final intelligence assessment that integrates red team challenges.

## CONSENSUS ANALYSIS:
{state["aggregated_consensus"]}

## ACH RED TEAM CHALLENGE:
{ach_formatted_for_llm}
"""

    # Add baseline comparison section if available (formatted for readability)
    if state.get("baseline_comparison") and not state["baseline_comparison"].startswith("[Error"):
        baseline_formatted_for_llm = _format_specialist_output(state["baseline_comparison"], "Baseline Change Analysis")
        synthesis_prompt += f"""
## BASELINE CHANGE ANALYSIS:
{baseline_formatted_for_llm}

**Integration Note:** The baseline comparison identifies how this analysis differs from prior assessments. Incorporate key changes (confirmed/challenged/updated) into your final synthesis. Highlight where confidence has increased or decreased relative to prior assessment.
"""

    synthesis_prompt += """
## YOUR TASK:
Produce a final assessment that:
1. **Preserves the consensus view** where well-supported
2. **Integrates ACH alternative hypotheses** as "monitoring-worthy" where plausible
3. **Highlights baseline changes** (confirmations, challenges, updates) if provided
4. **Flags disconfirming evidence** that warrants caution
5. **Provides decision-makers** with both the consensus AND credible alternatives

**Tone:** Balanced, acknowledges uncertainty, action-oriented.

**Structure:**
- Executive Summary (2-3 sentences, include baseline change summary if applicable)
- Primary Assessment (consensus view)
- Baseline Change Summary (if applicable: what changed from prior assessment)
- Alternative Hypotheses Worth Monitoring (from ACH)
- Key Uncertainties & Disconfirming Evidence
- Recommended Actions

Use markdown formatting with clear section headers.
"""

    from langfuse.openai import AsyncOpenAI
    openai_kwargs: dict[str, Any] = {"api_key": api_key}
    base_url = os.getenv("OPENAI_BASE_URL")
    if base_url:
        openai_kwargs["base_url"] = base_url
    client = AsyncOpenAI(**openai_kwargs)

    effective_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    try:
        resp = await client.chat.completions.create(
            model=effective_model,
            messages=[
                {"role": "system", "content": AGGREGATOR_SYSTEM_PROMPT},
                {"role": "user", "content": synthesis_prompt},
            ],
            temperature=0.3,
            max_completion_tokens=4096,
            name="final_synthesis"
        )
        synthesis_output = resp.choices[0].message.content or ""

        # Append formatted ACH and baseline comparison outputs for reference
        appendix_parts = []

        if state.get("ach_analysis"):
            ach_formatted = _format_specialist_output(state["ach_analysis"], "ACH Red Team Analysis")
            appendix_parts.append(f"""
---

## APPENDIX A: ACH Red Team Analysis

{ach_formatted}
""")

        if state.get("baseline_comparison") and not state["baseline_comparison"].startswith("[Error"):
            baseline_formatted = _format_specialist_output(state["baseline_comparison"], "Baseline Comparison Analysis")
            appendix_parts.append(f"""
---

## APPENDIX B: Baseline Comparison Analysis

{baseline_formatted}
""")

        # Combine synthesis + appendices
        full_output = synthesis_output
        if appendix_parts:
            full_output += "\n" + "".join(appendix_parts)

        return {"output": full_output}
    except Exception as exc:
        logger.error("Final synthesis LLM failed task_id=%s: %s", task_id, exc)
        # Fallback: concatenate all components with formatting
        parts = [state['aggregated_consensus']]
        if state.get("ach_analysis"):
            ach_formatted = _format_specialist_output(state["ach_analysis"], "ACH Challenges")
            parts.append(f"\n\n---\n\n## ACH CHALLENGES:\n{ach_formatted}")
        if state.get("baseline_comparison") and not state["baseline_comparison"].startswith("[Error"):
            baseline_formatted = _format_specialist_output(state["baseline_comparison"], "Baseline Comparison")
            parts.append(f"\n\n---\n\n## BASELINE COMPARISON:\n{baseline_formatted}")
        return {"output": "".join(parts)}


# ---------------------------------------------------------------------------
# Fixed nodes
# ---------------------------------------------------------------------------

def receive(state: LeadAnalystState, config: RunnableConfig) -> dict[str, Any]:
    executor = config["configurable"]["executor"]
    task_id = config["configurable"]["task_id"]
    executor.check_cancelled(task_id)
    return {"results": [], "selection_reasoning": {}}


def _format_value(value: Any, indent: int = 0) -> list[str]:
    """Recursively format a JSON value into markdown lines with proper indentation."""
    prefix = "  " * indent

    if isinstance(value, dict):
        # Format nested dictionary as subsections
        lines = []
        for key, val in value.items():
            # Convert snake_case to Title Case for headers
            header = key.replace("_", " ").title()
            lines.append(f"{prefix}*{header}:*")
            lines.extend(_format_value(val, indent + 1))
        return lines

    elif isinstance(value, list):
        # Format list items as bullets
        lines = []
        for item in value:
            if isinstance(item, (dict, list)):
                # Nested structure
                lines.extend(_format_value(item, indent))
            else:
                # Simple list item
                lines.append(f"{prefix}- {item}")
        return lines

    else:
        # Simple scalar value
        return [f"{prefix}{value}"]


def _format_specialist_output(text: str, default_label: str = "Analysis") -> str:
    """Parse and format a specialist's JSON output into readable markdown.

    Dynamically handles any JSON structure without hardcoding field names.
    Otherwise, return the raw text.
    """
    try:
        analysis = json.loads(text)
        if not isinstance(analysis, dict):
            return text

        parts = []

        # Define field display order and special handling
        # Top-level fields that should be rendered as simple key: value
        SIMPLE_FIELDS = {
            "framework_name": "Framework",
            "summary": "Summary",
            "change_magnitude": "Change Magnitude",
            "limitations": "Limitations",
            "confidence_level": "Confidence",
            "pre_mortem": "Pre-Mortem Analysis",
            "question_reframing": "Question Reframing",
        }

        # Fields that contain lists (render as bullet points)
        LIST_FIELDS = {
            "key_findings": "Key Findings",
            "evidence": "Evidence",
            "evidence_cited": "Evidence",
            "predictions": "Predictions",
            "alternative_hypotheses": "Alternative Hypotheses",
            "disconfirming_evidence": "Disconfirming Evidence",
            "recommended_actions": "Recommended Actions",
            "key_deltas": "Key Deltas",
        }

        # Fields that contain nested structures (render recursively)
        NESTED_FIELDS = {
            "changes": "Changes from Baseline",
            "baseline_changes": "Changes from Baseline",
            "confidence_assessment": "Confidence Assessment",
        }

        # Render fields in order: simple → lists → nested → remaining
        rendered_keys = set()

        # 1. Simple fields first
        for key, label in SIMPLE_FIELDS.items():
            if key in analysis:
                parts.append(f"**{label}:** {analysis[key]}")
                parts.append("")
                rendered_keys.add(key)

        # 2. List fields
        for key, label in LIST_FIELDS.items():
            if key in analysis and analysis[key]:
                parts.append(f"**{label}:**")
                for item in analysis[key]:
                    parts.append(f"- {item}")
                parts.append("")
                rendered_keys.add(key)

        # 3. Nested structured fields
        for key, label in NESTED_FIELDS.items():
            if key in analysis and analysis[key]:
                parts.append(f"**{label}:**")
                parts.extend(_format_value(analysis[key], indent=0))
                parts.append("")
                rendered_keys.add(key)

        # 4. Any remaining fields not explicitly handled (catch-all)
        for key, value in analysis.items():
            if key not in rendered_keys:
                header = key.replace("_", " ").title()
                parts.append(f"**{header}:**")
                parts.extend(_format_value(value, indent=0))
                parts.append("")

        return "\n".join(parts) if parts else text

    except (json.JSONDecodeError, TypeError, KeyError) as e:
        # Not valid JSON or unexpected structure - return raw text
        return text


def _build_aggregation_prompt(
    input_text: str,
    baselines: str,
    results: list[tuple[str, str]],
    selection_reasoning: dict[str, str] | None = None,
    peripheral_findings: str = "",
) -> str:
    """Build the user prompt for the aggregation LLM call."""
    parts = [
        "# META-ANALYSIS TASK",
        "",
        "## Original Request:",
        input_text,
        "",
    ]

    # Include baselines section for change detection
    if baselines:
        parts.extend([
            "## Current Baseline Assessments:",
            "",
            baselines,
            "",
            "**Your task:** Evaluate how the specialist analyses below challenge, update, or confirm these baselines.",
            "",
            "---",
            "",
        ])

    if selection_reasoning:
        active_reasoning = {k: v for k, v in selection_reasoning.items() if v}
        if active_reasoning:
            parts.extend(["## Specialist Selection Rationale:", ""])
            for label, reasoning in active_reasoning.items():
                parts.append(f"- **{label}**: {reasoning}")
            parts.extend(["", "---", ""])

    parts.extend(["## Individual Analyses:", ""])

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

    # Add peripheral findings section if available
    if peripheral_findings and not peripheral_findings.startswith("[Error"):
        parts.extend([
            "",
            "## PERIPHERAL SCAN FINDINGS:",
            "",
            "The Peripheral Scanner identified the following signals, blind spots, and uncited intelligence:",
            "",
            peripheral_findings,
            "",
            "**Integration Task:** Incorporate peripheral findings into your synthesis, especially signals that:",
            "- Were missed by all domain frameworks (collective blind spot)",
            "- Provide cross-domain connections",
            "- Represent weak early warnings",
            "",
            "---",
            "",
        ])

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
            return {"aggregated_consensus": "No sub-agent results available.", "output": "No sub-agent results available."}

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            sections = [f"=== {label} ===\n{text}" for label, text in results]
            concatenated = "\n\n".join(sections)
            return {"aggregated_consensus": concatenated, "output": concatenated}

        user_prompt = _build_aggregation_prompt(
            state["input"],
            state.get("baselines", ""),
            results,
            state.get("selection_reasoning"),
            peripheral_findings=state.get("peripheral_findings", "")
        )

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
                name="aggregate"
            )
            return {"aggregated_consensus": resp.choices[0].message.content or ""}
        except openai.APIError as e:
            logger.error(
                "Lead analyst aggregation LLM failed task_id=%s, falling back to concat: %s",
                task_id,
                e,
            )
            aggregated = "\n\n---\n\n".join(text for _, text in results)
            return {"aggregated_consensus": aggregated}

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
    graph.add_edge("respond", END)

    # Add meta-analysis nodes (used by both modes)
    graph.add_node("call_peripheral_scan", call_peripheral_scan)
    graph.add_node("call_ach_red_team", call_ach_red_team)
    graph.add_node("call_baseline_comparison", call_baseline_comparison)
    graph.add_node("final_synthesis", final_synthesis)

    def should_run_ach_analysis(state: LeadAnalystState) -> str:
        """Conditional edge: decide whether to run ACH red team or go straight to respond."""
        # For now, always run ACH analysis (default mode as requested)
        # Future: add logic based on question type, user preference, or state flags
        return "call_ach_red_team"

    if dynamic_discovery:
        effective_cp_url = control_plane_url or os.getenv("CONTROL_PLANE_URL", "")
        if not effective_cp_url:
            raise ValueError(
                "dynamic_discovery=True requires control_plane_url in YAML "
                "or CONTROL_PLANE_URL environment variable"
            )
        # Dynamic mode: LLM selects specialists from control plane
        graph.add_node("discover_and_select", _make_discover_node(effective_cp_url, min_specialists))
        graph.add_node("call_specialist", call_specialist)

        # Flow: discover → specialists (parallel) → peripheral_scan → aggregate → ach → synthesis → respond
        graph.add_edge("receive", "discover_and_select")
        graph.add_conditional_edges("discover_and_select", route_to_specialists, ["call_specialist"])
        # Send() handles fan-out synchronization: all parallel call_specialist instances
        # must complete before the graph advances. No loop needed.
        graph.add_edge("call_specialist", "call_peripheral_scan")
    else:
        # Static mode: use YAML-defined sub_agents
        for sa in sub_agents:
            graph.add_node(sa.node_id, _make_sub_agent_node(sa))
            graph.add_edge("receive", sa.node_id)
            graph.add_edge(sa.node_id, "call_peripheral_scan")
        if not sub_agents:
            # No sub-agents: skip straight to peripheral scan with empty results
            graph.add_edge("receive", "call_peripheral_scan")

    # Unified flow: peripheral_scan → aggregate → [conditional: ach or respond]
    graph.add_edge("call_peripheral_scan", "aggregate")

    # Conditional ACH analysis flow (default: always run ACH)
    graph.add_conditional_edges(
        "aggregate",
        should_run_ach_analysis,
        {
            "call_ach_red_team": "call_ach_red_team",
            "respond": "respond",  # Future: direct path if ACH is skipped
        }
    )

    # Sequential meta-analysis pipeline: ach → baseline_comparison → synthesis → respond
    graph.add_edge("call_ach_red_team", "call_baseline_comparison")
    graph.add_edge("call_baseline_comparison", "final_synthesis")
    graph.add_edge("final_synthesis", "respond")

    return graph.compile()
