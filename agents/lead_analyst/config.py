"""YAML-driven configuration for Lead Analyst orchestrators."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PROMPTS_DIR = Path(__file__).parent / "prompts"


@dataclass
class SubAgentConfig:
    """A single downstream sub-agent."""

    label: str
    url: str
    node_id: str  # derived: sanitised label for use as LangGraph node name

    @property
    def result_key(self) -> str:
        """State dict key where this sub-agent's result is stored."""
        return f"{self.node_id}_result"


def _to_node_id(label: str) -> str:
    """Convert a label to a valid, unique LangGraph node id.

    ``'ASEAN Security Analyst'`` → ``'call_asean_security_analyst'``
    """
    slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    return f"{slug}"


def _derive_type_id(filename: str) -> str:
    """Derive a type_id from a filename: ``lead_analyst.yaml`` → ``lead-analyst``."""
    stem = Path(filename).stem
    return re.sub(r"[_ ]+", "-", stem).lower()


@dataclass
class LeadAnalystConfig:
    """Configuration for a single lead analyst orchestrator loaded from YAML."""

    type_id: str
    name: str
    description: str
    sub_agents: list[SubAgentConfig]
    version: str = "0.1.0"
    aggregation_prompt: str | None = None
    model: str | None = None
    temperature: float = 0.3
    max_completion_tokens: int = 4096
    skills: list[dict[str, Any]] = field(default_factory=list)
    input_fields: list[dict[str, Any]] = field(default_factory=list)


def load_lead_analyst_configs(directory: Path) -> list[LeadAnalystConfig]:
    """Load all ``*.yaml`` / ``*.yml`` files from *directory* and return configs.

    Raises ``ValueError`` on missing required fields or duplicate type_ids.
    """
    configs: list[LeadAnalystConfig] = []
    seen_ids: dict[str, str] = {}

    yaml_files = sorted(list(directory.glob("*.yaml")) + list(directory.glob("*.yml")))
    if not yaml_files:
        print(f"[lead-analyst] WARNING: no YAML files found in {directory}")
        return configs

    for path in yaml_files:
        with open(path, "r", encoding="utf-8") as f:
            try:
                data: dict[str, Any] = yaml.safe_load(f) or {}
            except yaml.YAMLError as e:
                raise ValueError(f"Invalid YAML in {path}: {e}") from e

        # Required fields
        name = data.get("name")
        if not name:
            raise ValueError(f"{path.name}: missing required field 'name'")

        raw_sub_agents = data.get("sub_agents", [])
        if not raw_sub_agents:
            raise ValueError(f"{path.name}: must define at least one sub_agent")

        # Parse sub-agents
        sub_agents: list[SubAgentConfig] = []
        seen_node_ids: dict[str, str] = {}
        for entry in raw_sub_agents:
            label = entry.get("label")
            url = entry.get("url")
            if not label:
                raise ValueError(f"{path.name}: sub_agent entry missing 'label'")
            if not url:
                raise ValueError(f"{path.name}: sub_agent '{label}' missing 'url'")
            node_id = _to_node_id(label)
            if node_id in seen_node_ids:
                raise ValueError(
                    f"{path.name}: duplicate node id '{node_id}' "
                    f"(from '{label}' and '{seen_node_ids[node_id]}')"
                )
            seen_node_ids[node_id] = label
            sub_agents.append(SubAgentConfig(label=label, url=url, node_id=node_id))

        # Aggregation prompt: inline or file, mutually exclusive
        aggregation_prompt = data.get("aggregation_prompt")
        aggregation_prompt_file = data.get("aggregation_prompt_file")

        if aggregation_prompt and aggregation_prompt_file:
            raise ValueError(
                f"{path.name}: specify either 'aggregation_prompt' or "
                "'aggregation_prompt_file', not both"
            )

        if aggregation_prompt_file:
            prompt_path = PROMPTS_DIR / aggregation_prompt_file
            if not prompt_path.is_file():
                raise ValueError(
                    f"{path.name}: aggregation_prompt_file '{aggregation_prompt_file}' "
                    f"not found at {prompt_path}"
                )
            aggregation_prompt = prompt_path.read_text(encoding="utf-8")

        type_id = data.get("type_id") or _derive_type_id(path.name)

        # Uniqueness check
        if type_id in seen_ids:
            raise ValueError(
                f"{path.name}: duplicate type_id '{type_id}' "
                f"(already defined in {seen_ids[type_id]})"
            )
        seen_ids[type_id] = path.name

        configs.append(
            LeadAnalystConfig(
                type_id=type_id,
                name=name,
                description=data.get("description", name),
                sub_agents=sub_agents,
                version=data.get("version", "0.1.0"),
                aggregation_prompt=aggregation_prompt,
                model=data.get("model"),
                temperature=data.get("temperature", 0.3),
                max_completion_tokens=data.get("max_completion_tokens", 4096),
                skills=data.get("skills", []),
                input_fields=data.get("input_fields", []),
            )
        )
        print(f"[lead-analyst] Loaded config: {type_id} ({name}) from {path.name}")
        for sa in sub_agents:
            print(f"[lead-analyst]   Sub-agent: {sa.node_id} -> {sa.url}")

    return configs
