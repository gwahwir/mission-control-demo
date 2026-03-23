#!/usr/bin/env python
"""Quick test script to verify structured input changes."""

import asyncio
import json
from control_plane.routes import TaskRequest
from control_plane.task_store import TaskRecord, TaskState
from control_plane.a2a_client import A2AClient


async def test_task_request():
    """Test 1: TaskRequest model accepts structured input."""
    print("TEST 1: TaskRequest Model")
    print("-" * 50)

    req = TaskRequest(
        text="Analyze Ukraine conflict",
        baselines="Current assessment: Low NATO intervention probability",
        key_questions="1. Has Russian doctrine changed? 2. New escalation signals?"
    )

    print(f"✓ TaskRequest created successfully")
    print(f"  - text: {req.text[:50]}...")
    print(f"  - baselines: {req.baselines[:50]}...")
    print(f"  - key_questions: {req.key_questions[:50]}...")
    print()


async def test_task_record():
    """Test 2: TaskRecord stores and serializes structured fields."""
    print("TEST 2: TaskRecord Storage")
    print("-" * 50)

    record = TaskRecord(
        task_id="test-123",
        agent_id="lead-analyst",
        instance_url="http://localhost:8005",
        state=TaskState.SUBMITTED,
        input_text="Test scenario",
        baselines="Baseline data here",
        key_questions="Question 1\nQuestion 2"
    )

    # Test serialization
    data = record.to_dict()
    assert "baselines" in data
    assert "key_questions" in data
    assert data["baselines"] == "Baseline data here"
    assert data["key_questions"] == "Question 1\nQuestion 2"

    print(f"✓ TaskRecord created and serialized")
    print(f"  - baselines field: present")
    print(f"  - key_questions field: present")
    print(f"  - to_dict() includes new fields: YES")

    # Test deserialization
    record2 = TaskRecord.from_row(data)
    assert record2.baselines == record.baselines
    assert record2.key_questions == record.key_questions

    print(f"✓ TaskRecord.from_row() works correctly")
    print()


async def test_a2a_message():
    """Test 3: A2A client constructs proper metadata."""
    print("TEST 3: A2A Client Message Construction")
    print("-" * 50)

    # This tests the signature - actual network call would need a running agent
    client = A2AClient("http://dummy:8000")

    # Verify method signature accepts new parameters
    import inspect
    sig = inspect.signature(client.send_message)
    params = list(sig.parameters.keys())

    assert "baselines" in params, "baselines parameter missing"
    assert "key_questions" in params, "key_questions parameter missing"

    print(f"✓ A2AClient.send_message() signature updated")
    print(f"  - Parameters: {', '.join(params)}")
    print()


async def test_lead_analyst_state():
    """Test 4: LeadAnalystState has new fields."""
    print("TEST 4: LeadAnalystState Structure")
    print("-" * 50)

    from agents.lead_analyst.graph import LeadAnalystState
    import typing

    # Check TypedDict has the fields
    annotations = typing.get_type_hints(LeadAnalystState)

    assert "baselines" in annotations, "baselines field missing from LeadAnalystState"
    assert "key_questions" in annotations, "key_questions field missing from LeadAnalystState"
    assert annotations["baselines"] == str
    assert annotations["key_questions"] == str

    print(f"✓ LeadAnalystState extended correctly")
    print(f"  - Fields: {', '.join(annotations.keys())}")
    print()


async def test_lead_analyst_executor():
    """Test 5: LeadAnalystExecutor.prepare_input() extracts metadata."""
    print("TEST 5: LeadAnalystExecutor.prepare_input()")
    print("-" * 50)

    from agents.lead_analyst.executor import LeadAnalystExecutor
    from agents.lead_analyst.config import LeadAnalystConfig
    from unittest.mock import Mock

    # Create a mock config
    config = Mock(spec=LeadAnalystConfig)
    config.sub_agents = []
    config.aggregation_prompt = None
    config.model = None
    config.temperature = 0.3
    config.max_completion_tokens = 4096
    config.name = "Test"
    config.dynamic_discovery = False
    config.control_plane_url = None
    config.min_specialists = 3

    executor = LeadAnalystExecutor(config)

    # Create mock context with metadata
    context = Mock()
    context.get_user_input = Mock(return_value="Test input")
    context.message = Mock()
    context.message.metadata = {
        "baselines": "Test baselines",
        "keyQuestions": "Test questions"
    }

    # Test prepare_input
    result = executor.prepare_input(context)

    assert "input" in result
    assert "baselines" in result
    assert "key_questions" in result
    assert result["input"] == "Test input"
    assert result["baselines"] == "Test baselines"
    assert result["key_questions"] == "Test questions"

    print(f"✓ prepare_input() extracts metadata correctly")
    print(f"  - Returns: {list(result.keys())}")
    print()


async def main():
    """Run all tests."""
    print("=" * 50)
    print("STRUCTURED INPUT IMPLEMENTATION TESTS")
    print("=" * 50)
    print()

    try:
        await test_task_request()
        await test_task_record()
        await test_a2a_message()
        await test_lead_analyst_state()
        await test_lead_analyst_executor()

        print("=" * 50)
        print("✓ ALL TESTS PASSED")
        print("=" * 50)
        return 0

    except Exception as e:
        print()
        print("=" * 50)
        print(f"✗ TEST FAILED: {e}")
        print("=" * 50)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(asyncio.run(main()))
