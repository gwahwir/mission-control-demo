import { useEffect, useRef, useState } from "react";
import { Box, Text, Badge, Group, Stack, Collapse } from "@mantine/core";
import ReactMarkdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import { fetchTask } from "../../hooks/useApi";

// ── Agent identity ────────────────────────────────────────────────────────────

const NODE_META = {
  receive:                    { label: "Lead Analyst A",      role: "Orchestrator",        color: "#00d4ff" },
  discover_and_select:        { label: "Lead Analyst A",      role: "Specialist Selection", color: "#00d4ff" },
  call_specialist:            { label: "Specialist",          role: "Domain Analysis",      color: "#a78bfa" },
  call_peripheral_scan:       { label: "Peripheral Scanner",  role: "Weak Signal Detection",color: "#fb923c" },
  aggregate:                  { label: "Lead Analyst A",      role: "Aggregation",          color: "#00d4ff" },
  call_ach_red_team:          { label: "ACH Red Team",        role: "Challenge Analysis",   color: "#f87171" },
  call_baseline_comparison:   { label: "Baseline Analyst",   role: "Change Detection",     color: "#34d399" },
  final_synthesis:            { label: "Lead Analyst A",      role: "Final Synthesis",      color: "#00d4ff" },
  respond:                    { label: "Lead Analyst A",      role: "Complete",             color: "#00d4ff" },
};

function getNodeMeta(key) {
  // Fan-out: call_specialist:1, call_specialist:2, …
  const base = key.replace(/:\d+$/, "");
  return NODE_META[base] ?? { label: key, role: "Agent", color: "#6b7280" };
}

// ── Extract readable text from a node_output JSON payload ─────────────────────

function extractMessageText(nodeKey, payloadStr) {
  let payload;
  try { payload = JSON.parse(payloadStr); } catch { return payloadStr; }

  const base = nodeKey.replace(/:\d+$/, "");

  switch (base) {
    case "discover_and_select": {
      const selected = payload.selected_specialists ?? [];
      const reasoning = payload.selection_reasoning ?? {};
      if (!selected.length) return null;
      const lines = selected.map((s) => {
        const why = reasoning[s.label] ? ` — ${reasoning[s.label]}` : "";
        return `**${s.label}**${why}`;
      });
      return `Selected ${selected.length} specialist${selected.length !== 1 ? "s" : ""}:\n\n${lines.join("\n\n")}`;
    }

    case "call_specialist": {
      const results = payload.results ?? [];
      if (!results.length) return null;
      const [label, text] = results[0];
      // Try to parse JSON output from specialist
      try {
        const parsed = JSON.parse(text);
        if (parsed.summary) return `**${label}**\n\n${parsed.summary}`;
      } catch {}
      return text || null;
    }

    case "call_peripheral_scan":
      return payload.peripheral_findings || null;

    case "aggregate":
      return payload.aggregated_consensus || null;

    case "call_ach_red_team":
      return payload.ach_analysis || null;

    case "call_baseline_comparison":
      return payload.baseline_comparison || null;

    case "final_synthesis":
      return payload.output || null;

    default:
      return null;
  }
}

// ── Typing indicator ──────────────────────────────────────────────────────────

function TypingIndicator({ nodeKey }) {
  const meta = getNodeMeta(nodeKey);
  return (
    <Box
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 10,
        padding: "8px 0",
        opacity: 0.85,
      }}
    >
      <Box style={{ flexShrink: 0, paddingTop: 2 }}>
        <Box
          style={{
            width: 32,
            height: 32,
            border: `1px solid ${meta.color}`,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontFamily: "monospace",
            fontSize: 11,
            color: meta.color,
            background: "var(--hud-bg-deep)",
          }}
        >
          {meta.label.slice(0, 2).toUpperCase()}
        </Box>
      </Box>
      <Box>
        <Group gap={6} mb={4}>
          <Text size="xs" style={{ color: meta.color, fontFamily: "monospace", fontWeight: 700, fontSize: 11 }}>
            {meta.label}
          </Text>
          <Text size="xs" style={{ color: "var(--hud-text-dimmed)", fontFamily: "monospace", fontSize: 10 }}>
            {meta.role}
          </Text>
        </Group>
        <Box
          style={{
            background: "var(--hud-bg-panel)",
            border: `1px solid ${meta.color}44`,
            padding: "8px 12px",
            display: "flex",
            gap: 4,
            alignItems: "center",
          }}
        >
          <TypingDots color={meta.color} />
        </Box>
      </Box>
    </Box>
  );
}

function TypingDots({ color }) {
  return (
    <Box style={{ display: "flex", gap: 4, alignItems: "center" }}>
      {[0, 1, 2].map((i) => (
        <Box
          key={i}
          style={{
            width: 6,
            height: 6,
            borderRadius: "50%",
            backgroundColor: color,
            animation: `typing-bounce 1.2s ease-in-out ${i * 0.2}s infinite`,
          }}
        />
      ))}
    </Box>
  );
}

// ── Single message bubble ─────────────────────────────────────────────────────

function MessageBubble({ nodeKey, payloadStr, index }) {
  const [expanded, setExpanded] = useState(false);
  const meta = getNodeMeta(nodeKey);
  const text = extractMessageText(nodeKey, payloadStr);

  if (!text) return null;

  const preview = text.replace(/\*\*/g, "").slice(0, 120).trim();
  const isLong = text.length > 120;

  // Specialist fan-out: label comes from the results tuple
  let displayLabel = meta.label;
  const base = nodeKey.replace(/:\d+$/, "");
  if (base === "call_specialist") {
    try {
      const payload = JSON.parse(payloadStr);
      const results = payload.results ?? [];
      if (results[0]?.[0]) displayLabel = results[0][0];
    } catch {}
  }

  return (
    <Box
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 10,
        padding: "8px 0",
        borderBottom: "1px solid var(--hud-border)",
      }}
    >
      {/* Avatar */}
      <Box style={{ flexShrink: 0, paddingTop: 2 }}>
        <Box
          style={{
            width: 32,
            height: 32,
            border: `1px solid ${meta.color}`,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontFamily: "monospace",
            fontSize: 11,
            color: meta.color,
            background: "var(--hud-bg-deep)",
          }}
        >
          {displayLabel.slice(0, 2).toUpperCase()}
        </Box>
      </Box>

      {/* Content */}
      <Box style={{ flex: 1, minWidth: 0 }}>
        <Group gap={6} mb={4}>
          <Text size="xs" style={{ color: meta.color, fontFamily: "monospace", fontWeight: 700, fontSize: 11 }}>
            {displayLabel}
          </Text>
          <Badge
            size="xs"
            radius={0}
            variant="outline"
            color="gray"
            styles={{ root: { fontFamily: "monospace", fontSize: 9, letterSpacing: "0.5px", borderColor: "var(--hud-border)" } }}
          >
            {meta.role}
          </Badge>
        </Group>

        {/* Collapsed preview */}
        {!expanded && (
          <Box
            style={{
              background: "var(--hud-bg-panel)",
              border: `1px solid ${meta.color}22`,
              padding: "8px 12px",
              cursor: isLong ? "pointer" : "default",
            }}
            onClick={() => isLong && setExpanded(true)}
          >
            <Text size="xs" style={{ color: "var(--hud-text-primary)", fontFamily: "monospace", fontSize: 11, lineHeight: 1.6 }}>
              {preview}{isLong ? "..." : ""}
            </Text>
          </Box>
        )}

        {/* Expanded full content */}
        <Collapse in={expanded}>
          <Box
            style={{
              background: "var(--hud-bg-panel)",
              border: `1px solid ${meta.color}44`,
              padding: "12px 16px",
              fontSize: 12,
              lineHeight: 1.7,
              color: "var(--hud-text-primary)",
              fontFamily: "monospace",
            }}
          >
            <ReactMarkdown
              remarkPlugins={[remarkBreaks]}
              components={{
                p: ({ children }) => <p style={{ margin: "0 0 8px" }}>{children}</p>,
                strong: ({ children }) => <strong style={{ color: meta.color }}>{children}</strong>,
                li: ({ children }) => <li style={{ marginBottom: 4 }}>{children}</li>,
                h2: ({ children }) => <h2 style={{ color: meta.color, fontSize: 13, margin: "12px 0 6px", letterSpacing: "1px" }}>{children}</h2>,
                h3: ({ children }) => <h3 style={{ color: "var(--hud-text-dimmed)", fontSize: 12, margin: "10px 0 4px" }}>{children}</h3>,
                code: ({ children }) => <code style={{ background: "var(--hud-bg-deep)", padding: "1px 4px", fontSize: 11 }}>{children}</code>,
              }}
            >
              {text}
            </ReactMarkdown>
          </Box>
        </Collapse>

        {isLong && (
          <Text
            size="xs"
            style={{ color: "var(--hud-cyan)", fontFamily: "monospace", fontSize: 10, cursor: "pointer", marginTop: 4 }}
            onClick={() => setExpanded((v) => !v)}
          >
            {expanded ? "[ collapse ]" : "[ expand ]"}
          </Text>
        )}
      </Box>
    </Box>
  );
}

// ── Empty / idle states ───────────────────────────────────────────────────────

function EmptyState({ analystTask }) {
  if (!analystTask) {
    return (
      <Box style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100%", gap: 12 }}>
        <Text style={{ color: "var(--hud-text-dimmed)", fontFamily: "monospace", fontSize: 12, letterSpacing: "1px" }}>
          NO ACTIVE PIPELINE
        </Text>
        <Text style={{ color: "var(--hud-border)", fontFamily: "monospace", fontSize: 11 }}>
          Run a pipeline from [04] PIPELINE to see the agent feed
        </Text>
      </Box>
    );
  }
  return (
    <Box style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", gap: 8 }}>
      <Text style={{ color: "var(--hud-text-dimmed)", fontFamily: "monospace", fontSize: 12, letterSpacing: "1px" }}>
        WAITING FOR AGENTS...
      </Text>
    </Box>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function CommsPage({ analystTask, taskState }) {
  const [seededState, setSeededState] = useState(null);
  const bottomRef = useRef(null);
  const scrollContainerRef = useRef(null);
  const userScrolledUpRef = useRef(false);

  // On mount or task change, fetch current task state immediately to catch
  // any node_outputs that arrived before this tab was opened.
  useEffect(() => {
    setSeededState(null);
    if (!analystTask) return;
    fetchTask(analystTask.agentId, analystTask.taskId)
      .then(setSeededState)
      .catch(() => {});
  }, [analystTask]);

  // Merge: live prop updates take precedence over seeded state,
  // but seed fills in what was there before the tab opened.
  const merged = taskState ?? seededState;
  const nodeOutputs = merged?.node_outputs ?? {};
  const rawRunningNode = merged?.running_node ?? "";

  // Keep the typing indicator visible until that node's output actually lands.
  // The backend clears running_node and emits NODE_OUTPUT almost simultaneously,
  // so rawRunningNode often flickers to "" before React re-renders. We hold onto
  // the last non-empty value and only clear it once the node appears in node_outputs.
  const [displayRunningNode, setDisplayRunningNode] = useState("");
  useEffect(() => {
    if (rawRunningNode) {
      setDisplayRunningNode(rawRunningNode);
    } else if (displayRunningNode && nodeOutputs[displayRunningNode] !== undefined) {
      // The node we were showing as "typing" has completed — clear it
      setDisplayRunningNode("");
    }
  }, [rawRunningNode, nodeOutputs]);

  const runningNode = displayRunningNode;

  // Detect when user scrolls up — suppress auto-scroll until they return to bottom
  const handleScroll = () => {
    const el = scrollContainerRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    userScrolledUpRef.current = distanceFromBottom > 80;
  };

  // Auto-scroll only when new content arrives and user hasn't scrolled up
  const prevNodeOutputCountRef = useRef(0);
  useEffect(() => {
    const currentCount = Object.keys(nodeOutputs).length;
    const hasNewContent = currentCount > prevNodeOutputCountRef.current || runningNode;
    prevNodeOutputCountRef.current = currentCount;

    if (hasNewContent && !userScrolledUpRef.current) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [nodeOutputs, runningNode]);

  // Build ordered message list from node_outputs keys
  // Keys arrive in insertion order (JS object preserves insertion order for string keys)
  const messageEntries = Object.entries(nodeOutputs).filter(([key]) => {
    const base = key.replace(/:\d+$/, "");
    return base !== "receive" && base !== "respond";
  });

  const isTerminal = ["completed", "failed", "canceled"].includes(taskState?.state);

  return (
    <Box style={{ display: "flex", flexDirection: "column", height: "calc(100vh - 180px)" }}>
      {/* Header */}
      <Box
        style={{
          background: "var(--hud-bg-panel)",
          border: "1px solid var(--hud-border)",
          padding: "10px 16px",
          flexShrink: 0,
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <Text
          size="xs"
          style={{ color: "var(--hud-cyan)", letterSpacing: "2px", textTransform: "uppercase", fontSize: 11, fontFamily: "monospace" }}
        >
          ◈ AGENT COMMS FEED
        </Text>
        <Group gap={8}>
          {analystTask && (
            <Text size="xs" style={{ color: "var(--hud-text-dimmed)", fontFamily: "monospace", fontSize: 10 }}>
              TASK {analystTask.taskId.slice(0, 8)}…
            </Text>
          )}
          {taskState && (
            <Badge
              size="xs"
              radius={0}
              color={isTerminal ? (taskState.state === "completed" ? "hud-green" : "hud-red") : "hud-amber"}
              variant="light"
              styles={{ root: { fontFamily: "monospace", fontSize: 9, letterSpacing: "1px" } }}
            >
              {taskState.state?.toUpperCase()}
            </Badge>
          )}
        </Group>
      </Box>

      {/* Feed */}
      <Box
        ref={scrollContainerRef}
        onScroll={handleScroll}
        style={{
          flex: 1,
          overflowY: "auto",
          padding: "12px 16px",
          background: "var(--hud-bg-deep)",
          border: "1px solid var(--hud-border)",
          borderTop: "none",
        }}
      >
        {messageEntries.length === 0 && !runningNode ? (
          <EmptyState analystTask={analystTask} />
        ) : (
          <Stack gap={0}>
            {messageEntries.map(([key, payloadStr], i) => (
              <MessageBubble key={key} nodeKey={key} payloadStr={payloadStr} index={i} />
            ))}
            {runningNode && !isTerminal && <TypingIndicator nodeKey={runningNode} />}
            <div ref={bottomRef} />
          </Stack>
        )}
      </Box>

      {/* CSS for typing animation */}
      <style>{`
        @keyframes typing-bounce {
          0%, 60%, 100% { transform: translateY(0); opacity: 0.4; }
          30% { transform: translateY(-6px); opacity: 1; }
        }
      `}</style>
    </Box>
  );
}
