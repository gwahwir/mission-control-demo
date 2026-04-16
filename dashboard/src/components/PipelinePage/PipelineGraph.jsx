import { useMemo } from "react";
import { ReactFlow, Background, Controls, Handle, Position, MarkerType } from "@xyflow/react";
import "@xyflow/react/dist/style.css";

const STATE_STYLES = {
  pending:   { background: "#0d1117", border: "1px solid #374151", color: "#6b7280", opacity: 0.5, boxShadow: "none" },
  running:   { background: "#1a1200", border: "1px solid #f59e0b", color: "#fbbf24", opacity: 1,   boxShadow: "0 0 12px rgba(245,158,11,0.5)" },
  completed: { background: "#0a1a0a", border: "1px solid #22c55e", color: "#4ade80", opacity: 1,   boxShadow: "none" },
  failed:    { background: "#1a0505", border: "1px solid #ef4444", color: "#f87171", opacity: 1,   boxShadow: "none" },
  selected:  { background: "#001a2a", border: "2px solid #00d4ff", color: "#00d4ff", opacity: 1,   boxShadow: "0 0 14px rgba(0,212,255,0.3)" },
};

const DOT_COLORS = {
  running: "#f59e0b",
  completed: "#22c55e",
  failed: "#ef4444",
  selected: "#00d4ff",
};

// Fan-out keys: "call_specialist:N"
const FAN_OUT_RE = /^(.+):(\d+)$/;

function PipelineNode({ data }) {
  const style = STATE_STYLES[data.executionState] || STATE_STYLES.pending;
  const dotColor = DOT_COLORS[data.executionState];
  const handleStyle = { background: "var(--hud-cyan)", width: 6, height: 6 };

  return (
    <div
      style={{
        ...style,
        borderRadius: 0,
        minWidth: 150,
        padding: "7px 14px",
        fontFamily: "monospace",
        fontSize: 11,
        letterSpacing: "0.5px",
        textTransform: "uppercase",
        display: "flex",
        alignItems: "center",
        gap: 6,
        cursor: "pointer",
        ...(data.isFanOut && { borderLeft: "3px solid rgba(0,212,255,0.35)" }),
        ...(data.isGroup && {
          fontSize: 10,
          letterSpacing: "1.5px",
          opacity: data.executionState === "pending" ? 0.35 : 1,
        }),
      }}
    >
      <Handle type="target" position={Position.Left} style={handleStyle} />
      {dotColor && (
        <span
          style={{
            display: "inline-block",
            width: 6,
            height: 6,
            borderRadius: "50%",
            backgroundColor: dotColor,
            flexShrink: 0,
          }}
        />
      )}
      <span style={{ flex: 1 }}>{data.label}</span>
      <Handle type="source" position={Position.Right} style={handleStyle} />
    </div>
  );
}

// Group container node (Lead Analyst box)
function GroupNode({ data }) {
  const borderColor =
    data.executionState === "running"
      ? "#f59e0b"
      : data.executionState === "completed"
      ? "#22c55e"
      : data.executionState === "failed"
      ? "#ef4444"
      : "#1e2a3a";

  const glowColor =
    data.executionState === "running"
      ? "rgba(245,158,11,0.2)"
      : data.executionState === "completed"
      ? "rgba(34,197,94,0.1)"
      : "transparent";

  return (
    <div
      style={{
        width: data.width,
        height: data.height,
        border: `1px solid ${borderColor}`,
        background: "rgba(0,212,255,0.02)",
        boxShadow: `0 0 20px ${glowColor}`,
        borderRadius: 0,
        position: "relative",
      }}
    >
      <div
        style={{
          position: "absolute",
          top: -1,
          left: 12,
          background: "var(--hud-bg-deep)",
          padding: "0 6px",
          fontSize: 10,
          letterSpacing: "1.5px",
          textTransform: "uppercase",
          color: borderColor,
          fontFamily: "monospace",
          transform: "translateY(-50%)",
        }}
      >
        {data.label}
      </div>
    </div>
  );
}

const nodeTypes = {
  pipelineNode: PipelineNode,
  groupNode: GroupNode,
};

// ─── Layout constants ─────────────────────────────────────────────────────────
const NW = 160;   // node width
const NH = 34;    // node height
const HS = 80;    // horizontal gap between nodes
const VS = 16;    // vertical gap between stacked fan-out nodes
const FAN_STRIDE = NH + VS;

// Outer stage X positions (left-to-right)
const X_BASELINE_FETCH  = 0;
const X_RELEVANCY       = X_BASELINE_FETCH + NW + HS;
const X_ANALYST_GROUP   = X_RELEVANCY + NW + HS;
const ANALYST_PAD_X     = 24;
const ANALYST_PAD_TOP   = 40;
const ANALYST_PAD_BOT   = 24;

// Analyst-internal X positions (relative to group origin)
const AX_DISCOVER       = ANALYST_PAD_X;
const AX_SPECIALISTS    = AX_DISCOVER + NW + 60;
const AX_SEQUENTIAL     = AX_SPECIALISTS + NW + 60;

// Analyst sequential nodes top-to-bottom
const ANALYST_SEQ = [
  { id: "call_peripheral_scan",    label: "PERIPHERAL SCAN" },
  { id: "aggregate",               label: "AGGREGATE" },
  { id: "call_ach_red_team",       label: "ACH RED TEAM" },
  { id: "call_baseline_comparison",label: "BASELINE COMPARISON" },
  { id: "final_synthesis",         label: "FINAL SYNTHESIS" },
];
const SEQ_GAP = NH + 20;

function getOuterState(stageId, runState) {
  const order = ["fetching_baseline", "checking_relevance", "running_analyst", "writing_baseline", "done"];
  const stageMap = {
    baseline_fetch:  "fetching_baseline",
    relevancy_check: "checking_relevance",
    baseline_write:  "writing_baseline",
  };
  const target = stageMap[stageId];
  if (!target) return "pending";
  const runIdx = order.indexOf(runState);
  const tgtIdx = order.indexOf(target);
  if (runState === "error") return "failed";
  if (runState === "skipped" && stageId === "baseline_write") return "pending";
  if (runIdx === tgtIdx) return "running";
  if (runIdx > tgtIdx) return "completed";
  return "pending";
}

function getAnalystInternalState(nodeId, analystTaskState) {
  if (!analystTaskState) return "pending";
  const { running_node, node_outputs, state } = analystTaskState;
  if (running_node === nodeId) return "running";
  if (node_outputs && nodeId in node_outputs) return "completed";
  if (state === "failed" && node_outputs && !(nodeId in node_outputs)) return "failed";
  return "pending";
}

function getGroupState(runState, analystTaskState) {
  if (runState === "running_analyst") return "running";
  if (["writing_baseline", "done"].includes(runState)) return "completed";
  if (runState === "error" && analystTaskState?.state === "failed") return "failed";
  return "pending";
}

export default function PipelineGraph({ runState, analystTaskState, selectedNodeId, onNodeSelect }) {
  const nodeOutputs = analystTaskState?.node_outputs ?? {};

  const { nodes, edges } = useMemo(() => {
    const ns = [];
    const es = [];

    // ── Detect specialist fan-out keys ─────────────────────────────────────
    const specialistFanOuts = Object.keys(nodeOutputs)
      .filter((k) => FAN_OUT_RE.test(k) && k.startsWith("call_specialist"))
      .sort((a, b) => {
        const ai = parseInt(FAN_OUT_RE.exec(a)[2]);
        const bi = parseInt(FAN_OUT_RE.exec(b)[2]);
        return ai - bi;
      });

    // Base specialist node (always shown)
    const hasBaseSpecialist = "call_specialist" in nodeOutputs || analystTaskState?.running_node === "call_specialist";
    const specialistCount = Math.max(specialistFanOuts.length + (hasBaseSpecialist ? 1 : 0), 1);

    // ── Analyst group dimensions ────────────────────────────────────────────
    const seqTotalH = ANALYST_SEQ.length * NH + (ANALYST_SEQ.length - 1) * 20;
    const fanH = specialistCount * NH + (specialistCount - 1) * VS;
    const innerH = Math.max(seqTotalH, fanH, NH /* discover */);
    const groupW = ANALYST_PAD_X + NW + 60 + NW + 60 + NW + ANALYST_PAD_X;
    const groupH = ANALYST_PAD_TOP + innerH + ANALYST_PAD_BOT;

    const groupX = X_ANALYST_GROUP;
    const groupY = 0;

    // Center all columns vertically within the group
    const discoverY = groupY + ANALYST_PAD_TOP + (innerH - NH) / 2;
    const fanStartY = groupY + ANALYST_PAD_TOP + (innerH - fanH) / 2;
    const seqStartY = groupY + ANALYST_PAD_TOP + (innerH - seqTotalH) / 2;

    const outerY = groupY + groupH / 2 - NH / 2; // vertically center outer nodes with group

    // ── Outer: Baseline Fetch ───────────────────────────────────────────────
    const bfState = selectedNodeId === "baseline_fetch" ? "selected" : getOuterState("baseline_fetch", runState);
    ns.push({
      id: "baseline_fetch",
      type: "pipelineNode",
      position: { x: X_BASELINE_FETCH, y: outerY },
      data: { label: "BASELINE FETCH", executionState: bfState },
      draggable: false,
    });

    // ── Outer: Relevancy Check ──────────────────────────────────────────────
    const relState = selectedNodeId === "relevancy_check" ? "selected" : getOuterState("relevancy_check", runState);
    ns.push({
      id: "relevancy_check",
      type: "pipelineNode",
      position: { x: X_RELEVANCY, y: outerY },
      data: { label: "RELEVANCY CHECK", executionState: relState },
      draggable: false,
    });

    // ── Analyst Group container ─────────────────────────────────────────────
    ns.push({
      id: "group_analyst",
      type: "groupNode",
      position: { x: groupX, y: groupY },
      data: { label: "LEAD ANALYST", executionState: getGroupState(runState, analystTaskState), width: groupW, height: groupH },
      draggable: false,
      selectable: false,
      zIndex: 0,
    });

    // ── Analyst: Discover + Select ──────────────────────────────────────────
    const discoverState = selectedNodeId === "discover_and_select" ? "selected" : getAnalystInternalState("discover_and_select", analystTaskState);
    ns.push({
      id: "discover_and_select",
      type: "pipelineNode",
      position: { x: groupX + AX_DISCOVER, y: discoverY },
      data: { label: "DISCOVER + SELECT", executionState: discoverState },
      draggable: false,
      zIndex: 1,
    });

    // ── Analyst: Specialist fan-out nodes ───────────────────────────────────
    const specialistIds = [];

    // Base specialist node
    const baseSpecState = selectedNodeId === "call_specialist"
      ? "selected"
      : getAnalystInternalState("call_specialist", analystTaskState);
    ns.push({
      id: "call_specialist",
      type: "pipelineNode",
      position: { x: groupX + AX_SPECIALISTS, y: fanStartY },
      data: { label: "SPECIALIST", executionState: baseSpecState },
      draggable: false,
      zIndex: 1,
    });
    specialistIds.push("call_specialist");

    // Fan-out instances
    specialistFanOuts.forEach((key, idx) => {
      const foState = selectedNodeId === key ? "selected" : getAnalystInternalState(key, analystTaskState);
      ns.push({
        id: key,
        type: "pipelineNode",
        position: { x: groupX + AX_SPECIALISTS, y: fanStartY + (idx + 1) * FAN_STRIDE },
        data: { label: `SPECIALIST ${idx + 2}`, executionState: foState, isFanOut: true },
        draggable: false,
        zIndex: 1,
      });
      specialistIds.push(key);
    });

    // ── Analyst: Sequential nodes ───────────────────────────────────────────
    ANALYST_SEQ.forEach(({ id, label }, idx) => {
      const seqState = selectedNodeId === id ? "selected" : getAnalystInternalState(id, analystTaskState);
      ns.push({
        id,
        type: "pipelineNode",
        position: { x: groupX + AX_SEQUENTIAL, y: seqStartY + idx * SEQ_GAP },
        data: { label, executionState: seqState },
        draggable: false,
        zIndex: 1,
      });
    });

    // ── Outer: Baseline Write ───────────────────────────────────────────────
    const bwX = groupX + groupW + HS;
    const bwState = selectedNodeId === "baseline_write" ? "selected" : getOuterState("baseline_write", runState);
    ns.push({
      id: "baseline_write",
      type: "pipelineNode",
      position: { x: bwX, y: outerY },
      data: { label: "BASELINE WRITE", executionState: bwState },
      draggable: false,
    });

    // ── Edges ───────────────────────────────────────────────────────────────
    const edgeBase = {
      type: "smoothstep",
      animated: true,
      style: { stroke: "rgba(0,212,255,0.25)", strokeDasharray: "4 4" },
    };

    // Outer chain
    es.push({ ...edgeBase, id: "e-bf-rel", source: "baseline_fetch", target: "relevancy_check" });
    es.push({ ...edgeBase, id: "e-rel-grp", source: "relevancy_check", target: "group_analyst", targetHandle: null });

    // Discover → each specialist
    specialistIds.forEach((sid, i) => {
      es.push({ ...edgeBase, id: `e-disc-${sid}`, source: "discover_and_select", target: sid });
    });

    // Each specialist → peripheral scan (first sequential node)
    specialistIds.forEach((sid, i) => {
      es.push({ ...edgeBase, id: `e-${sid}-seq`, source: sid, target: "call_peripheral_scan" });
    });

    // Sequential chain
    for (let i = 0; i < ANALYST_SEQ.length - 1; i++) {
      es.push({
        ...edgeBase,
        id: `e-seq-${i}`,
        source: ANALYST_SEQ[i].id,
        target: ANALYST_SEQ[i + 1].id,
      });
    }

    // relevancy → discover_and_select (entry into analyst group)
    es.push({ ...edgeBase, id: "e-rel-disc", source: "relevancy_check", target: "discover_and_select" });

    // final_synthesis → baseline_write
    es.push({ ...edgeBase, id: "e-synth-bw", source: "final_synthesis", target: "baseline_write" });

    // Remove the group node as edge target (was placeholder)
    const finalEdges = es.filter((e) => e.target !== "group_analyst");

    return { nodes: ns, edges: finalEdges };
  }, [runState, analystTaskState, selectedNodeId, nodeOutputs]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      fitView
      fitViewOptions={{ padding: 0.15 }}
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable={true}
      onNodeClick={(_, node) => {
        if (node.type === "groupNode") return;
        onNodeSelect(node.id === selectedNodeId ? null : node.id);
      }}
      proOptions={{ hideAttribution: true }}
    >
      <Background color="rgba(0,212,255,0.05)" gap={20} />
      <Controls
        showInteractive={false}
        style={{
          background: "var(--hud-bg-panel)",
          border: "1px solid var(--hud-border)",
          borderRadius: 0,
        }}
      />
    </ReactFlow>
  );
}
