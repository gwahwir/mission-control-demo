"""Tests for specialist_agent config loading, including system_prompt_file and output_format."""

from pathlib import Path

import pytest
import yaml


@pytest.fixture
def tmp_configs(tmp_path):
    """Return (cards_dir, prompts_dir) under tmp_path."""
    cards = tmp_path / "agent_cards"
    cards.mkdir()
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    return cards, prompts


def _write_yaml(path: Path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f)


def test_load_inline_system_prompt(tmp_configs, monkeypatch):
    """Existing inline system_prompt pattern still works."""
    cards, prompts = tmp_configs
    _write_yaml(cards / "test_agent.yaml", {
        "name": "Test Agent",
        "system_prompt": "You are a test agent.",
    })

    from agents.specialist_agent.config import load_specialist_configs
    monkeypatch.setattr("agents.specialist_agent.config.PROMPTS_DIR", prompts)

    configs = load_specialist_configs(cards)
    assert len(configs) == 1
    assert configs[0].system_prompt == "You are a test agent."
    assert configs[0].type_id == "test-agent"
    assert configs[0].output_format is None


def test_load_system_prompt_file(tmp_configs, monkeypatch):
    """system_prompt_file resolves .md file into system_prompt."""
    cards, prompts = tmp_configs
    (prompts / "my_prompt.md").write_text("# Expert\nYou are an expert.", encoding="utf-8")
    _write_yaml(cards / "my_agent.yaml", {
        "name": "My Agent",
        "system_prompt_file": "my_prompt.md",
    })

    from agents.specialist_agent.config import load_specialist_configs
    monkeypatch.setattr("agents.specialist_agent.config.PROMPTS_DIR", prompts)

    configs = load_specialist_configs(cards)
    assert len(configs) == 1
    assert configs[0].system_prompt == "# Expert\nYou are an expert."


def test_both_prompt_and_file_raises(tmp_configs, monkeypatch):
    """Specifying both system_prompt and system_prompt_file raises ValueError."""
    cards, prompts = tmp_configs
    (prompts / "p.md").write_text("prompt", encoding="utf-8")
    _write_yaml(cards / "bad.yaml", {
        "name": "Bad Agent",
        "system_prompt": "inline",
        "system_prompt_file": "p.md",
    })

    from agents.specialist_agent.config import load_specialist_configs
    monkeypatch.setattr("agents.specialist_agent.config.PROMPTS_DIR", prompts)

    with pytest.raises(ValueError, match="not both"):
        load_specialist_configs(cards)


def test_missing_prompt_file_raises(tmp_configs, monkeypatch):
    """Nonexistent .md file raises ValueError."""
    cards, prompts = tmp_configs
    _write_yaml(cards / "missing.yaml", {
        "name": "Missing Agent",
        "system_prompt_file": "nonexistent.md",
    })

    from agents.specialist_agent.config import load_specialist_configs
    monkeypatch.setattr("agents.specialist_agent.config.PROMPTS_DIR", prompts)

    with pytest.raises(ValueError, match="not found"):
        load_specialist_configs(cards)


def test_output_format_loaded(tmp_configs, monkeypatch):
    """output_format is preserved from YAML."""
    cards, prompts = tmp_configs
    _write_yaml(cards / "fmt.yaml", {
        "name": "Format Agent",
        "system_prompt": "You are an agent.",
        "output_format": "Respond as JSON.",
    })

    from agents.specialist_agent.config import load_specialist_configs
    monkeypatch.setattr("agents.specialist_agent.config.PROMPTS_DIR", prompts)

    configs = load_specialist_configs(cards)
    assert configs[0].output_format == "Respond as JSON."


def test_all_analytical_yamls_load():
    """All analytical framework YAMLs load successfully with .md content."""
    from agents.specialist_agent.config import load_specialist_configs

    cards_dir = Path(__file__).parent.parent / "agents" / "specialist_agent" / "agent_cards"
    configs = load_specialist_configs(cards_dir)

    # 16 analytical framework agents (14 original + climate_security + economic_statecraft)
    assert len(configs) == 16

    analytical_ids = {
        "taleb-antifragile", "behavioral-economics", "realist-ir", "liberal-ir",
        "bilahari-kausikan", "yergin-energy", "counterfactual-thinking",
        "copenhagen-securitization", "military-strategy-deterrence",
        "technology-emerging-threats", "asean-security", "bridget-welsh",
        "ach-red-team", "peripheral-scan",
    }
    loaded_ids = {c.type_id for c in configs}
    assert analytical_ids.issubset(loaded_ids)

    # All analytical agents should have system_prompt loaded from file (non-empty)
    for c in configs:
        if c.type_id in analytical_ids:
            assert len(c.system_prompt) > 100, f"{c.type_id} prompt too short"
            assert c.output_format is not None, f"{c.type_id} missing output_format"
            assert c.max_completion_tokens == 4096, f"{c.type_id} should have max_completion_tokens=4096"
