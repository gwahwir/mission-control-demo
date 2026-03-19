"""YAML-driven configuration for specialist agents."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


PROMPTS_DIR = Path(__file__).parent / "prompts"


@dataclass
class SpecialistConfig:
    """Configuration for a single specialist agent loaded from YAML."""

    type_id: str
    name: str
    description: str
    system_prompt: str
    version: str = "0.1.0"
    model: str | None = None
    temperature: float = 0.3
    max_completion_tokens: int = 1024
    skills: list[dict[str, Any]] = field(default_factory=list)
    input_fields: list[dict[str, Any]] = field(default_factory=list)
    output_format: str | None = None


def _derive_type_id(filename: str) -> str:
    """Derive a type_id from a filename: ``code_reviewer.yaml`` → ``code-reviewer``."""
    stem = Path(filename).stem
    return re.sub(r"[_ ]+", "-", stem).lower()


def load_specialist_configs(directory: Path) -> list[SpecialistConfig]:
    """Load all ``*.yaml`` / ``*.yml`` files from *directory* and return configs.

    Raises ``ValueError`` on missing required fields or duplicate type_ids.
    """
    configs: list[SpecialistConfig] = []
    seen_ids: dict[str, str] = {}

    yaml_files = sorted(list(directory.glob("*.yaml")) + list(directory.glob("*.yml")))
    if not yaml_files:
        print(f"[specialist] WARNING: no YAML files found in {directory}")
        return configs

    for path in yaml_files:
        with open(path, "r", encoding="utf-8") as f:
            try:
                data: dict[str, Any] = yaml.safe_load(f) or {}
            except yaml.YAMLError as e:
                raise ValueError(f"Invalid YAML in {path}: {e}") from e

        # Required fields
        name = data.get("name")
        system_prompt = data.get("system_prompt")
        system_prompt_file = data.get("system_prompt_file")

        if not name:
            raise ValueError(f"{path.name}: missing required field 'name'")

        if system_prompt and system_prompt_file:
            raise ValueError(
                f"{path.name}: specify either 'system_prompt' or 'system_prompt_file', not both"
            )

        if system_prompt_file:
            prompt_path = PROMPTS_DIR / system_prompt_file
            if not prompt_path.is_file():
                raise ValueError(
                    f"{path.name}: system_prompt_file '{system_prompt_file}' not found at {prompt_path}"
                )
            system_prompt = prompt_path.read_text(encoding="utf-8")

        if not system_prompt:
            raise ValueError(f"{path.name}: missing required field 'system_prompt' or 'system_prompt_file'")

        type_id = data.get("type_id") or _derive_type_id(path.name)

        # Uniqueness check
        if type_id in seen_ids:
            raise ValueError(
                f"{path.name}: duplicate type_id '{type_id}' (already defined in {seen_ids[type_id]})"
            )
        seen_ids[type_id] = path.name

        configs.append(
            SpecialistConfig(
                type_id=type_id,
                name=name,
                description=data.get("description", name),
                system_prompt=system_prompt,
                version=data.get("version", "0.1.0"),
                model=data.get("model"),
                temperature=data.get("temperature", 0.3),
                max_completion_tokens=data.get("max_completion_tokens", 1024),
                skills=data.get("skills", []),
                input_fields=data.get("input_fields", []),
                output_format=data.get("output_format"),
            )
        )
        print(f"[specialist] Loaded config: {type_id} ({name}) from {path.name}")

    return configs
