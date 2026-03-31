#!/usr/bin/env python
"""Manual integration test for baseline comparison specialist.

This script tests the baseline comparison specialist by loading its configuration
and verifying the structure matches the expected output format.

Usage:
    python test_baseline_manual.py
"""

import json
import os
import sys

# Fix Windows encoding for console output
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

# Mock OpenAI for testing without API key
os.environ["OPENAI_API_KEY"] = "test-key"

from agents.specialist_agent.config import load_specialist_configs
from pathlib import Path


def test_baseline_comparison():
    """Test the baseline comparison specialist directly."""

    # Load the baseline comparison config
    configs = load_specialist_configs(Path("agents/specialist_agent/agent_cards"))
    baseline_config = next((c for c in configs if c.type_id == "baseline-comparison"), None)

    if not baseline_config:
        print("[FAIL] Baseline comparison config not found!")
        return False

    print(f"[PASS] Loaded config: {baseline_config.name}")
    print(f"   Type ID: {baseline_config.type_id}")
    print(f"   System prompt: {len(baseline_config.system_prompt)} chars")
    print(f"   Output format defined: {bool(baseline_config.output_format)}")
    print(f"   Model: {baseline_config.model or 'default (gpt-4o-mini)'}")
    print(f"   Temperature: {baseline_config.temperature}")
    print(f"   Max tokens: {baseline_config.max_completion_tokens}\n")

    # Verify output format structure
    print("[INFO] Validating output format structure...")
    output_format = baseline_config.output_format

    required_fields = [
        "framework_name",
        "summary",
        "baseline_changes",
        "change_magnitude",
        "key_deltas",
        "confidence_assessment"
    ]

    baseline_change_fields = [
        "confirmed",
        "confirmed_tentative",
        "challenged",
        "challenged_uncertain",
        "updated",
        "stable",
        "new_insights",
        "ach_meta_insights"
    ]

    confidence_fields = [
        "overall",
        "ach_impact",
        "high_confidence_changes",
        "uncertain_changes"
    ]

    all_found = True
    for field in required_fields:
        if field in output_format:
            print(f"   [PASS] Found required field: {field}")
        else:
            print(f"   [FAIL] Missing required field: {field}")
            all_found = False

    print(f"\n[INFO] Checking baseline_changes sub-fields...")
    for field in baseline_change_fields:
        if field in output_format:
            print(f"   [PASS] Found: {field}")
        else:
            print(f"   [WARN] Not found: {field} (optional)")

    print(f"\n[INFO] Checking confidence_assessment sub-fields...")
    for field in confidence_fields:
        if field in output_format:
            print(f"   [PASS] Found: {field}")
        else:
            print(f"   [WARN] Not found: {field} (optional)")

    # Verify ACH integration
    print(f"\n[INFO] Checking ACH integration...")
    if "ach_impact" in output_format and "confirmed_tentative" in output_format:
        print("   [PASS] ACH integration fields present")
        print("   [PASS] Supports ACH-aware confidence calibration")
    else:
        print("   [WARN] ACH integration fields may be incomplete")

    # Check system prompt mentions ACH
    if "ACH" in baseline_config.system_prompt:
        print("   [PASS] System prompt includes ACH guidance")
    else:
        print("   [WARN] System prompt may not mention ACH")

    return all_found


if __name__ == "__main__":
    success = test_baseline_comparison()

    print("\n" + "="*60)
    if success:
        print("[PASS] BASELINE COMPARISON SPECIALIST VALIDATION: PASSED")
        print("="*60)
        print("\nNext steps:")
        print("1. Run full test suite: python -m pytest tests/test_baseline_comparison.py -v")
        print("2. Start services: bash run-local.sh")
        print("3. Test via dashboard: http://localhost:5173")
        print("\nOr test directly with curl:")
        print("   curl http://localhost:8006/baseline-comparison/.well-known/agent-card.json | jq")
    else:
        print("[FAIL] VALIDATION FAILED - Check output above for details")
        print("="*60)
        exit(1)
