# Baseline Comparison Feature - Quick Reference

## Overview

The baseline comparison feature enables the Lead Analyst to detect and analyze changes between prior assessments (baselines) and new analysis. It integrates ACH red team insights for confidence calibration and provides transparent appendices in the final output.

## Architecture

### Pipeline Position
```
receive → [specialists] → peripheral_scan → aggregate →
ach_red_team → baseline_comparison → final_synthesis → respond
```

**Why Sequential?**
- Baseline comparison receives ACH context for confidence calibration
- ACH challenges help distinguish high-confidence vs uncertain changes
- Sequential flow avoids analysis paralysis while leveraging ACH insights

### Key Components

1. **Baseline Comparison Specialist** (`agents/specialist_agent/agent_cards/baseline_comparison.yaml`)
   - Type: L2 meta-specialist (tagged `specialist_L2` to exclude from LLM discovery)
   - Input: Baseline + aggregated consensus + ACH analysis
   - Output: Structured JSON with confidence-calibrated change categories

2. **Graph Node** (`agents/lead_analyst/graph.py:652-726`)
   - Function: `call_baseline_comparison()`
   - Conditional: Only runs if `baselines` field is populated
   - Error handling: Graceful fallback with warning log

3. **Final Synthesis Enhancement** (`agents/lead_analyst/graph.py:741-835`)
   - Integrates baseline changes into narrative
   - Appends raw ACH and baseline comparison as appendices
   - Both LLM mode and fallback mode include appendices

## Output Structure

### Main Synthesis
```json
{
  "synthesis": "3-5 paragraph narrative integrating all perspectives",
  "baseline_change_summary": {
    "scope": "CHALLENGED (high confidence): Baseline X revised to Y",
    "timeline": "STABLE (uncertain): Baseline defended by ACH"
  },
  "key_takeaways": ["Insight 1", "Insight 2", "..."],
  "recommended_actions": ["Action 1", "Action 2", "..."]
}
```

### Appendix A: ACH Red Team Analysis (Raw Output)
```json
{
  "consensus_hypothesis": "H1: ...",
  "alternative_hypotheses": ["H2: ...", "H3: ...", "H4: ..."],
  "disconfirming_evidence": ["Evidence 1", "Evidence 2"],
  "pre_mortem": "What if we're wrong? ...",
  "silent_signals": ["Signal 1", "Signal 2"]
}
```

### Appendix B: Baseline Comparison Analysis (Raw Output)
```json
{
  "baseline_changes": {
    "confirmed": ["High confidence baseline points"],
    "confirmed_tentative": ["Supported BUT ACH raises doubts"],
    "challenged": ["High confidence contradictions"],
    "challenged_uncertain": ["Contradicted BUT ACH defends baseline"],
    "updated": ["Refined points"],
    "stable": ["Unchanged elements"],
    "new_insights": ["Beyond baseline scope"],
    "ach_meta_insights": ["Framing issues flagged by ACH"]
  },
  "confidence_assessment": {
    "overall": "High/Medium/Low",
    "ach_impact": "How ACH affects confidence",
    "high_confidence_changes": ["Change 1", "..."],
    "uncertain_changes": ["Change 2", "..."]
  },
  "change_magnitude": "Major/Moderate/Minor"
}
```

## Confidence Calibration Logic

### High Confidence
- **Confirmed**: Baseline + consensus agree AND ACH doesn't challenge
- **Challenged**: Baseline contradicted by consensus AND ACH agrees

### Uncertain/Tentative
- **Confirmed Tentative**: Baseline + consensus agree BUT ACH raises doubts
- **Challenged Uncertain**: Baseline contradicted BUT ACH defends baseline or questions consensus

### Meta-Insights
- ACH reveals that baseline-consensus framing is incomplete
- Requires revisiting assumptions or third alternative

## Use Cases

### Executive Briefing
- **Audience**: Senior decision-makers
- **Focus**: Read main synthesis, skim takeaways
- **Appendices**: Skip unless specific questions arise

### Analytical Review
- **Audience**: Peer analysts, QA
- **Focus**: Verify synthesis accuracy against raw outputs
- **Appendices**: Deep dive into ACH (Appendix A) and baseline comparison (Appendix B)

### Historical Analysis
- **Audience**: Future analysts, post-mortem
- **Focus**: Compare what was known vs what happened
- **Appendices**: Review baseline comparison to see prior state, ACH to see alternatives considered

## Testing

### Manual Testing
```bash
# Start services
OPENAI_API_KEY=sk-... python -m agents.lead_analyst.server
OPENAI_API_KEY=sk-... python -m agents.specialist_agent.server

# Submit task with baselines via dashboard
# Input: "Analyze the following..."
# Baselines: "Prior assessment: Risk level was high"
# Key Questions: "Has the risk changed?"

# Verify output includes:
# - Baseline comparison section with structured JSON
# - Final synthesis integrating baseline changes
# - Appendix A with raw ACH output
# - Appendix B with raw baseline comparison output
```

### Automated Testing
```bash
# Run baseline comparison tests
pytest tests/test_baseline_comparison.py -v

# Expected: 12/12 tests passing
# - Node invocation and skip logic
# - Input format and ACH context passing
# - Integration with final synthesis
# - Appendix inclusion
# - Error handling
```

## Key Files

| File | Purpose |
|------|---------|
| `agents/specialist_agent/agent_cards/baseline_comparison.yaml` | Specialist config |
| `agents/specialist_agent/prompts/baseline_comparison.md` | System prompt with confidence calibration rules |
| `agents/lead_analyst/graph.py` | Graph node (lines 652-726), final synthesis (lines 741-835) |
| `tests/test_baseline_comparison.py` | Comprehensive test suite (12 tests) |
| `agents/lead_analyst/README.md` | Pipeline documentation |
| `TESTING_BASELINE_COMPARISON.md` | Manual testing guide |
| `APPENDIX_ENHANCEMENT_SUMMARY.md` | Appendix feature documentation |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SPECIALIST_AGENT_URL` | `http://specialist-agent:8006` | Base URL for meta-specialists |
| `OPENAI_API_KEY` | None | Required for LLM-powered synthesis |
| `OPENAI_MODEL` | `gpt-4o-mini` | Model for aggregation and synthesis |

## Backward Compatibility

✅ **Fully backward compatible:**
- No baselines provided → baseline comparison skipped
- Existing tests pass without modification
- Output format is additive only (appendices)
- Error handling preserves pipeline flow

## Future Enhancements (Phase 2 - Deferred)

- Multi-document time-series analysis
- Weekly aggregation of baseline changes
- PostgreSQL time-range queries
- Historical comparison API endpoints
- Trend detection: improving/deteriorating/stable

## Quick Troubleshooting

**Baseline comparison not running?**
- Check that `baselines` field is populated in input
- Verify specialist agent is running on port 8006
- Check logs for "Baseline comparison skipped" message

**Appendices not appearing?**
- Verify `ach_analysis` and `baseline_comparison` are in state
- Check for error messages starting with `[Error calling`
- Ensure final synthesis completed successfully

**Confidence calibration not working?**
- Verify ACH analysis is passed to baseline comparison
- Check specialist prompt includes "Confidence Calibration" section
- Review test `test_baseline_comparison_receives_ach_context`

## Status

✅ **Phase 1 Complete** (Baseline Comparison with ACH Confidence Calibration + Appendices)
- Specialist agent created and tested
- Graph node integrated and wired
- Confidence calibration implemented
- Appendices added to final synthesis
- All tests passing (12/12)
- Documentation updated

⏸️ **Phase 2 Deferred** (Multi-Document Time-Series Analysis)
- Awaiting user request to proceed
