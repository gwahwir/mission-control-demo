# Baseline Comparison - Quick Start Guide

## What is Baseline Comparison?

The baseline comparison specialist detects changes between an original assessment and new analysis, with ACH-aware confidence calibration. It identifies what's been confirmed, challenged, updated, or remains stable.

---

## How to Use

### Via Dashboard (Recommended)

1. **Start the stack:**
   ```bash
   bash run-local.sh
   ```

2. **Open dashboard:** http://localhost:5173

3. **Navigate to Lead Analyst A**

4. **Fill in the form:**
   - **Scenario/Question:** Your analysis request
   - **Current Baseline Assessments:** Paste your prior assessment
   - **Key Questions:** What you want to know
   - **Submit**

5. **Watch the pipeline execute:**
   - Specialists analyze
   - ACH red team challenges
   - **Baseline comparison detects changes** ← New!
   - Final synthesis integrates everything

6. **Review output:**
   - Look for "BASELINE CHANGE ANALYSIS" section
   - Check confirmed/challenged/updated points
   - Note confidence levels (influenced by ACH)

---

### Via API

```bash
# Submit task with baselines
curl -X POST http://localhost:8000/agents/lead-analyst-a/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "input": "Analyze recent developments in semiconductor supply chains",
    "baselines": "Prior assessment (Q4 2025): US export controls were narrow, China 3-5 years behind on advanced nodes, limited decoupling in non-sensitive tech",
    "key_questions": "Has the tech decoupling accelerated? Have export controls expanded? Has China closed the gap?"
  }'

# Response: {"task_id": "task-abc123", "state": "submitted"}

# Poll for completion
curl http://localhost:8000/tasks/task-abc123 | jq

# View output
curl http://localhost:8000/tasks/task-abc123 | jq '.output'
```

---

## Input Format

### Minimal Example
```json
{
  "input": "Analyze current situation",
  "baselines": "Prior assessment: X was true, Y was expected"
}
```

### Full Example
```json
{
  "input": "Analyze current geopolitical dynamics in Southeast Asia",
  "baselines": "Prior assessment (3 months ago):\n- ASEAN unity on SCS was holding\n- US-China competition was moderate\n- Economic integration continued despite tensions\n- Regional forums remained active",
  "key_questions": "Has ASEAN cohesion weakened? Has US-China competition intensified? Are there signs of economic decoupling?"
}
```

---

## Output Structure

```json
{
  "framework_name": "Baseline Change Detection",
  "summary": "Executive summary with confidence qualifiers",
  "baseline_changes": {
    "confirmed": ["Points validated by new analysis"],
    "confirmed_tentative": ["Points supported BUT ACH raises doubts"],
    "challenged": ["Points contradicted with high confidence"],
    "challenged_uncertain": ["Points contradicted BUT ACH questions consensus"],
    "updated": ["Points refined or adjusted"],
    "stable": ["Key continuities - what hasn't changed"],
    "new_insights": ["New findings beyond baseline scope"],
    "ach_meta_insights": ["Where both baseline AND consensus miss fundamentals"]
  },
  "change_magnitude": "Major|Moderate|Minor",
  "key_deltas": ["Top 3-5 significant changes"],
  "confidence_assessment": {
    "overall": "High|Medium|Low",
    "ach_impact": "How ACH challenges affect confidence",
    "high_confidence_changes": ["Changes well-supported"],
    "uncertain_changes": ["Changes where ACH creates doubt"]
  }
}
```

---

## When to Use Baselines

### ✅ Good Use Cases

1. **Monitoring ongoing situations**
   - Track how assessments evolve over weeks/months
   - Example: "Update our Q4 assessment of US-China tech competition"

2. **Validating predictions**
   - Check if forecasts materialized
   - Example: "We predicted escalation in 6 months. Did it happen?"

3. **Detecting blind spots**
   - Find what changed that you didn't anticipate
   - Example: "Our baseline said X was stable. What actually shifted?"

4. **Calibrating confidence**
   - Measure how often your assessments hold up
   - Example: "Were we right about the risk level?"

### ❌ Don't Use Baselines When

1. **First-time analysis** - No prior assessment exists
2. **Different topic** - Baseline covers unrelated subject
3. **Too old** - Baseline from 6+ months ago may be irrelevant
4. **Too vague** - Baseline lacks specific claims to compare

---

## Skip Logic

The baseline comparison **automatically skips** when:
- `baselines` field is empty or missing
- `baselines` is null or whitespace-only
- No error is raised - task continues normally

This means you can always include the baselines field in your template. It only activates when populated.

---

## ACH Integration

The baseline comparison uses ACH red team analysis to calibrate confidence:

### High Confidence Changes
- Both consensus AND ACH support the change
- ACH provides additional evidence
- No alternative hypotheses challenge this point

### Low Confidence Changes
- Consensus contradicts baseline BUT ACH defends it
- ACH identifies blind spots affecting the comparison
- Alternative hypotheses suggest the framing is incomplete

### Example

**Baseline:** "Economic growth will remain strong at 5%"

**Consensus:** "Growth has slowed to 2% - baseline challenged"

**ACH:** "H2 (Recession) suggests even consensus is optimistic. Leading indicators show contraction risk."

**Result:**
- Change classification: `challenged` (baseline was wrong)
- Confidence: `uncertain_changes` (ACH suggests consensus may also be wrong)
- Output: "Baseline growth projection challenged, BUT consensus 2% estimate may be optimistic per ACH recession hypothesis"

---

## Tips

1. **Be specific in baselines**
   - Good: "US-China military encounters: 2 per quarter, risk level: moderate"
   - Bad: "Tensions were okay"

2. **Include timelines**
   - Good: "Assessment from Q4 2025: ..."
   - Bad: "Previous assessment: ..."

3. **Note uncertainties**
   - Good: "Growth forecast: 4-6% (high confidence), inflation: 2-3% (uncertain)"
   - Bad: "Growth: 5%, inflation: 2.5%"

4. **Use key questions**
   - Guide the comparison toward what matters most
   - Example: "Has the risk level changed from moderate to high?"

5. **Check ACH impact**
   - Look at `ach_impact` field in output
   - High ACH impact = more uncertainty in baseline changes

---

## Troubleshooting

### "baseline_comparison field is empty"

**Check:**
- Is `baselines` field non-empty in your input?
- Are services running? `docker compose ps` or check dashboard
- Check logs: `docker compose logs lead-analyst | grep baseline`

### "Error calling baseline_comparison"

**Causes:**
- Specialist agent not running or not registered
- OpenAI API key missing or invalid
- Network connectivity issue

**Fix:**
```bash
# Restart specialist agent
docker compose restart specialist-agent

# Check registration
curl http://localhost:8000/agents | jq '.[] | select(.type_id | contains("baseline"))'

# Verify OpenAI key is set
echo $OPENAI_API_KEY
```

### "Output doesn't include baseline changes"

**If baselines were provided:**
- Check task `node_outputs.call_baseline_comparison` field
- Look for error messages in control plane logs
- Verify specialist agent is registered at startup

**If baselines were empty:**
- This is expected behavior - comparison skips when no baselines

---

## Testing

### Run Test Suite
```bash
python -m pytest tests/test_baseline_comparison.py -v
```

### Validate Configuration
```bash
python test_baseline_manual.py
```

### Test Direct Specialist Call
```bash
curl -X POST http://localhost:8006/baseline-comparison/ \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "message/send",
    "params": {
      "message": {
        "text": "## BASELINE ASSESSMENTS:\nPrior: X was true\n\n---\n## NEW ANALYSIS:\nCurrent: Y is now true\n\n---\n## YOUR TASK:\nCompare these assessments."
      }
    },
    "id": 1
  }' | jq
```

---

## Example Session

**Input:**
```json
{
  "input": "Analyze current state of US-China tech competition focusing on semiconductors and AI",
  "baselines": "Prior assessment (6 months ago):\n- US export controls targeted advanced chips (7nm and below)\n- China 3-5 years behind on leading-edge nodes\n- Decoupling limited to semiconductors\n- Tech cooperation continued in climate tech, biotech",
  "key_questions": "Have export controls expanded? Has China narrowed the gap? Is decoupling spreading beyond semiconductors?"
}
```

**Output Excerpt:**
```json
{
  "framework_name": "Baseline Change Detection",
  "summary": "Major shift from baseline. Export controls have expanded significantly beyond semiconductors. China has accelerated domestic development (now 2-3 years behind per some estimates). Tech decoupling is spreading to AI, quantum, biotech. However, ACH analysis suggests consensus may overestimate China's progress due to opacity.",
  "baseline_changes": {
    "challenged": [
      "Baseline claim 'export controls targeted only advanced chips' is now false - controls expanded to include AI chips, quantum tech, and biotech tools"
    ],
    "updated": [
      "Gap estimate narrowed from '3-5 years' to '2-3 years' BUT with high uncertainty per ACH"
    ],
    "confirmed_tentative": [
      "Decoupling spread beyond semiconductors - confirmed BUT ACH notes this was predictable, baseline may have been too optimistic"
    ],
    "new_insights": [
      "Emergence of China-only AI supply chain parallel to Western systems"
    ]
  },
  "change_magnitude": "Major",
  "confidence_assessment": {
    "overall": "Medium",
    "ach_impact": "ACH challenges raise doubt about consensus estimates of China's progress - opacity makes verification difficult",
    "high_confidence_changes": ["Export control expansion - well documented"],
    "uncertain_changes": ["China's actual technological gap - ACH notes evidence is ambiguous"]
  }
}
```

---

## Learn More

- [BASELINE_TEST_RESULTS.md](BASELINE_TEST_RESULTS.md) - Complete test validation
- [TESTING_BASELINE_COMPARISON.md](TESTING_BASELINE_COMPARISON.md) - Manual testing guide
- [BASELINE_COMPARISON_REFERENCE.md](BASELINE_COMPARISON_REFERENCE.md) - Implementation details
- [agents/specialist_agent/README.md](agents/specialist_agent/README.md) - Specialist architecture
