import { Text, Badge, Stack, Group, Box, Divider } from "@mantine/core";
import NodeOutputPanel from "../TaskGraphModal/NodeOutputPanel";

// Outer stage node IDs
const OUTER_NODES = new Set(["baseline_fetch", "relevancy_check", "baseline_write"]);

// Analyst internal node IDs (sequential)
const ANALYST_NODES = new Set([
  "discover_and_select",
  "call_specialist",
  "call_peripheral_scan",
  "aggregate",
  "call_ach_red_team",
  "call_baseline_comparison",
  "final_synthesis",
]);

function dim(text) {
  return <Text size="xs" style={{ color: "var(--hud-text-dimmed)" }}>{text}</Text>;
}

function Label({ children }) {
  return (
    <Text
      size="xs"
      style={{
        color: "var(--hud-text-dimmed)",
        letterSpacing: "1px",
        textTransform: "uppercase",
        fontSize: 10,
        marginBottom: 4,
      }}
    >
      {children}
    </Text>
  );
}

function Field({ label, children }) {
  return (
    <Box>
      <Label>{label}</Label>
      {children}
    </Box>
  );
}

// ── Outer stage summary cards ─────────────────────────────────────────────────

function BaselineFetchCard({ stageOutputs, runState }) {
  const baseline = stageOutputs.baseline;
  const isRunning = runState === "fetching_baseline";
  const isDone = baseline !== undefined; // null = no baseline yet, object = loaded

  return (
    <Stack gap="sm">
      <Group justify="space-between">
        <Text size="xs" fw={600} style={{ color: "var(--hud-cyan)", letterSpacing: "1px", textTransform: "uppercase" }}>
          [ BASELINE FETCH ] OUTPUT
        </Text>
        {isRunning && <Badge color="hud-amber" variant="light" size="xs">FETCHING</Badge>}
        {isDone && !isRunning && <Badge color="hud-green" variant="light" size="xs">DONE</Badge>}
      </Group>
      <Divider color="var(--hud-border)" />
      {isRunning && dim("Fetching current baseline...")}
      {!isRunning && !isDone && dim("Not yet fetched.")}
      {!isRunning && isDone && baseline === null && (
        <Badge color="hud-amber" variant="light">No baseline exists yet — first run</Badge>
      )}
      {!isRunning && isDone && baseline && (
        <>
          <Field label="Topic">
            <Text size="xs" style={{ color: "var(--hud-text-primary)", fontFamily: "monospace" }}>{baseline.topic_path}</Text>
          </Field>
          <Field label="Version">
            <Badge color="hud-cyan" variant="outline" size="sm">v{baseline.version_number}</Badge>
          </Field>
          <Field label="Narrative">
            <Box
              style={{
                background: "var(--hud-bg-surface)",
                border: "1px solid var(--hud-border)",
                padding: "8px 10px",
                fontSize: 12,
                lineHeight: 1.7,
                color: "var(--hud-text-primary)",
                maxHeight: 300,
                overflowY: "auto",
                fontFamily: "monospace",
                whiteSpace: "pre-wrap",
              }}
            >
              {baseline.narrative}
            </Box>
          </Field>
          {baseline.created_at && (
            <Field label="Version date">
              <Text size="xs" style={{ color: "var(--hud-text-dimmed)", fontFamily: "monospace" }}>
                {baseline.created_at}
              </Text>
            </Field>
          )}
        </>
      )}
    </Stack>
  );
}

function RelevancyCard({ stageOutputs, runState }) {
  const relevance = stageOutputs.relevance;
  const isRunning = runState === "checking_relevance";

  return (
    <Stack gap="sm">
      <Group justify="space-between">
        <Text size="xs" fw={600} style={{ color: "var(--hud-cyan)", letterSpacing: "1px", textTransform: "uppercase" }}>
          [ RELEVANCY CHECK ] OUTPUT
        </Text>
        {isRunning && <Badge color="hud-amber" variant="light" size="xs">ASSESSING</Badge>}
        {relevance && !isRunning && (
          <Badge color={relevance.relevant ? "hud-green" : "hud-red"} variant="light" size="xs">
            {relevance.relevant ? "RELEVANT" : "NOT RELEVANT"}
          </Badge>
        )}
      </Group>
      <Divider color="var(--hud-border)" />
      {isRunning && dim("Calling relevancy agent...")}
      {!isRunning && !relevance && dim("Not yet assessed.")}
      {relevance && !isRunning && (
        <>
          <Group gap="md">
            <Field label="Verdict">
              <Badge
                color={relevance.relevant ? "hud-green" : "hud-red"}
                variant={relevance.relevant ? "filled" : "outline"}
                size="md"
              >
                {relevance.relevant ? "RELEVANT" : "NOT RELEVANT"}
              </Badge>
            </Field>
            <Field label="Confidence">
              <Text size="sm" fw={700} style={{ color: relevance.relevant ? "var(--hud-green)" : "var(--hud-red)", fontFamily: "monospace" }}>
                {Math.round((relevance.confidence ?? 0) * 100)}%
              </Text>
            </Field>
          </Group>
          <Field label="Reasoning">
            <Box
              style={{
                background: "var(--hud-bg-surface)",
                border: "1px solid var(--hud-border)",
                padding: "8px 10px",
                fontSize: 12,
                lineHeight: 1.7,
                color: "var(--hud-text-primary)",
                fontFamily: "monospace",
                whiteSpace: "pre-wrap",
              }}
            >
              {relevance.reasoning}
            </Box>
          </Field>
        </>
      )}
    </Stack>
  );
}

function BaselineWriteCard({ stageOutputs, runState }) {
  const bw = stageOutputs.baselineWrite;
  const isRunning = runState === "writing_baseline";

  return (
    <Stack gap="sm">
      <Group justify="space-between">
        <Text size="xs" fw={600} style={{ color: "var(--hud-cyan)", letterSpacing: "1px", textTransform: "uppercase" }}>
          [ BASELINE WRITE ] OUTPUT
        </Text>
        {isRunning && <Badge color="hud-amber" variant="light" size="xs">WRITING</Badge>}
        {bw && !isRunning && <Badge color="hud-green" variant="light" size="xs">SAVED</Badge>}
      </Group>
      <Divider color="var(--hud-border)" />
      {isRunning && dim("Writing updated baseline...")}
      {!isRunning && !bw && dim("Not yet written.")}
      {bw && !isRunning && (
        <>
          <Group gap="md">
            <Field label="Previous version">
              <Badge color="hud-amber" variant="outline" size="sm">
                {bw.fromVersion != null ? `v${bw.fromVersion}` : "—"}
              </Badge>
            </Field>
            <Text style={{ color: "var(--hud-text-dimmed)", fontSize: 16, alignSelf: "flex-end", marginBottom: 2 }}>→</Text>
            <Field label="New version">
              <Badge color="hud-green" variant="filled" size="sm">v{bw.toVersion}</Badge>
            </Field>
          </Group>
          {bw.deltaSummary && (
            <Field label="Delta summary">
              <Box
                style={{
                  background: "var(--hud-bg-surface)",
                  border: "1px solid var(--hud-border)",
                  padding: "8px 10px",
                  fontSize: 12,
                  lineHeight: 1.7,
                  color: "var(--hud-text-primary)",
                  fontFamily: "monospace",
                  whiteSpace: "pre-wrap",
                }}
              >
                {bw.deltaSummary}
              </Box>
            </Field>
          )}
          {bw.claimsAdded?.length > 0 && (
            <Field label={`Claims added (${bw.claimsAdded.length})`}>
              <Stack gap={4}>
                {bw.claimsAdded.map((c, i) => (
                  <Text key={i} size="xs" style={{ color: "var(--hud-green)", fontFamily: "monospace" }}>
                    + {c}
                  </Text>
                ))}
              </Stack>
            </Field>
          )}
          {bw.claimsSuperseded?.length > 0 && (
            <Field label={`Claims superseded (${bw.claimsSuperseded.length})`}>
              <Stack gap={4}>
                {bw.claimsSuperseded.map((c, i) => (
                  <Text key={i} size="xs" style={{ color: "var(--hud-red)", fontFamily: "monospace" }}>
                    - {c}
                  </Text>
                ))}
              </Stack>
            </Field>
          )}
        </>
      )}
    </Stack>
  );
}

// ── Main panel ────────────────────────────────────────────────────────────────

export default function PipelineOutputPanel({ selectedNodeId, stageOutputs, runState, analystTaskState, onClose }) {
  const nodeOutputs = analystTaskState?.node_outputs ?? {};
  const runningNode = analystTaskState?.running_node ?? null;
  const taskFailed = analystTaskState?.state === "failed";
  const taskError = analystTaskState?.error ?? null;

  const containerStyle = {
    height: "100%",
    overflowY: "auto",
    padding: 16,
    background: "var(--hud-bg-panel)",
    borderLeft: "1px solid var(--hud-border)",
  };

  if (!selectedNodeId) {
    return (
      <div style={containerStyle}>
        <Text
          size="xs"
          style={{
            color: "var(--hud-text-dimmed)",
            letterSpacing: "1px",
            textTransform: "uppercase",
            marginTop: 40,
            textAlign: "center",
            display: "block",
          }}
        >
          Click a node to inspect its output
        </Text>
      </div>
    );
  }

  // ── Outer stage cards ─────────────────────────────────────────────────────
  if (selectedNodeId === "baseline_fetch") {
    return (
      <div style={containerStyle}>
        <BaselineFetchCard stageOutputs={stageOutputs} runState={runState} />
      </div>
    );
  }
  if (selectedNodeId === "relevancy_check") {
    return (
      <div style={containerStyle}>
        <RelevancyCard stageOutputs={stageOutputs} runState={runState} />
      </div>
    );
  }
  if (selectedNodeId === "baseline_write") {
    return (
      <div style={containerStyle}>
        <BaselineWriteCard stageOutputs={stageOutputs} runState={runState} />
      </div>
    );
  }

  // ── Analyst internal nodes → delegate to NodeOutputPanel ─────────────────
  const nodeOutputJson = selectedNodeId in nodeOutputs ? nodeOutputs[selectedNodeId] : undefined;

  let nodeState = "pending";
  if (runningNode === selectedNodeId) nodeState = "running";
  else if (selectedNodeId in nodeOutputs) nodeState = "completed";
  else if (taskFailed) nodeState = "failed";

  return (
    <div style={containerStyle}>
      <NodeOutputPanel
        nodeId={selectedNodeId}
        nodeOutputJson={nodeOutputJson}
        nodeState={nodeState}
        taskError={taskError}
        onClose={onClose}
      />
    </div>
  );
}
