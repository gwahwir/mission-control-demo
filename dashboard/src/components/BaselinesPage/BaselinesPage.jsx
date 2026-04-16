import { useState, useEffect, useCallback, useRef } from "react";
import { Box, Text, Badge, Stack, Group, Divider } from "@mantine/core";
import ReactMarkdown from "react-markdown";
import remarkBreaks from "remark-breaks";

// ── Data fetching ─────────────────────────────────────────────────────────────

async function fetchTopics() {
  const res = await fetch("/topics");
  if (!res.ok) throw new Error("Failed to fetch topics");
  const data = await res.json();
  return data.topics ?? [];
}

async function fetchHistory(topicPath) {
  const res = await fetch(`/baselines/${topicPath}/history`);
  if (!res.ok) throw new Error("Failed to fetch history");
  return res.json();
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toISOString().replace("T", " ").slice(0, 19) + " UTC";
}

// ── Topic list (left panel) ───────────────────────────────────────────────────

function TopicRow({ topic, selected, onClick }) {
  const isSelected = selected?.topic_path === topic.topic_path;
  return (
    <Box
      onClick={onClick}
      style={{
        padding: "10px 14px",
        cursor: "pointer",
        borderLeft: isSelected ? "2px solid var(--hud-cyan)" : "2px solid transparent",
        background: isSelected ? "rgba(0,212,255,0.05)" : "transparent",
        transition: "background 0.15s, border-color 0.15s",
      }}
      onMouseEnter={(e) => { if (!isSelected) e.currentTarget.style.background = "rgba(255,255,255,0.02)"; }}
      onMouseLeave={(e) => { if (!isSelected) e.currentTarget.style.background = "transparent"; }}
    >
      <Text
        size="xs"
        style={{
          color: isSelected ? "var(--hud-cyan)" : "var(--hud-text-primary)",
          fontFamily: "monospace",
          fontWeight: isSelected ? 700 : 400,
          fontSize: 12,
          letterSpacing: "0.5px",
          marginBottom: 2,
        }}
      >
        {topic.display_name || topic.topic_path}
      </Text>
      <Text
        size="xs"
        style={{
          color: "var(--hud-text-dimmed)",
          fontFamily: "monospace",
          fontSize: 10,
          letterSpacing: "0.3px",
        }}
      >
        {topic.topic_path}
      </Text>
    </Box>
  );
}

// ── Delta block ───────────────────────────────────────────────────────────────

function DeltaBlock({ delta }) {
  const [open, setOpen] = useState(false);
  if (!delta) return null;

  return (
    <Box
      style={{
        margin: "8px 0 0 0",
        borderLeft: "2px solid rgba(0,212,255,0.2)",
        paddingLeft: 12,
      }}
    >
      <Text
        size="xs"
        style={{
          color: "var(--hud-cyan)",
          fontFamily: "monospace",
          fontSize: 10,
          letterSpacing: "1px",
          cursor: "pointer",
          userSelect: "none",
        }}
        onClick={() => setOpen((v) => !v)}
      >
        {open ? "[ hide delta ]" : "[ show delta ]"}
      </Text>
      {open && (
        <Box mt={8}>
          {delta.delta_summary && (
            <Text
              size="xs"
              style={{
                color: "var(--hud-text-dimmed)",
                fontFamily: "monospace",
                fontSize: 11,
                lineHeight: 1.6,
                whiteSpace: "pre-wrap",
                marginBottom: 8,
              }}
            >
              {delta.delta_summary}
            </Text>
          )}
          {delta.claims_added?.length > 0 && (
            <Box mb={6}>
              <Text size="xs" style={{ color: "var(--hud-text-dimmed)", fontFamily: "monospace", fontSize: 10, letterSpacing: "1px", marginBottom: 4 }}>
                CLAIMS ADDED
              </Text>
              <Stack gap={2}>
                {delta.claims_added.map((c, i) => (
                  <Text key={i} size="xs" style={{ color: "var(--hud-green)", fontFamily: "monospace", fontSize: 11 }}>
                    + {c}
                  </Text>
                ))}
              </Stack>
            </Box>
          )}
          {delta.claims_superseded?.length > 0 && (
            <Box>
              <Text size="xs" style={{ color: "var(--hud-text-dimmed)", fontFamily: "monospace", fontSize: 10, letterSpacing: "1px", marginBottom: 4 }}>
                CLAIMS SUPERSEDED
              </Text>
              <Stack gap={2}>
                {delta.claims_superseded.map((c, i) => (
                  <Text key={i} size="xs" style={{ color: "var(--hud-red)", fontFamily: "monospace", fontSize: 11 }}>
                    - {c}
                  </Text>
                ))}
              </Stack>
            </Box>
          )}
        </Box>
      )}
    </Box>
  );
}

// ── Version card ──────────────────────────────────────────────────────────────

function VersionCard({ version, delta, isLatest }) {
  const [expanded, setExpanded] = useState(isLatest);
  const preview = (version.narrative ?? "").replace(/[#*`]/g, "").slice(0, 200).trim();
  const isLong = (version.narrative ?? "").length > 200;

  return (
    <Box
      style={{
        border: "1px solid var(--hud-border)",
        background: isLatest ? "rgba(0,212,255,0.02)" : "var(--hud-bg-panel)",
        marginBottom: 10,
      }}
    >
      {/* Version header */}
      <Box
        style={{
          padding: "10px 14px",
          borderBottom: expanded ? "1px solid var(--hud-border)" : "none",
          display: "flex",
          alignItems: "center",
          gap: 10,
          cursor: "pointer",
        }}
        onClick={() => setExpanded((v) => !v)}
      >
        <Badge
          size="sm"
          radius={0}
          variant={isLatest ? "filled" : "outline"}
          color={isLatest ? "hud-cyan" : "gray"}
          styles={{ root: { fontFamily: "monospace", fontSize: 10, letterSpacing: "1px" } }}
        >
          v{version.version_number}
        </Badge>
        {isLatest && (
          <Badge
            size="xs"
            radius={0}
            variant="light"
            color="hud-green"
            styles={{ root: { fontFamily: "monospace", fontSize: 9, letterSpacing: "1px" } }}
          >
            CURRENT
          </Badge>
        )}
        <Text size="xs" style={{ color: "var(--hud-text-dimmed)", fontFamily: "monospace", fontSize: 10, flex: 1 }}>
          {formatDate(version.created_at)}
        </Text>
        <Text size="xs" style={{ color: "var(--hud-text-dimmed)", fontFamily: "monospace", fontSize: 10, letterSpacing: "0.5px" }}>
          {expanded ? "▾" : "▸"}
        </Text>
      </Box>

      {/* Narrative body */}
      <Box style={{ padding: "10px 14px" }}>
        {!expanded ? (
          <Text
            size="xs"
            style={{
              color: "var(--hud-text-dimmed)",
              fontFamily: "monospace",
              fontSize: 11,
              lineHeight: 1.6,
              whiteSpace: "pre-wrap",
            }}
          >
            {preview}{isLong ? "…" : ""}
          </Text>
        ) : (
          <Box
            className="markdown-output"
            style={{
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
                strong: ({ children }) => <strong style={{ color: "var(--hud-cyan)" }}>{children}</strong>,
                h1: ({ children }) => <h1 style={{ color: "var(--hud-cyan)", fontSize: 13, margin: "14px 0 6px", letterSpacing: "1px" }}>{children}</h1>,
                h2: ({ children }) => <h2 style={{ color: "var(--hud-cyan)", fontSize: 12, margin: "12px 0 5px", letterSpacing: "1px" }}>{children}</h2>,
                h3: ({ children }) => <h3 style={{ color: "var(--hud-text-dimmed)", fontSize: 11, margin: "10px 0 4px" }}>{children}</h3>,
                li: ({ children }) => <li style={{ marginBottom: 3 }}>{children}</li>,
                table: ({ children }) => <table style={{ borderCollapse: "collapse", width: "100%", marginBottom: 8 }}>{children}</table>,
                th: ({ children }) => <th style={{ border: "1px solid var(--hud-border)", padding: "4px 8px", color: "var(--hud-text-dimmed)", fontSize: 10, letterSpacing: "0.5px", textAlign: "left", background: "var(--hud-bg-deep)" }}>{children}</th>,
                td: ({ children }) => <td style={{ border: "1px solid var(--hud-border)", padding: "4px 8px", fontSize: 11 }}>{children}</td>,
                code: ({ children }) => <code style={{ background: "var(--hud-bg-deep)", padding: "1px 4px", fontSize: 10 }}>{children}</code>,
                hr: () => <hr style={{ border: "none", borderTop: "1px solid var(--hud-border)", margin: "12px 0" }} />,
              }}
            >
              {version.narrative ?? ""}
            </ReactMarkdown>
          </Box>
        )}

        {/* Delta (shown below the narrative, for non-first versions) */}
        {delta && <DeltaBlock delta={delta} />}
      </Box>
    </Box>
  );
}

// ── Right panel ───────────────────────────────────────────────────────────────

function HistoryPanel({ topic, onRefresh, loading, history, error }) {
  if (!topic) {
    return (
      <Box
        style={{
          flex: 1,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height: "100%",
        }}
      >
        <Stack gap={8} align="center">
          <Text style={{ color: "var(--hud-border)", fontFamily: "monospace", fontSize: 24 }}>◈</Text>
          <Text
            size="xs"
            style={{
              color: "var(--hud-text-dimmed)",
              fontFamily: "monospace",
              fontSize: 11,
              letterSpacing: "1.5px",
              textTransform: "uppercase",
            }}
          >
            Select a topic to view its baseline history
          </Text>
        </Stack>
      </Box>
    );
  }

  const versions = [...(history?.versions ?? [])].sort((a, b) => b.version_number - a.version_number);
  const deltas = history?.deltas ?? [];
  const latestVersion = versions[0]?.version_number;

  // Map deltas by to_version for quick lookup
  const deltaByToVersion = {};
  for (const d of deltas) {
    deltaByToVersion[d.to_version] = d;
  }

  return (
    <Box style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
      {/* Header */}
      <Box
        style={{
          padding: "12px 16px",
          borderBottom: "1px solid var(--hud-border)",
          background: "var(--hud-bg-panel)",
          flexShrink: 0,
        }}
      >
        <Group justify="space-between" align="flex-start">
          <Box>
            <Group gap={10} align="center" mb={4}>
              <Text
                style={{
                  color: "var(--hud-cyan)",
                  fontFamily: "monospace",
                  fontSize: 14,
                  fontWeight: 700,
                  letterSpacing: "1px",
                  textTransform: "uppercase",
                }}
              >
                {topic.display_name || topic.topic_path}
              </Text>
              {latestVersion != null && (
                <Badge
                  size="sm"
                  radius={0}
                  color="hud-green"
                  variant="light"
                  styles={{ root: { fontFamily: "monospace", fontSize: 10, letterSpacing: "1px" } }}
                >
                  CURRENT v{latestVersion}
                </Badge>
              )}
            </Group>
            <Text size="xs" style={{ color: "var(--hud-text-dimmed)", fontFamily: "monospace", fontSize: 10 }}>
              {topic.topic_path}
            </Text>
          </Box>
          <Text
            size="xs"
            style={{
              color: "var(--hud-text-dimmed)",
              fontFamily: "monospace",
              fontSize: 10,
              cursor: "pointer",
              letterSpacing: "1px",
              userSelect: "none",
              marginTop: 2,
            }}
            onClick={onRefresh}
          >
            [ refresh ]
          </Text>
        </Group>
      </Box>

      {/* Version list */}
      <Box style={{ flex: 1, overflowY: "auto", padding: "14px 16px" }}>
        {loading && (
          <Text size="xs" style={{ color: "var(--hud-text-dimmed)", fontFamily: "monospace", fontSize: 11, letterSpacing: "1px" }}>
            LOADING...
          </Text>
        )}
        {error && (
          <Text size="xs" style={{ color: "var(--hud-red)", fontFamily: "monospace", fontSize: 11 }}>
            {error}
          </Text>
        )}
        {!loading && !error && versions.length === 0 && (
          <Text size="xs" style={{ color: "var(--hud-text-dimmed)", fontFamily: "monospace", fontSize: 11 }}>
            No versions found for this topic.
          </Text>
        )}
        {!loading && versions.map((v) => (
          <VersionCard
            key={v.version_number}
            version={v}
            delta={deltaByToVersion[v.version_number] ?? null}
            isLatest={v.version_number === latestVersion}
          />
        ))}
      </Box>
    </Box>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function BaselinesPage({ refreshKey }) {
  const [topics, setTopics] = useState([]);
  const [topicsLoading, setTopicsLoading] = useState(true);
  const [selectedTopic, setSelectedTopic] = useState(null);
  const [history, setHistory] = useState(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState(null);
  const pollRef = useRef(null);

  const loadTopics = useCallback(async () => {
    try {
      const data = await fetchTopics();
      setTopics(data);
    } catch {
      // silently ignore poll failures
    } finally {
      setTopicsLoading(false);
    }
  }, []);

  const loadHistory = useCallback(async (topic) => {
    if (!topic) return;
    setHistoryLoading(true);
    setHistoryError(null);
    try {
      const data = await fetchHistory(topic.topic_path);
      setHistory(data);
    } catch (e) {
      setHistoryError(e.message ?? "Failed to load history");
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  // Initial load + 30s poll for topics
  useEffect(() => {
    loadTopics();
    pollRef.current = setInterval(loadTopics, 30000);
    return () => clearInterval(pollRef.current);
  }, [loadTopics]);

  // Load history when selected topic changes or a baseline write completes
  useEffect(() => {
    setHistory(null);
    loadHistory(selectedTopic);
  }, [selectedTopic, loadHistory, refreshKey]);

  return (
    <Box
      style={{
        display: "flex",
        height: "calc(100vh - 180px)",
        border: "1px solid var(--hud-border)",
        overflow: "hidden",
      }}
    >
      {/* Left: topic list */}
      <Box
        style={{
          width: 280,
          flexShrink: 0,
          borderRight: "1px solid var(--hud-border)",
          background: "var(--hud-bg-panel)",
          display: "flex",
          flexDirection: "column",
        }}
      >
        {/* Panel header */}
        <Box
          style={{
            padding: "12px 14px",
            borderBottom: "1px solid var(--hud-border)",
            background: "var(--hud-bg-deep)",
            flexShrink: 0,
          }}
        >
          <Text
            size="xs"
            style={{
              color: "var(--hud-cyan)",
              fontFamily: "monospace",
              fontSize: 11,
              letterSpacing: "2px",
              textTransform: "uppercase",
            }}
          >
            ◈ TOPICS
          </Text>
        </Box>

        {/* Topic rows */}
        <Box style={{ flex: 1, overflowY: "auto" }}>
          {topicsLoading && (
            <Text size="xs" style={{ color: "var(--hud-text-dimmed)", fontFamily: "monospace", fontSize: 10, padding: "12px 14px", letterSpacing: "1px" }}>
              LOADING...
            </Text>
          )}
          {!topicsLoading && topics.length === 0 && (
            <Text size="xs" style={{ color: "var(--hud-text-dimmed)", fontFamily: "monospace", fontSize: 10, padding: "12px 14px" }}>
              No topics registered yet.
            </Text>
          )}
          {topics.map((t) => (
            <TopicRow
              key={t.topic_path}
              topic={t}
              selected={selectedTopic}
              onClick={() => setSelectedTopic(t)}
            />
          ))}
        </Box>
      </Box>

      {/* Right: history panel */}
      <Box style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0, background: "var(--hud-bg-deep)" }}>
        <HistoryPanel
          topic={selectedTopic}
          onRefresh={() => loadHistory(selectedTopic)}
          loading={historyLoading}
          history={history}
          error={historyError}
        />
      </Box>
    </Box>
  );
}
