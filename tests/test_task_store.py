# tests/test_task_store.py
from __future__ import annotations
import json
import pytest
from control_plane.task_store import TaskRecord, TaskState


def test_task_record_node_outputs_default():
    r = TaskRecord(task_id="t1", agent_id="echo-agent")
    assert r.node_outputs == {}


def test_task_record_running_node_default():
    r = TaskRecord(task_id="t1", agent_id="echo-agent")
    assert r.running_node == ""


def test_task_record_to_dict_includes_node_outputs_and_running_node():
    r = TaskRecord(task_id="t1", agent_id="echo-agent")
    r.node_outputs["receive"] = '{"input": "hello"}'
    r.running_node = "analyze"
    d = r.to_dict()
    assert d["node_outputs"]["receive"] == '{"input": "hello"}'
    assert d["running_node"] == "analyze"


def test_task_record_from_row_deserializes_node_outputs():
    row = {
        "task_id": "t1", "agent_id": "echo-agent", "instance_url": "",
        "state": "completed", "input_text": "hi", "baselines": "",
        "key_questions": "", "output_text": "HI", "error": "",
        "created_at": 1000.0, "updated_at": 1001.0, "a2a_task": "{}",
        "node_outputs": '{"receive": "{\\"input\\": \\"hi\\"}"}',
        "running_node": "analyze",
    }
    r = TaskRecord.from_row(row)
    assert r.node_outputs == {"receive": '{"input": "hi"}'}
    assert r.running_node == "analyze"


def test_task_record_from_row_missing_fields_use_defaults():
    row = {
        "task_id": "t1", "agent_id": "echo-agent", "instance_url": "",
        "state": "completed", "input_text": "hi", "baselines": "",
        "key_questions": "", "output_text": "", "error": "",
        "created_at": 1000.0, "updated_at": 1001.0, "a2a_task": "{}",
        # node_outputs and running_node intentionally absent
    }
    r = TaskRecord.from_row(row)
    assert r.node_outputs == {}
    assert r.running_node == ""
