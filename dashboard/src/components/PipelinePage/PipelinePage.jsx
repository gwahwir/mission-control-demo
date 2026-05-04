import { useState, useEffect, useRef, useCallback } from "react";
import { Alert, Badge, Group, Text, Box } from "@mantine/core";
import PipelineForm from "./PipelineForm";
import PipelineGraph from "./PipelineGraph";
import PipelineOutputPanel from "./PipelineOutputPanel";
import {
  dispatchTask,
  fetchBaseline,
  fetchTask,
  ensureTopicRegistered,
  writeBaselineVersion,
  writeBaselineDelta,
  subscribeToTask,
} from "../../hooks/useApi";

const RELEVANCE_THRESHOLD = 0.5;
const POLL_INTERVAL_MS = 800;
const TERMINAL_STATES = new Set(["completed", "failed", "canceled"]);

// ── Helpers ───────────────────────────────────────────────────────────────────

function extractTextFromTask(task) {
  // Control plane stores final output in output_text
  if (task?.output_text) return task.output_text;
  // Fallback: A2A message parts format
  for (const part of task?.status?.message?.parts ?? []) {
    if (part.kind === "text") return part.text;
  }
  return "";
}

function extractSection(lines, marker) {
  const idx = lines.findIndex((l) => l.toLowerCase().includes(marker.toLowerCase()));
  if (idx === -1) return null;
  const section = [];
  for (let i = idx + 1; i < lines.length; i++) {
    if (lines[i].startsWith("## ") && section.length) break;
    section.push(lines[i]);
  }
  return section.join("\n").trim() || null;
}

function extractUpdatedNarrative(analysisText) {
  const lines = analysisText.split("\n");
  const primary = extractSection(lines, "## Primary Assessment");
  const changes = extractSection(lines, "## Baseline Change Summary");
  if (primary || changes) {
    const parts = [];
    if (primary) parts.push(`## Primary Assessment\n${primary}`);
    if (changes) parts.push(`## Baseline Change Summary\n${changes}`);
    return parts.join("\n\n");
  }
  // Fallback: first 3000 chars
  return analysisText.slice(0, 3000);
}

function extractDeltaFields(analysisText) {
  const lines = analysisText.split("\n");
  let sectionText = "";
  for (const marker of ["Baseline Change Summary", "Baseline Comparison", "Appendix: Baseline"]) {
    const idx = lines.findIndex((l) => l.toLowerCase().includes(marker.toLowerCase()));
    if (idx !== -1) {
      const section = [];
      for (let i = idx + 1; i < lines.length; i++) {
        if (lines[i].startsWith("# ") && section.length) break;
        section.push(lines[i]);
      }
      sectionText = section.join("\n").trim();
      break;
    }
  }
  const source = sectionText || analysisText;
  const paras = source.split("\n\n");
  const firstPara = paras.find((p) => p.trim().length > 40) ?? "";
  const deltaSummary = firstPara.trim().slice(0, 300) || "Baseline updated following new report analysis.";

  const claimsAdded = [];
  const claimsSuperseded = [];
  for (const line of source.split("\n")) {
    const stripped = line.replace(/^[•\-* ]+/, "").trim();
    if (!stripped || stripped.length < 20) continue;
    const lower = stripped.toLowerCase();
    if (["confirmed", "updated", "new signal", "new development", "added"].some((kw) => lower.includes(kw))) {
      claimsAdded.push(stripped);
    } else if (["challenged", "superseded", "no longer", "reversed", "contradicted"].some((kw) => lower.includes(kw))) {
      claimsSuperseded.push(stripped);
    }
  }
  return {
    deltaSummary,
    claimsAdded: claimsAdded.slice(0, 10),
    claimsSuperseded: claimsSuperseded.slice(0, 10),
  };
}

// ── Status bar ────────────────────────────────────────────────────────────────

const STATE_LABELS = {
  idle:               "IDLE — ready to run",
  fetching_baseline:  "FETCHING BASELINE...",
  checking_relevance: "ASSESSING RELEVANCE...",
  running_analyst:    "LEAD ANALYST RUNNING...",
  writing_baseline:   "WRITING BASELINE...",
  done:               "PIPELINE COMPLETE",
  skipped:            "REPORT NOT RELEVANT — skipped analysis",
  error:              "ERROR",
};

const STATE_COLORS = {
  idle: "gray",
  fetching_baseline: "hud-amber",
  checking_relevance: "hud-amber",
  running_analyst: "hud-amber",
  writing_baseline: "hud-amber",
  done: "hud-green",
  skipped: "hud-violet",
  error: "hud-red",
};

function StatusBar({ runState, error, agentsReady }) {
  if (!agentsReady) {
    return (
      <Alert color="hud-amber" variant="light" radius={0} mb="sm" styles={{ root: { fontFamily: "monospace", fontSize: 12 } }}>
        Waiting for relevancy agent and lead analyst to come online...
      </Alert>
    );
  }
  return (
    <Group mb="sm" gap="xs">
      <Badge
        color={STATE_COLORS[runState] ?? "gray"}
        variant={runState === "idle" ? "outline" : "light"}
        radius={0}
        styles={{ root: { fontFamily: "monospace", letterSpacing: "1px", fontSize: 11 } }}
      >
        {STATE_LABELS[runState] ?? runState.toUpperCase()}
      </Badge>
      {error && (
        <Text size="xs" style={{ color: "var(--hud-red)", fontFamily: "monospace" }}>
          {error}
        </Text>
      )}
    </Group>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function PipelinePage({ agents, onAnalystTaskStarted, onAnalystTaskState, onBaselineWritten }) {
  const [runState, setRunState] = useState("idle");
  const [stageOutputs, setStageOutputs] = useState({
    baseline: undefined,   // undefined = not loaded, null = no baseline, object = loaded
    relevance: null,
    analyst: null,
    baselineWrite: null,
  });
  const [analystTaskState, setAnalystTaskState] = useState(null);
  const [selectedNodeId, setSelectedNodeId] = useState(null);
  const [error, setError] = useState(null);

  // Hold topic/version across run for write-back
  const runParamsRef = useRef(null);
  // Cleanup ref for WS subscription
  const wsCleanupRef = useRef(null);
  // Stable ref to onAnalystTaskState to avoid stale closure in useCallback
  const onAnalystTaskStateRef = useRef(onAnalystTaskState);
  useEffect(() => { onAnalystTaskStateRef.current = onAnalystTaskState; }, [onAnalystTaskState]);

  const relevancyAgent = agents.find((a) => a.id === "relevancy" && a.status === "online");
  const leadAnalystAgent = agents.find(
    (a) => a.id?.startsWith("lead-analyst") && a.status === "online"
  );
  const agentsReady = !!(relevancyAgent && leadAnalystAgent);

  // Cleanup WS on unmount
  useEffect(() => () => wsCleanupRef.current?.(), []);

  const handleRun = useCallback(
    async ({ topic, topicLabel, report, keyQuestions, baselineOverride }) => {
      if (!agentsReady || runState !== "idle") return;

      setError(null);
      setAnalystTaskState(null);
      setStageOutputs({ baseline: undefined, relevance: null, analyst: null, baselineWrite: null });
      setSelectedNodeId(null);
      runParamsRef.current = { topic, topicLabel, report, keyQuestions };

      try {
        // ── Step 1: Fetch baseline ────────────────────────────────────────────
        setRunState("fetching_baseline");
        await ensureTopicRegistered(topic, topicLabel);
        const baseline = baselineOverride
          ? { narrative: baselineOverride, version_number: null }
          : await fetchBaseline(topic);
        setStageOutputs((s) => ({ ...s, baseline: baseline ?? null }));

        // ── Step 2: Relevancy check ───────────────────────────────────────────
        setRunState("checking_relevance");

        const fullQuestion = baseline
          ? `${keyQuestions}\n\nCurrent baseline context:\n${baseline.narrative.slice(0, 1500)}`
          : keyQuestions;

        const relTask = await dispatchTask(relevancyAgent.id, {
          text: JSON.stringify({ text: report, question: fullQuestion }),
        });

        // Poll until terminal
        let relResult = relTask;
        while (!TERMINAL_STATES.has(relResult.state)) {
          await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
          relResult = await fetchTask(relevancyAgent.id, relTask.task_id);
        }

        const relText = extractTextFromTask(relResult);
        let relevance = { relevant: false, confidence: 0, reasoning: relText, error: true };
        try { relevance = JSON.parse(relText); } catch {}

        setStageOutputs((s) => ({ ...s, relevance }));

        if (!relevance.relevant || (relevance.confidence ?? 0) < RELEVANCE_THRESHOLD) {
          setRunState("skipped");
          return;
        }

        // ── Step 3: Lead analyst ──────────────────────────────────────────────
        setRunState("running_analyst");

        const analystTask = await dispatchTask(leadAnalystAgent.id, {
          text: JSON.stringify({
            text: report,
            baselines: baseline?.narrative ?? "",
            key_questions: keyQuestions,
          }),
        });

        setStageOutputs((s) => ({ ...s, analyst: analystTask }));
        onAnalystTaskStarted?.(analystTask.task_id, leadAnalystAgent.id);

        // Subscribe to WebSocket for live node updates
        wsCleanupRef.current?.();
        wsCleanupRef.current = subscribeToTask(analystTask.task_id, (msg) => {
          setAnalystTaskState(msg);
          onAnalystTaskStateRef.current?.(msg);
        });

        // Also poll as fallback until terminal (WS may miss final state)
        let analystResult = analystTask;
        while (!TERMINAL_STATES.has(analystResult.state)) {
          await new Promise((r) => setTimeout(r, 2000));
          analystResult = await fetchTask(leadAnalystAgent.id, analystTask.task_id);
          setAnalystTaskState((prev) => {
            const next = { ...(prev ?? {}), ...analystResult };
            onAnalystTaskStateRef.current?.(next);
            return next;
          });
        }

        wsCleanupRef.current?.();
        wsCleanupRef.current = null;

        if (analystResult.state === "failed") {
          throw new Error("Lead analyst task failed");
        }

        const analysisText = extractTextFromTask(analystResult);

        // ── Step 4: Write baseline ────────────────────────────────────────────
        setRunState("writing_baseline");

        const newNarrative = extractUpdatedNarrative(analysisText);
        const { deltaSummary, claimsAdded, claimsSuperseded } = extractDeltaFields(analysisText);

        const versionResult = await writeBaselineVersion(topic, newNarrative);
        await writeBaselineDelta(topic, {
          fromVersion: baseline?.version_number ?? null,
          toVersion: versionResult.version_number,
          deltaSummary,
          claimsAdded,
          claimsSuperseded,
        });

        setStageOutputs((s) => ({
          ...s,
          baselineWrite: {
            fromVersion: baseline?.version_number ?? null,
            toVersion: versionResult.version_number,
            deltaSummary,
            claimsAdded,
            claimsSuperseded,
          },
        }));

        setRunState("done");
        onBaselineWritten?.();
      } catch (err) {
        console.error("Pipeline error:", err);
        setError(err.message ?? String(err));
        setRunState("error");
      }
    },
    [agentsReady, runState, relevancyAgent, leadAnalystAgent]
  );

  const isRunning = !["idle", "done", "skipped", "error"].includes(runState);

  const handleReset = () => {
    wsCleanupRef.current?.();
    wsCleanupRef.current = null;
    setRunState("idle");
    setError(null);
    setAnalystTaskState(null);
    setStageOutputs({ baseline: undefined, relevance: null, analyst: null, baselineWrite: null });
    setSelectedNodeId(null);
  };

  return (
    <Box style={{ display: "flex", flexDirection: "column", height: "calc(100vh - 180px)", gap: 12 }}>
      {/* Form */}
      <Box
        style={{
          background: "var(--hud-bg-panel)",
          border: "1px solid var(--hud-border)",
          padding: "12px 16px",
          flexShrink: 0,
        }}
      >
        <Group justify="space-between" mb="sm">
          <Text
            size="xs"
            style={{
              color: "var(--hud-cyan)",
              letterSpacing: "2px",
              textTransform: "uppercase",
              fontSize: 11,
              fontFamily: "monospace",
            }}
          >
            ◈ PIPELINE INPUT
          </Text>
          {(runState === "done" || runState === "skipped" || runState === "error") && (
            <Text
              size="xs"
              style={{
                color: "var(--hud-text-dimmed)",
                cursor: "pointer",
                fontFamily: "monospace",
                fontSize: 11,
                letterSpacing: "1px",
              }}
              onClick={handleReset}
            >
              [ RESET ]
            </Text>
          )}
        </Group>
        <StatusBar runState={runState} error={error} agentsReady={agentsReady} />
        <PipelineForm onRun={handleRun} isRunning={isRunning} />
      </Box>

      {/* Graph + Output panel */}
      <Box style={{ display: "flex", flex: 1, gap: 0, minHeight: 0 }}>
        {/* Left: graph */}
        <Box
          style={{
            flex: "0 0 62%",
            border: "1px solid var(--hud-border)",
            background: "var(--hud-bg-deep)",
            minHeight: 0,
          }}
        >
          <PipelineGraph
            runState={runState}
            analystTaskState={analystTaskState}
            selectedNodeId={selectedNodeId}
            onNodeSelect={setSelectedNodeId}
          />
        </Box>

        {/* Right: output panel */}
        <Box style={{ flex: "0 0 38%", minHeight: 0, overflowY: "auto" }}>
          <PipelineOutputPanel
            selectedNodeId={selectedNodeId}
            stageOutputs={stageOutputs}
            runState={runState}
            analystTaskState={analystTaskState}
            onClose={() => setSelectedNodeId(null)}
          />
        </Box>
      </Box>
    </Box>
  );
}
