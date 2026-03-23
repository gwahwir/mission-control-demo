"""Tests for dynamic specialist discovery in the Lead Analyst."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _write_yaml(path: Path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f)


FAKE_SPECIALISTS = [
    {
        "id": "taleb-antifragile",
        "name": "Taleb Antifragile Analyst",
        "description": "Analyzes tail risks using Taleb's antifragility framework",
        "status": "online",
        "skills": [{"id": "s1", "name": "Antifragile", "tags": ["specialist", "analysis"]}],
        "instances": [{"url": "http://specialist:8006/taleb-antifragile", "status": "online", "active_tasks": 0}],
    },
    {
        "id": "realist-ir",
        "name": "Realist IR Analyst",
        "description": "Analyzes international relations through a realist lens",
        "status": "online",
        "skills": [{"id": "s2", "name": "Realist IR", "tags": ["specialist", "analysis"]}],
        "instances": [{"url": "http://specialist:8006/realist-ir", "status": "online", "active_tasks": 0}],
    },
    {
        "id": "asean-security",
        "name": "ASEAN Security Analyst",
        "description": "Analyzes Southeast Asian security dynamics",
        "status": "online",
        "skills": [{"id": "s3", "name": "ASEAN Security", "tags": ["specialist", "analysis"]}],
        "instances": [{"url": "http://specialist:8006/asean-security", "status": "online", "active_tasks": 0}],
    },
    {
        "id": "lead-analyst-a",  # NOT a specialist — no "specialist" tag
        "name": "Lead Analyst A",
        "description": "An orchestrator",
        "status": "online",
        "skills": [{"id": "s4", "name": "Orchestrate", "tags": ["orchestration"]}],
        "instances": [{"url": "http://lead:8005/lead-analyst-a", "status": "online", "active_tasks": 0}],
    },
    {
        "id": "offline-specialist",
        "name": "Offline Specialist",
        "description": "Currently offline",
        "status": "offline",
        "skills": [{"id": "s5", "name": "Offline", "tags": ["specialist"]}],
        "instances": [{"url": "http://specialist:8006/offline-specialist", "status": "offline", "active_tasks": 0}],
    },
]


def _make_runnableconfig():
    executor = MagicMock()
    executor.check_cancelled = MagicMock()
    return {"configurable": {"executor": executor, "task_id": "t1", "context_id": "c1"}}


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------

class TestDynamicDiscoveryConfig:

    def test_dynamic_discovery_defaults_to_false(self, tmp_path):
        from agents.lead_analyst.config import load_lead_analyst_configs
        _write_yaml(tmp_path / "test.yaml", {
            "name": "Test Analyst",
            "sub_agents": [{"label": "Echo", "url": "http://echo:8001"}],
        })
        configs = load_lead_analyst_configs(tmp_path)
        assert configs[0].dynamic_discovery is False

    def test_dynamic_discovery_true_makes_sub_agents_optional(self, tmp_path):
        from agents.lead_analyst.config import load_lead_analyst_configs
        _write_yaml(tmp_path / "test.yaml", {
            "name": "Dynamic Analyst",
            "dynamic_discovery": True,
            "control_plane_url": "http://cp:8000",
        })
        configs = load_lead_analyst_configs(tmp_path)
        assert configs[0].dynamic_discovery is True
        assert configs[0].sub_agents == []

    def test_static_mode_still_requires_sub_agents(self, tmp_path):
        from agents.lead_analyst.config import load_lead_analyst_configs
        _write_yaml(tmp_path / "no_agents.yaml", {"name": "Bad Analyst"})
        with pytest.raises(ValueError, match="sub_agent"):
            load_lead_analyst_configs(tmp_path)

    def test_min_specialists_defaults_to_3(self, tmp_path):
        from agents.lead_analyst.config import load_lead_analyst_configs
        _write_yaml(tmp_path / "test.yaml", {
            "name": "Dynamic Analyst",
            "dynamic_discovery": True,
            "control_plane_url": "http://cp:8000",
        })
        configs = load_lead_analyst_configs(tmp_path)
        assert configs[0].min_specialists == 3

    def test_min_specialists_configurable(self, tmp_path):
        from agents.lead_analyst.config import load_lead_analyst_configs
        _write_yaml(tmp_path / "test.yaml", {
            "name": "Dynamic Analyst",
            "dynamic_discovery": True,
            "control_plane_url": "http://cp:8000",
            "min_specialists": 5,
        })
        configs = load_lead_analyst_configs(tmp_path)
        assert configs[0].min_specialists == 5

    def test_control_plane_url_from_yaml(self, tmp_path):
        from agents.lead_analyst.config import load_lead_analyst_configs
        _write_yaml(tmp_path / "test.yaml", {
            "name": "Dynamic Analyst",
            "dynamic_discovery": True,
            "control_plane_url": "http://my-cp:8000",
        })
        configs = load_lead_analyst_configs(tmp_path)
        assert configs[0].control_plane_url == "http://my-cp:8000"


# ---------------------------------------------------------------------------
# _filter_online_specialists
# ---------------------------------------------------------------------------

class TestFilterOnlineSpecialists:

    def test_excludes_non_specialist_tagged_agents(self):
        from agents.lead_analyst.graph import _filter_online_specialists
        result = _filter_online_specialists(FAKE_SPECIALISTS)
        labels = [r["label"] for r in result]
        assert "Lead Analyst A" not in labels

    def test_excludes_offline_agents(self):
        from agents.lead_analyst.graph import _filter_online_specialists
        result = _filter_online_specialists(FAKE_SPECIALISTS)
        labels = [r["label"] for r in result]
        assert "Offline Specialist" not in labels

    def test_returns_three_online_specialists(self):
        from agents.lead_analyst.graph import _filter_online_specialists
        result = _filter_online_specialists(FAKE_SPECIALISTS)
        assert len(result) == 3

    def test_picks_first_online_instance_url(self):
        from agents.lead_analyst.graph import _filter_online_specialists
        agent = {
            "id": "multi",
            "name": "Multi Instance",
            "description": "...",
            "status": "online",
            "skills": [{"tags": ["specialist"]}],
            "instances": [
                {"url": "http://host-1:8006/multi", "status": "offline", "active_tasks": 0},
                {"url": "http://host-2:8006/multi", "status": "online", "active_tasks": 0},
            ],
        }
        result = _filter_online_specialists([agent])
        assert result[0]["url"] == "http://host-2:8006/multi"


# ---------------------------------------------------------------------------
# discover_and_select node
# ---------------------------------------------------------------------------

class TestDiscoverAndSelectNode:

    def _make_node(self, control_plane_url="http://cp:8000", min_specialists=3):
        from agents.lead_analyst.graph import _make_discover_node
        return _make_discover_node(control_plane_url, min_specialists)

    async def test_selects_via_llm_when_api_key_available(self):
        node = self._make_node()
        state = {"input": "Analyze ASEAN dynamics", "results": [], "selected_specialists": []}
        config = _make_runnableconfig()
        selected = [
            {"label": "Taleb Antifragile Analyst", "url": "http://specialist:8006/taleb-antifragile"},
            {"label": "Realist IR Analyst", "url": "http://specialist:8006/realist-ir"},
            {"label": "ASEAN Security Analyst", "url": "http://specialist:8006/asean-security"},
        ]
        with patch("agents.lead_analyst.graph._fetch_agents", new=AsyncMock(return_value=FAKE_SPECIALISTS)):
            with patch("agents.lead_analyst.graph._select_specialists_with_llm",
                       new=AsyncMock(return_value=selected)):
                result = await node(state, config)
        assert len(result["selected_specialists"]) == 3

    async def test_falls_back_to_first_n_when_no_api_key(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        node = self._make_node(min_specialists=2)
        state = {"input": "test", "results": [], "selected_specialists": []}
        config = _make_runnableconfig()
        with patch("agents.lead_analyst.graph._fetch_agents", new=AsyncMock(return_value=FAKE_SPECIALISTS)):
            result = await node(state, config)
        assert len(result["selected_specialists"]) == 2

    async def test_falls_back_when_llm_fails(self):
        node = self._make_node(min_specialists=2)
        state = {"input": "test", "results": [], "selected_specialists": []}
        config = _make_runnableconfig()
        with patch("agents.lead_analyst.graph._fetch_agents", new=AsyncMock(return_value=FAKE_SPECIALISTS)):
            with patch("agents.lead_analyst.graph._select_specialists_with_llm",
                       new=AsyncMock(side_effect=Exception("LLM down"))):
                result = await node(state, config)
        assert len(result["selected_specialists"]) == 2

    async def test_raises_if_fewer_online_than_min(self):
        node = self._make_node(min_specialists=5)
        state = {"input": "test", "results": [], "selected_specialists": []}
        config = _make_runnableconfig()
        with patch("agents.lead_analyst.graph._fetch_agents", new=AsyncMock(return_value=FAKE_SPECIALISTS)):
            with pytest.raises(RuntimeError, match="min_specialists"):
                await node(state, config)


# ---------------------------------------------------------------------------
# route_to_specialists
# ---------------------------------------------------------------------------

class TestRouteToSpecialists:

    def test_creates_send_per_specialist(self):
        from langgraph.types import Send
        from agents.lead_analyst.graph import route_to_specialists
        state = {
            "input": "test", "results": [], "output": "",
            "selected_specialists": [
                {"label": "A", "url": "http://a"},
                {"label": "B", "url": "http://b"},
                {"label": "C", "url": "http://c"},
            ],
        }
        sends = route_to_specialists(state)
        assert len(sends) == 3
        assert all(isinstance(s, Send) for s in sends)
        assert all(s.node == "call_specialist" for s in sends)

    def test_empty_list_returns_empty(self):
        from agents.lead_analyst.graph import route_to_specialists
        state = {"input": "test", "results": [], "output": "", "selected_specialists": []}
        assert route_to_specialists(state) == []


# ---------------------------------------------------------------------------
# call_specialist node
# ---------------------------------------------------------------------------

class TestCallSpecialistNode:

    async def test_returns_tuple_in_results(self):
        from agents.lead_analyst.graph import call_specialist
        state = {
            "input": "Analyze the summit",
            "results": [], "selected_specialists": [],
            "_spec_label": "ASEAN Security Analyst",
            "_spec_url": "http://specialist:8006/asean-security",
        }
        config = _make_runnableconfig()
        with patch("agents.lead_analyst.graph._call_sub_agent",
                   new=AsyncMock(return_value='{"summary": "analysis"}')):
            result = await call_specialist(state, config)
        assert result["results"] == [("ASEAN Security Analyst", '{"summary": "analysis"}')]

    async def test_handles_error_gracefully(self):
        from agents.lead_analyst.graph import call_specialist
        state = {
            "input": "test", "results": [], "selected_specialists": [],
            "_spec_label": "Failing Agent", "_spec_url": "http://bad:9999/agent",
        }
        config = _make_runnableconfig()
        with patch("agents.lead_analyst.graph._call_sub_agent",
                   new=AsyncMock(side_effect=Exception("connection refused"))):
            result = await call_specialist(state, config)
        label, text = result["results"][0]
        assert label == "Failing Agent"
        assert "[Error" in text


# ---------------------------------------------------------------------------
# Full dynamic graph integration
# ---------------------------------------------------------------------------

class TestDynamicGraphIntegration:

    async def test_dynamic_graph_fans_out_to_three(self):
        from agents.lead_analyst.graph import build_lead_analyst_graph
        graph = build_lead_analyst_graph(
            sub_agents=[], dynamic_discovery=True,
            control_plane_url="http://cp:8000", min_specialists=3,
        )
        config = _make_runnableconfig()
        selected = [
            {"label": "Analyst A", "url": "http://s:8006/a"},
            {"label": "Analyst B", "url": "http://s:8006/b"},
            {"label": "Analyst C", "url": "http://s:8006/c"},
        ]
        with patch("agents.lead_analyst.graph._fetch_agents",
                   new=AsyncMock(return_value=FAKE_SPECIALISTS)):
            with patch("agents.lead_analyst.graph._select_specialists_with_llm",
                       new=AsyncMock(return_value=selected)):
                with patch("agents.lead_analyst.graph._call_sub_agent",
                           new=AsyncMock(return_value='{"summary": "analysis"}')):
                    result = await graph.ainvoke(
                        {"input": "What is the geopolitical risk in ASEAN?"},
                        config=config,
                    )
        assert len(result["results"]) == 3
        assert {r[0] for r in result["results"]} == {"Analyst A", "Analyst B", "Analyst C"}

    async def test_static_graph_unchanged(self):
        from agents.lead_analyst.config import SubAgentConfig
        from agents.lead_analyst.graph import build_lead_analyst_graph
        sub_agents = [SubAgentConfig(label="Echo Agent", url="http://echo:8001", node_id="echo_agent")]
        graph = build_lead_analyst_graph(sub_agents=sub_agents, dynamic_discovery=False)
        config = _make_runnableconfig()
        with patch("agents.lead_analyst.graph._call_sub_agent",
                   new=AsyncMock(return_value="echo result")):
            result = await graph.ainvoke({"input": "ping"}, config=config)
        assert result["results"] == [("Echo Agent", "echo result")]
