import sys
import pytest


def test_build_prompt_contains_seed():
    from scripts_for_testing.generate_synthetic_baselines import build_prompt
    seed = "US-China trade war escalates over semiconductor tariffs."
    prompt = build_prompt(seed, n_topics=3, n_versions=2)
    assert seed in prompt
    assert "3" in prompt
    assert "2" in prompt


def test_build_prompt_returns_string():
    from scripts_for_testing.generate_synthetic_baselines import build_prompt
    result = build_prompt("some seed", n_topics=2, n_versions=1)
    assert isinstance(result, str)
    assert len(result) > 100


def test_build_delta_body_first_version():
    from scripts_for_testing.generate_synthetic_baselines import build_delta_body
    version_entry = {
        "delta_summary": "Initial baseline established.",
        "claims_added": ["Claim A"],
        "claims_superseded": [],
    }
    body = build_delta_body(version_entry, from_version=None, to_version=1)
    assert body["from_version"] is None
    assert body["to_version"] == 1
    assert body["delta_summary"] == "Initial baseline established."
    assert body["claims_added"] == ["Claim A"]
    assert body["claims_superseded"] == []
    assert body["article_metadata"] == {}


def test_build_delta_body_subsequent_version():
    from scripts_for_testing.generate_synthetic_baselines import build_delta_body
    version_entry = {
        "delta_summary": "Iran resumed talks.",
        "claims_added": ["New claim"],
        "claims_superseded": ["Old claim"],
    }
    body = build_delta_body(version_entry, from_version=1, to_version=2)
    assert body["from_version"] == 1
    assert body["to_version"] == 2
