import { useState, useEffect, useRef, useCallback } from "react";
import {
  Box,
  Text,
  Badge,
  Group,
  Stack,
  Collapse,
  Textarea,
  TextInput,
  Button,
  SimpleGrid,
} from "@mantine/core";
import ReactMarkdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import {
  dispatchTask,
  fetchBaseline,
  fetchTask,
  ensureTopicRegistered,
  subscribeToTask,
  writeBaselineVersion,
  writeBaselineDelta,
} from "../../hooks/useApi";

// ── Constants ─────────────────────────────────────────────────────────────────

const TERMINAL_STATES = new Set(["completed", "failed", "canceled"]);

const PIPELINE_STEPS = [
  { id: "baseline",   label: "BASELINE FETCH",    short: "BL" },
  { id: "analyst",    label: "LEAD ANALYST A",     short: "LA" },
];

// ── Agent identity ─────────────────────────────────────────────────────────────

const NODE_META = {
  receive:                  { label: "Lead Analyst A",     role: "Orchestrator",         color: "#00d4ff" },
  discover_and_select:      { label: "Lead Analyst A",     role: "Specialist Selection", color: "#00d4ff" },
  call_specialist:          { label: "Specialist",         role: "Domain Analysis",      color: "#a78bfa" },
  call_peripheral_scan:     { label: "Peripheral Scanner", role: "Weak Signal Detection",color: "#fb923c" },
  aggregate:                { label: "Lead Analyst A",     role: "Aggregation",          color: "#00d4ff" },
  call_ach_red_team:        { label: "ACH Red Team",       role: "Challenge Analysis",   color: "#f87171" },
  call_baseline_comparison: { label: "Baseline Analyst",  role: "Change Detection",     color: "#34d399" },
  final_synthesis:          { label: "Lead Analyst A",     role: "Final Synthesis",      color: "#00d4ff" },
  respond:                  { label: "Lead Analyst A",     role: "Complete",             color: "#00d4ff" },
};

function getNodeMeta(key) {
  const base = key.replace(/:\d+$/, "");
  return NODE_META[base] ?? { label: key, role: "Agent", color: "#6b7280" };
}

function categoryToRole(category) {
  if (!category) return "Domain Analysis";
  return category
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

// ── Text extraction from node outputs ─────────────────────────────────────────

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

// ── Typing indicator ───────────────────────────────────────────────────────────

function TypingDots({ color }) {
  return (
    <Box style={{ display: "flex", gap: 4, alignItems: "center" }}>
      {[0, 1, 2].map((i) => (
        <Box
          key={i}
          style={{
            width: 5,
            height: 5,
            borderRadius: "50%",
            backgroundColor: color,
            animation: `af-typing-bounce 1.2s ease-in-out ${i * 0.2}s infinite`,
          }}
        />
      ))}
    </Box>
  );
}

function TypingIndicator({ nodeKey }) {
  const meta = getNodeMeta(nodeKey);
  return (
    <Box
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 10,
        padding: "10px 0",
        animation: "af-fade-in 0.25s ease-out",
      }}
    >
      <AgentAvatar label={meta.label} color={meta.color} />
      <Box>
        <AgentHeader label={meta.label} role={meta.role} color={meta.color} />
        <Box
          style={{
            background: "var(--hud-bg-panel)",
            border: `1px solid ${meta.color}33`,
            padding: "8px 14px",
            display: "flex",
            gap: 4,
            alignItems: "center",
            minWidth: 64,
          }}
        >
          <TypingDots color={meta.color} />
        </Box>
      </Box>
    </Box>
  );
}

// ── Avatar + header helpers ────────────────────────────────────────────────────

function AgentAvatar({ label, color }) {
  return (
    <Box
      style={{
        flexShrink: 0,
        width: 34,
        height: 34,
        border: `1px solid ${color}`,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontFamily: "monospace",
        fontSize: 10,
        fontWeight: 700,
        color,
        background: "var(--hud-bg-deep)",
        letterSpacing: "0.5px",
        boxShadow: `0 0 8px ${color}22`,
      }}
    >
      {label.slice(0, 2).toUpperCase()}
    </Box>
  );
}

function AgentHeader({ label, role, color }) {
  return (
    <Group gap={6} mb={5}>
      <Text
        size="xs"
        style={{ color, fontFamily: "monospace", fontWeight: 700, fontSize: 11, letterSpacing: "0.5px" }}
      >
        {label}
      </Text>
      <Badge
        size="xs"
        radius={0}
        variant="outline"
        styles={{
          root: {
            fontFamily: "monospace",
            fontSize: 9,
            letterSpacing: "0.5px",
            borderColor: `${color}44`,
            color: "var(--hud-text-dimmed)",
            textTransform: "uppercase",
          },
        }}
      >
        {role}
      </Badge>
    </Group>
  );
}

// ── Message bubble ─────────────────────────────────────────────────────────────

function MessageBubble({ nodeKey, payloadStr, animIndex }) {
  const [expanded, setExpanded] = useState(false);
  const meta = getNodeMeta(nodeKey);
  const text = extractMessageText(nodeKey, payloadStr);

  if (!text) return null;

  let displayLabel = meta.label;
  let displayRole = meta.role;
  const base = nodeKey.replace(/:\d+$/, "");
  if (base === "call_specialist") {
    try {
      const payload = JSON.parse(payloadStr);
      const results = payload.results ?? [];
      if (results[0]?.[0]) displayLabel = results[0][0];
      displayRole = categoryToRole(results[0]?.[2]);
    } catch {}
  }

  const preview = text.replace(/\*\*/g, "").slice(0, 160).trim();
  const isLong = text.length > 160;

  return (
    <Box
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 10,
        padding: "10px 0",
        borderBottom: "1px solid var(--hud-border)",
        animation: `af-fade-in 0.3s ease-out both`,
        animationDelay: `${animIndex * 0.04}s`,
      }}
    >
      <AgentAvatar label={displayLabel} color={meta.color} />

      <Box style={{ flex: 1, minWidth: 0 }}>
        <AgentHeader label={displayLabel} role={displayRole} color={meta.color} />

        {!expanded && (
          <Box
            style={{
              background: "var(--hud-bg-panel)",
              border: `1px solid ${meta.color}1a`,
              padding: "9px 13px",
              cursor: isLong ? "pointer" : "default",
              transition: "border-color 0.15s",
            }}
            onMouseEnter={(e) => isLong && (e.currentTarget.style.borderColor = `${meta.color}44`)}
            onMouseLeave={(e) => isLong && (e.currentTarget.style.borderColor = `${meta.color}1a`)}
            onClick={() => isLong && setExpanded(true)}
          >
            <Text
              size="xs"
              style={{
                color: "var(--hud-text-primary)",
                fontFamily: "monospace",
                fontSize: 11,
                lineHeight: 1.65,
                whiteSpace: "pre-wrap",
              }}
            >
              {preview}{isLong ? "…" : ""}
            </Text>
          </Box>
        )}

        <Collapse in={expanded}>
          <Box
            className="markdown-output"
            style={{
              background: "var(--hud-bg-panel)",
              border: `1px solid ${meta.color}33`,
              padding: "12px 16px",
              fontSize: 11,
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
                li: ({ children }) => <li style={{ marginBottom: 3 }}>{children}</li>,
                h2: ({ children }) => <h2 style={{ color: meta.color, fontSize: 12, margin: "12px 0 5px", letterSpacing: "1px" }}>{children}</h2>,
                h3: ({ children }) => <h3 style={{ color: "var(--hud-text-dimmed)", fontSize: 11, margin: "10px 0 4px" }}>{children}</h3>,
                code: ({ children }) => <code style={{ background: "var(--hud-bg-deep)", padding: "1px 4px", fontSize: 10 }}>{children}</code>,
              }}
            >
              {text}
            </ReactMarkdown>
          </Box>
        </Collapse>

        {isLong && (
          <Text
            size="xs"
            style={{
              color: "var(--hud-cyan)",
              fontFamily: "monospace",
              fontSize: 10,
              cursor: "pointer",
              marginTop: 4,
              letterSpacing: "0.5px",
              userSelect: "none",
            }}
            onClick={() => setExpanded((v) => !v)}
          >
            {expanded ? "[ collapse ]" : "[ expand full report ]"}
          </Text>
        )}
      </Box>
    </Box>
  );
}

// ── System message (pipeline stage transitions) ────────────────────────────────

function SystemMessage({ text, color = "var(--hud-text-dimmed)" }) {
  return (
    <Box
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        padding: "6px 0",
        animation: "af-fade-in 0.2s ease-out",
      }}
    >
      <Box style={{ flex: 1, height: 1, background: "var(--hud-border)" }} />
      <Text
        size="xs"
        style={{ color, fontFamily: "monospace", fontSize: 10, letterSpacing: "1.5px", whiteSpace: "nowrap" }}
      >
        {text}
      </Text>
      <Box style={{ flex: 1, height: 1, background: "var(--hud-border)" }} />
    </Box>
  );
}

// ── Step progress bar ─────────────────────────────────────────────────────────

function StepBar({ currentStep, taskState }) {
  const steps = [
    { id: "idle",     label: "READY",    flex: 1 },
    { id: "baseline", label: "BASELINE", flex: 1 },
    { id: "analyst",  label: "ANALYSIS", flex: 6 },
    { id: "writing",  label: "WRITING",  flex: 1 },
    { id: "done",     label: "COMPLETE", flex: 1 },
  ];

  const stepIndex = {
    idle: 0,
    fetching_baseline: 1,
    running_analyst: 2,
    writing_baseline: 3,
    done: 4,
    error: -1,
  }[currentStep] ?? 0;

  const isTerminal = ["completed", "failed", "canceled"].includes(taskState?.state);
  const isFailed = currentStep === "error" || taskState?.state === "failed";

  return (
    <Group gap={0} style={{ width: "100%" }}>
      {steps.map((s, i) => {
        const isActive = i === stepIndex;
        const isDone = i < stepIndex || (i === stepIndex && (currentStep === "done" || isTerminal));
        const isCurrent = isActive && !isDone;
        const color = isFailed && isActive ? "var(--hud-red)"
          : isDone ? "var(--hud-green)"
          : isCurrent ? "var(--hud-amber)"
          : "var(--hud-border)";
        const textColor = isFailed && isActive ? "var(--hud-red)"
          : isDone ? "var(--hud-green)"
          : isCurrent ? "var(--hud-amber)"
          : "var(--hud-text-dimmed)";

        return (
          <Box key={s.id} style={{ flex: s.flex, display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
            <Box
              style={{
                height: 2,
                width: "100%",
                background: color,
                transition: "background 0.4s ease",
                boxShadow: (isDone || isCurrent) ? `0 0 6px ${color}` : "none",
              }}
            />
            <Text
              size="xs"
              style={{
                color: textColor,
                fontFamily: "monospace",
                fontSize: 9,
                letterSpacing: "1px",
                transition: "color 0.4s ease",
                fontWeight: isCurrent ? 700 : 400,
              }}
            >
              {s.label}
            </Text>
          </Box>
        );
      })}
    </Group>
  );
}

// ── Input form panel ───────────────────────────────────────────────────────────

const INPUT_STYLES = {
  input: {
    background: "var(--hud-bg-deep)",
    border: "1px solid var(--hud-border)",
    borderRadius: 0,
    color: "var(--hud-text-primary)",
    fontFamily: "monospace",
    fontSize: 12,
    transition: "border-color 0.15s",
  },
  label: {
    color: "var(--hud-text-dimmed)",
    fontSize: 10,
    letterSpacing: "1px",
    textTransform: "uppercase",
    marginBottom: 4,
    fontFamily: "monospace",
  },
};

function InputPanel({ onRun, isRunning, onReset, runState, agents }) {
  const [topic, setTopic] = useState("geo.middle_east.iran");
  const [topicLabel, setTopicLabel] = useState("Iran");
  const [report, setReport] = useState("");
  const [keyQuestions, setKeyQuestions] = useState(
    "1. Are there new signals of diplomatic engagement or breakdown?\n2. What is the risk of escalation in the next 30 days?\n3. What is the impact on South East Asia's energy security?\n4. What is the impact on South East Asia's supply chain dependencies?"
  );
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [baselineOverride, setBaselineOverride] = useState("");

  const leadAnalystAgent = agents?.find(
    (a) => a.id?.startsWith("lead-analyst") && a.status === "online"
  );
  const agentsReady = !!leadAnalystAgent;

  const canRun = !isRunning && agentsReady && topic.trim() && report.trim() && keyQuestions.trim();
  const isDone = ["done", "error"].includes(runState);

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!canRun) return;
    onRun({ topic, topicLabel, report, keyQuestions, baselineOverride: baselineOverride.trim() || null });
  };

  return (
    <Box
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        background: "var(--hud-bg-panel)",
        borderRight: "1px solid var(--hud-border)",
      }}
    >
      {/* Panel header */}
      <Box
        style={{
          padding: "12px 16px",
          borderBottom: "1px solid var(--hud-border)",
          background: "var(--hud-bg-deep)",
          flexShrink: 0,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
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
          ◈ ANALYST INPUT
        </Text>
        {isDone && (
          <Text
            size="xs"
            style={{
              color: "var(--hud-text-dimmed)",
              cursor: "pointer",
              fontFamily: "monospace",
              fontSize: 10,
              letterSpacing: "1px",
              userSelect: "none",
            }}
            onClick={onReset}
          >
            [ RESET ]
          </Text>
        )}
      </Box>

      {/* Agent readiness */}
      {!agentsReady && (
        <Box
          style={{
            padding: "8px 16px",
            background: "rgba(255,184,0,0.06)",
            borderBottom: "1px solid rgba(255,184,0,0.2)",
            flexShrink: 0,
          }}
        >
          <Text size="xs" style={{ color: "var(--hud-amber)", fontFamily: "monospace", fontSize: 10, letterSpacing: "0.5px" }}>
            ⚠ Waiting for Lead Analyst agent to come online...
          </Text>
        </Box>
      )}

      {/* Form */}
      <Box
        component="form"
        onSubmit={handleSubmit}
        style={{ flex: 1, overflowY: "auto", padding: "16px" }}
      >
        <SimpleGrid cols={2} spacing="sm" mb="sm">
          <TextInput
            label="Topic Path"
            placeholder="geo.middle_east.iran"
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            styles={INPUT_STYLES}
            required
            disabled={isRunning}
          />
          <TextInput
            label="Topic Label"
            placeholder="Iran"
            value={topicLabel}
            onChange={(e) => setTopicLabel(e.target.value)}
            styles={INPUT_STYLES}
            disabled={isRunning}
          />
        </SimpleGrid>

        <Textarea
          label="Incoming Report"
          placeholder="Paste the incoming report or intelligence text here..."
          value={report}
          onChange={(e) => setReport(e.target.value)}
          styles={INPUT_STYLES}
          minRows={4}
          autosize
          maxRows={10}
          mb="sm"
          required
          disabled={isRunning}
        />

        <Textarea
          label="Key Questions"
          placeholder="1. Question one&#10;2. Question two"
          value={keyQuestions}
          onChange={(e) => setKeyQuestions(e.target.value)}
          styles={INPUT_STYLES}
          minRows={3}
          autosize
          maxRows={6}
          mb="sm"
          required
          disabled={isRunning}
        />

        <Box mb="md">
          <Text
            size="xs"
            style={{
              color: "var(--hud-text-dimmed)",
              cursor: "pointer",
              letterSpacing: "1px",
              textTransform: "uppercase",
              fontSize: 10,
              userSelect: "none",
              fontFamily: "monospace",
            }}
            onClick={() => setShowAdvanced((v) => !v)}
          >
            {showAdvanced ? "▾" : "▸"} Advanced options
          </Text>
          <Collapse in={showAdvanced}>
            <Box mt="sm">
              <Textarea
                label="Baseline Override (optional)"
                placeholder="Paste an existing baseline narrative here to override the stored baseline..."
                value={baselineOverride}
                onChange={(e) => setBaselineOverride(e.target.value)}
                styles={INPUT_STYLES}
                minRows={3}
                autosize
                disabled={isRunning}
              />
            </Box>
          </Collapse>
        </Box>

        <Button
          type="submit"
          disabled={!canRun}
          loading={isRunning}
          variant="outline"
          color="hud-cyan"
          radius={0}
          fullWidth
          styles={{
            root: {
              fontFamily: "monospace",
              letterSpacing: "2px",
              fontSize: 12,
              textTransform: "uppercase",
              borderColor: canRun ? "var(--hud-cyan)" : "var(--hud-border)",
              height: 38,
            },
          }}
        >
          {isRunning ? "ANALYSING..." : "DISPATCH ANALYSIS"}
        </Button>
      </Box>
    </Box>
  );
}

// ── Chat feed panel ────────────────────────────────────────────────────────────

function ChatFeed({ taskState, runState, taskId, fetchedBaseline, baselineResult }) {
  const bottomRef = useRef(null);
  const scrollContainerRef = useRef(null);
  const userScrolledUpRef = useRef(false);

  const nodeOutputs = taskState?.node_outputs ?? {};
  const rawRunningNode = taskState?.running_node ?? "";

  // Track which node is currently running AND how many node_outputs existed when
  // it started. This lets us correctly clear the typing indicator even when the
  // same node name runs multiple times (e.g. call_specialist:0, :1, :2 …).
  // The control plane always emits running_node = "call_specialist" (plain name)
  // but deduplicates node_outputs keys as "call_specialist", "call_specialist:1" …
  // so checking nodeOutputs[prev] would find the *first* run's entry immediately
  // and kill the dots for every subsequent run of the same node.
  const nodeOutputCount = Object.keys(nodeOutputs).length;
  const [displayRunningNode, setDisplayRunningNode] = useState("");
  const expectedOutputCountRef = useRef(0); // outputs that should exist when this node finishes

  useEffect(() => {
    if (rawRunningNode) {
      // A new node just started — record how many outputs exist right now.
      // The dots should stay until we see at least one more output land.
      expectedOutputCountRef.current = nodeOutputCount + 1;
      setDisplayRunningNode(rawRunningNode);
    } else {
      // running_node cleared — hide dots only once the new output has landed.
      setDisplayRunningNode((prev) => {
        if (!prev) return prev;
        return nodeOutputCount >= expectedOutputCountRef.current ? "" : prev;
      });
    }
  }, [rawRunningNode, nodeOutputCount]);

  const runningNode = displayRunningNode;

  const handleScroll = () => {
    const el = scrollContainerRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    userScrolledUpRef.current = distanceFromBottom > 80;
  };

  const prevNodeOutputCountRef = useRef(0);
  useEffect(() => {
    const currentCount = Object.keys(nodeOutputs).length;
    const hasNewContent = currentCount > prevNodeOutputCountRef.current || runningNode;
    prevNodeOutputCountRef.current = currentCount;
    if (hasNewContent && !userScrolledUpRef.current) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [nodeOutputs, runningNode]);

  const messageEntries = Object.entries(nodeOutputs).filter(([key]) => {
    const base = key.replace(/:\d+$/, "");
    return base !== "receive" && base !== "respond";
  });

  const isTerminal = TERMINAL_STATES.has(taskState?.state);
  const taskStatus = taskState?.state;

  // Idle state
  if (runState === "idle") {
    return (
      <Box
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          height: "100%",
          gap: 16,
          padding: 32,
        }}
      >
        <Box
          style={{
            width: 48,
            height: 48,
            border: "1px solid var(--hud-border)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "var(--hud-text-dimmed)",
            fontSize: 20,
          }}
        >
          ◈
        </Box>
        <Stack gap={6} align="center">
          <Text
            style={{
              color: "var(--hud-text-dimmed)",
              fontFamily: "monospace",
              fontSize: 12,
              letterSpacing: "2px",
              textTransform: "uppercase",
            }}
          >
            AWAITING DISPATCH
          </Text>
          <Text
            style={{
              color: "var(--hud-border)",
              fontFamily: "monospace",
              fontSize: 11,
              textAlign: "center",
            }}
          >
            Fill in the form and dispatch an analysis to see the live agent feed here.
          </Text>
        </Stack>
      </Box>
    );
  }

  return (
    <Box
      ref={scrollContainerRef}
      onScroll={handleScroll}
      style={{
        flex: 1,
        overflowY: "auto",
        padding: "12px 16px",
      }}
    >
      {/* Baseline fetch system message */}
      {["fetching_baseline", "running_analyst", "done", "error"].includes(runState) && (
        <SystemMessage text="── BASELINE FETCH ──" color="var(--hud-text-dimmed)" />
      )}
      {runState === "fetching_baseline" && (
        <Box style={{ padding: "8px 0", animation: "af-fade-in 0.2s ease-out" }}>
          <Group gap={8} align="center">
            <Box style={{ width: 34, height: 34, border: "1px solid var(--hud-border)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 10, color: "var(--hud-text-dimmed)", fontFamily: "monospace" }}>BL</Box>
            <Box>
              <Text size="xs" style={{ color: "var(--hud-text-dimmed)", fontFamily: "monospace", fontSize: 10, letterSpacing: "0.5px", marginBottom: 4 }}>Baseline Store</Text>
              <Box style={{ background: "var(--hud-bg-panel)", border: "1px solid var(--hud-border)", padding: "8px 14px", display: "flex", gap: 4 }}>
                <TypingDots color="var(--hud-text-dimmed)" />
              </Box>
            </Box>
          </Group>
        </Box>
      )}
      {["running_analyst", "writing_baseline", "done", "error"].includes(runState) && (
        <Box style={{ padding: "8px 0", borderBottom: "1px solid var(--hud-border)", animation: "af-fade-in 0.2s ease-out" }}>
          <Group gap={8} align="flex-start">
            <Box style={{ width: 34, height: 34, flexShrink: 0, border: "1px solid var(--hud-green)44", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 10, color: "var(--hud-green)", fontFamily: "monospace", background: "var(--hud-bg-deep)" }}>BL</Box>
            <Box style={{ flex: 1, minWidth: 0 }}>
              <Group gap={6} mb={4}>
                <Text size="xs" style={{ color: "var(--hud-green)", fontFamily: "monospace", fontSize: 11, fontWeight: 700 }}>Baseline Store</Text>
                <Badge size="xs" radius={0} variant="outline" styles={{ root: { fontFamily: "monospace", fontSize: 9, borderColor: "var(--hud-green)33", color: "var(--hud-text-dimmed)" } }}>
                  {fetchedBaseline === null ? "NO PRIOR BASELINE" : fetchedBaseline ? `v${fetchedBaseline.version_number} LOADED` : "BASELINE LOADED"}
                </Badge>
              </Group>
              <Box style={{ background: "var(--hud-bg-panel)", border: "1px solid var(--hud-green)1a", padding: "7px 13px" }}>
                {fetchedBaseline ? (
                  <Text size="xs" style={{ color: "var(--hud-text-primary)", fontFamily: "monospace", fontSize: 11, whiteSpace: "pre-wrap" }}>
                    {fetchedBaseline.narrative?.slice(0, 300)}{fetchedBaseline.narrative?.length > 300 ? "…" : ""}
                  </Text>
                ) : (
                  <Text size="xs" style={{ color: "var(--hud-text-dimmed)", fontFamily: "monospace", fontSize: 11 }}>
                    {fetchedBaseline === null ? "No prior baseline — this is the first run for this topic." : "Historical baseline retrieved for topic context."}
                  </Text>
                )}
              </Box>
            </Box>
          </Group>
        </Box>
      )}

      {/* Analyst section */}
      {["running_analyst", "done", "error"].includes(runState) && (
        <SystemMessage text="── LEAD ANALYST A ──" color="var(--hud-cyan)" />
      )}

      {messageEntries.length === 0 && runState === "running_analyst" && !runningNode && (
        <Box style={{ padding: "8px 0" }}>
          <Group gap={8} align="center">
            <Box style={{ width: 34, height: 34, border: "1px solid var(--hud-cyan)44", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 10, color: "var(--hud-cyan)", fontFamily: "monospace", background: "var(--hud-bg-deep)" }}>LA</Box>
            <Box>
              <Text size="xs" style={{ color: "var(--hud-cyan)", fontFamily: "monospace", fontSize: 10, marginBottom: 4, letterSpacing: "0.5px" }}>Lead Analyst A</Text>
              <Box style={{ background: "var(--hud-bg-panel)", border: "1px solid var(--hud-cyan)22", padding: "8px 14px", display: "flex", gap: 4 }}>
                <TypingDots color="var(--hud-cyan)" />
              </Box>
            </Box>
          </Group>
        </Box>
      )}

      <Stack gap={0}>
        {messageEntries.map(([key, payloadStr], i) => (
          <MessageBubble key={key} nodeKey={key} payloadStr={payloadStr} animIndex={i} />
        ))}
        {runningNode && !isTerminal && <TypingIndicator nodeKey={runningNode} />}
      </Stack>

      {/* Terminal state footer */}
      {isTerminal && messageEntries.length > 0 && (
        <Box mt={12}>
          <SystemMessage
            text={taskStatus === "completed" ? "── ANALYSIS COMPLETE ──" : `── ${taskStatus?.toUpperCase()} ──`}
            color={taskStatus === "completed" ? "var(--hud-green)" : "var(--hud-red)"}
          />
          {taskId && (
            <Text
              size="xs"
              style={{ color: "var(--hud-text-dimmed)", fontFamily: "monospace", fontSize: 10, textAlign: "center", marginTop: 4 }}
            >
              TASK {taskId.slice(0, 8)}…{taskId.slice(-4)}
            </Text>
          )}
        </Box>
      )}

      {/* Baseline write section */}
      {["writing_baseline", "done"].includes(runState) && (
        <>
          <SystemMessage text="── BASELINE WRITE ──" color="var(--hud-cyan)" />
          <Box style={{ padding: "8px 0", animation: "af-fade-in 0.2s ease-out" }}>
            <Group gap={8} align="flex-start">
              <Box style={{ width: 34, height: 34, flexShrink: 0, border: `1px solid ${baselineResult ? "var(--hud-green)" : "var(--hud-amber)"}44`, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 10, color: baselineResult ? "var(--hud-green)" : "var(--hud-amber)", fontFamily: "monospace", background: "var(--hud-bg-deep)" }}>BS</Box>
              <Box style={{ flex: 1, minWidth: 0 }}>
                <Group gap={6} mb={4}>
                  <Text size="xs" style={{ color: baselineResult ? "var(--hud-green)" : "var(--hud-amber)", fontFamily: "monospace", fontSize: 11, fontWeight: 700 }}>Baseline Store</Text>
                  <Badge size="xs" radius={0} variant="outline" styles={{ root: { fontFamily: "monospace", fontSize: 9, borderColor: baselineResult ? "var(--hud-green)33" : "var(--hud-amber)33", color: "var(--hud-text-dimmed)" } }}>
                    {baselineResult ? "WRITTEN" : "WRITING..."}
                  </Badge>
                </Group>
                <Box style={{ background: "var(--hud-bg-panel)", border: `1px solid ${baselineResult ? "var(--hud-green)" : "var(--hud-amber)"}1a`, padding: "7px 13px" }}>
                  {baselineResult ? (
                    <Stack gap={4}>
                      <Text size="xs" style={{ color: "var(--hud-text-primary)", fontFamily: "monospace", fontSize: 11 }}>
                        {baselineResult.fromVersion != null ? `v${baselineResult.fromVersion}` : "—"} → <strong style={{ color: "var(--hud-green)" }}>v{baselineResult.toVersion}</strong> saved
                      </Text>
                      {baselineResult.deltaSummary && (
                        <Text size="xs" style={{ color: "var(--hud-text-dimmed)", fontFamily: "monospace", fontSize: 10, whiteSpace: "pre-wrap" }}>
                          {baselineResult.deltaSummary.slice(0, 200)}{baselineResult.deltaSummary.length > 200 ? "…" : ""}
                        </Text>
                      )}
                      {baselineResult.claimsAdded?.length > 0 && (
                        <Text size="xs" style={{ color: "var(--hud-green)", fontFamily: "monospace", fontSize: 10 }}>
                          +{baselineResult.claimsAdded.length} claim{baselineResult.claimsAdded.length !== 1 ? "s" : ""} added
                        </Text>
                      )}
                      {baselineResult.claimsSuperseded?.length > 0 && (
                        <Text size="xs" style={{ color: "var(--hud-red)", fontFamily: "monospace", fontSize: 10 }}>
                          -{baselineResult.claimsSuperseded.length} claim{baselineResult.claimsSuperseded.length !== 1 ? "s" : ""} superseded
                        </Text>
                      )}
                    </Stack>
                  ) : (
                    <Box style={{ display: "flex", gap: 4 }}>
                      <TypingDots color="var(--hud-amber)" />
                    </Box>
                  )}
                </Box>
              </Box>
            </Group>
          </Box>
        </>
      )}

      <div ref={bottomRef} />
    </Box>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

function extractUpdatedNarrative(analysisText, oldNarrative) {
  const lines = analysisText.split("\n");
  for (const marker of ["## Updated Baseline", "## Baseline Change Summary", "## Baseline Update"]) {
    const idx = lines.findIndex((l) => l.toLowerCase().includes(marker.toLowerCase()));
    if (idx !== -1) {
      const section = [];
      for (let i = idx + 1; i < lines.length; i++) {
        if (lines[i].startsWith("## ") && section.length) break;
        section.push(lines[i]);
      }
      const text = section.join("\n").trim();
      if (text) return text;
    }
  }
  for (const marker of ["## Executive Summary", "## Primary Assessment"]) {
    const idx = lines.findIndex((l) => l.toLowerCase().includes(marker.toLowerCase()));
    if (idx !== -1) {
      const section = [];
      for (let i = idx + 1; i < lines.length; i++) {
        if (lines[i].startsWith("## ") && section.length) break;
        section.push(lines[i]);
      }
      const summary = section.join("\n").trim();
      if (summary) return oldNarrative ? `${summary}\n\n[Prior baseline]\n${oldNarrative}` : summary;
    }
  }
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
  return { deltaSummary, claimsAdded: claimsAdded.slice(0, 10), claimsSuperseded: claimsSuperseded.slice(0, 10) };
}

export default function AnalystFlowPage({ agents, onBaselineWritten }) {
  const [runState, setRunState] = useState("idle");
  const [taskState, setTaskState] = useState(null);
  const [taskId, setTaskId] = useState(null);
  const [error, setError] = useState(null);
  const [baselineResult, setBaselineResult] = useState(null); // {fromVersion, toVersion, deltaSummary, ...}
  const [fetchedBaseline, setFetchedBaseline] = useState(undefined); // undefined=not loaded, null=none, obj=loaded

  const wsCleanupRef = useRef(null);
  const taskStateRef = useRef(null);

  useEffect(() => () => wsCleanupRef.current?.(), []);

  const handleRun = useCallback(
    async ({ topic, topicLabel, report, keyQuestions, baselineOverride }) => {
      if (!["idle"].includes(runState)) return;

      const leadAnalystAgent = agents?.find(
        (a) => a.id?.startsWith("lead-analyst") && a.status === "online"
      );
      if (!leadAnalystAgent) return;

      setError(null);
      setTaskState(null);
      setTaskId(null);
      setBaselineResult(null);
      setFetchedBaseline(undefined);
      taskStateRef.current = null;

      try {
        // ── Step 1: Fetch baseline ────────────────────────────────────────────
        setRunState("fetching_baseline");
        await ensureTopicRegistered(topic, topicLabel);
        const baseline = baselineOverride
          ? { narrative: baselineOverride, version_number: null }
          : await fetchBaseline(topic);
        setFetchedBaseline(baseline ?? null);

        // ── Step 2: Lead analyst ──────────────────────────────────────────────
        setRunState("running_analyst");

        const analystTask = await dispatchTask(leadAnalystAgent.id, {
          text: JSON.stringify({
            text: report,
            baselines: baseline?.narrative ?? "",
            key_questions: keyQuestions,
          }),
        });

        setTaskId(analystTask.task_id);

        // Subscribe via WebSocket for live updates
        wsCleanupRef.current?.();
        wsCleanupRef.current = subscribeToTask(analystTask.task_id, (msg) => {
          setTaskState(msg);
          taskStateRef.current = msg;
        });

        // Poll as fallback until terminal
        let analystResult = analystTask;
        while (!TERMINAL_STATES.has(analystResult.state)) {
          await new Promise((r) => setTimeout(r, 2000));
          analystResult = await fetchTask(leadAnalystAgent.id, analystTask.task_id);
          setTaskState((prev) => ({ ...(prev ?? {}), ...analystResult }));
          taskStateRef.current = { ...(taskStateRef.current ?? {}), ...analystResult };
        }

        wsCleanupRef.current?.();
        wsCleanupRef.current = null;

        if (analystResult.state === "failed") {
          throw new Error("Lead analyst task failed");
        }

        // ── Step 3: Write baseline ────────────────────────────────────────────
        setRunState("writing_baseline");
        const analysisText = analystResult.output_text ?? "";
        const oldNarrative = (baseline ?? null)?.narrative ?? "";
        const newNarrative = extractUpdatedNarrative(analysisText, oldNarrative);
        const { deltaSummary, claimsAdded, claimsSuperseded } = extractDeltaFields(analysisText);
        try {
          const versionResult = await writeBaselineVersion(topic, newNarrative);
          await writeBaselineDelta(topic, {
            from_version: (baseline ?? null)?.version_number ?? null,
            to_version: versionResult.version_number,
            delta_summary: deltaSummary,
            claims_added: claimsAdded,
            claims_superseded: claimsSuperseded,
          });
          setBaselineResult({
            fromVersion: (baseline ?? null)?.version_number ?? null,
            toVersion: versionResult.version_number,
            deltaSummary,
            claimsAdded,
            claimsSuperseded,
          });
        } catch (writeErr) {
          console.warn("Baseline write failed:", writeErr);
          // Non-fatal — continue to done
        }

        setRunState("done");
        onBaselineWritten?.();
      } catch (err) {
        console.error("Analyst flow error:", err);
        setError(err.message ?? String(err));
        setRunState("error");
      }
    },
    [runState, agents]
  );

  const handleReset = useCallback(() => {
    wsCleanupRef.current?.();
    wsCleanupRef.current = null;
    setRunState("idle");
    setError(null);
    setTaskState(null);
    setTaskId(null);
    setBaselineResult(null);
    setFetchedBaseline(undefined);
    taskStateRef.current = null;
  }, []);

  const isRunning = runState === "fetching_baseline" || runState === "running_analyst" || runState === "writing_baseline";
  const taskStatus = taskState?.state;

  return (
    <Box style={{ display: "flex", flexDirection: "column", height: "calc(100vh - 180px)" }}>
      {/* Top status strip */}
      <Box
        style={{
          background: "var(--hud-bg-deep)",
          border: "1px solid var(--hud-border)",
          borderBottom: "none",
          padding: "8px 16px",
          flexShrink: 0,
        }}
      >
        <Group justify="space-between" align="center" mb={6}>
          <Text
            size="xs"
            style={{
              color: "var(--hud-cyan)",
              letterSpacing: "2px",
              fontSize: 10,
              fontFamily: "monospace",
              textTransform: "uppercase",
            }}
          >
            ◈ LEAD ANALYST A — LIVE WORKFLOW
          </Text>
          <Group gap={8}>
            {error && (
              <Text size="xs" style={{ color: "var(--hud-red)", fontFamily: "monospace", fontSize: 10 }}>
                ERR: {error}
              </Text>
            )}
            {taskStatus && (
              <Badge
                size="xs"
                radius={0}
                color={
                  taskStatus === "completed" ? "hud-green"
                  : taskStatus === "failed" ? "hud-red"
                  : taskStatus === "canceled" ? "hud-violet"
                  : "hud-amber"
                }
                variant="light"
                styles={{ root: { fontFamily: "monospace", fontSize: 9, letterSpacing: "1px" } }}
              >
                {taskStatus.toUpperCase()}
              </Badge>
            )}
          </Group>
        </Group>
        <StepBar currentStep={runState} taskState={taskState} />
      </Box>

      {/* Main two-panel layout */}
      <Box style={{ display: "flex", flex: 1, minHeight: 0, border: "1px solid var(--hud-border)" }}>
        {/* Left: Input form (fixed width) */}
        <Box style={{ width: 380, flexShrink: 0, minHeight: 0, overflowY: "auto" }}>
          <InputPanel
            onRun={handleRun}
            isRunning={isRunning}
            onReset={handleReset}
            runState={runState}
            agents={agents}
          />
        </Box>

        {/* Right: Live chat feed */}
        <Box
          style={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            minHeight: 0,
            background: "var(--hud-bg-deep)",
          }}
        >
          {/* Feed header */}
          <Box
            style={{
              padding: "10px 16px",
              borderBottom: "1px solid var(--hud-border)",
              background: "var(--hud-bg-panel)",
              flexShrink: 0,
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            <Text
              size="xs"
              style={{
                color: "var(--hud-text-dimmed)",
                fontFamily: "monospace",
                fontSize: 10,
                letterSpacing: "1.5px",
                textTransform: "uppercase",
              }}
            >
              ◈ AGENT COMMS FEED
            </Text>
            {taskId && (
              <Text size="xs" style={{ color: "var(--hud-text-dimmed)", fontFamily: "monospace", fontSize: 10 }}>
                TASK {taskId.slice(0, 8)}…
              </Text>
            )}
          </Box>

          <ChatFeed
            taskState={taskState}
            runState={runState}
            taskId={taskId}
            fetchedBaseline={fetchedBaseline}
            baselineResult={baselineResult}
          />
        </Box>
      </Box>

      {/* Keyframe animations */}
      <style>{`
        @keyframes af-typing-bounce {
          0%, 60%, 100% { transform: translateY(0); opacity: 0.4; }
          30% { transform: translateY(-5px); opacity: 1; }
        }
        @keyframes af-fade-in {
          from { opacity: 0; transform: translateY(6px); }
          to   { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </Box>
  );
}
