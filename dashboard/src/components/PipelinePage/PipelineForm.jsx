import { useState } from "react";
import { TextInput, Textarea, Button, Group, SimpleGrid, Text, Collapse, Box } from "@mantine/core";

const DEFAULT_TOPIC = "geo.middle_east.iran";
const DEFAULT_LABEL = "Iran";
const DEFAULT_KEY_QUESTIONS =
  "1. Are there new signals of diplomatic engagement or breakdown?\n2. What is the risk of escalation in the next 30 days?\n3. What is the impact on South East Asia's energy security?\n4. What is the impact on South East Asia's supply chain dependencies?";

export default function PipelineForm({ onRun, isRunning }) {
  const [topic, setTopic] = useState(DEFAULT_TOPIC);
  const [topicLabel, setTopicLabel] = useState(DEFAULT_LABEL);
  const [report, setReport] = useState("");
  const [keyQuestions, setKeyQuestions] = useState(DEFAULT_KEY_QUESTIONS);
  const [baselineOverride, setBaselineOverride] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);

  const canRun = !isRunning && topic.trim() && report.trim() && keyQuestions.trim();

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!canRun) return;
    onRun({ topic, topicLabel, report, keyQuestions, baselineOverride: baselineOverride.trim() || null });
  };

  const inputStyles = {
    input: {
      background: "var(--hud-bg-surface)",
      border: "1px solid var(--hud-border)",
      borderRadius: 0,
      color: "var(--hud-text-primary)",
      fontFamily: "monospace",
      fontSize: 12,
    },
    label: {
      color: "var(--hud-text-dimmed)",
      fontSize: 11,
      letterSpacing: "1px",
      textTransform: "uppercase",
      marginBottom: 4,
    },
  };

  return (
    <form onSubmit={handleSubmit}>
      <SimpleGrid cols={2} spacing="md" mb="sm">
        <TextInput
          label="Topic Path"
          placeholder="geo.middle_east.iran"
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          styles={inputStyles}
          required
        />
        <TextInput
          label="Topic Label"
          placeholder="Iran"
          value={topicLabel}
          onChange={(e) => setTopicLabel(e.target.value)}
          styles={inputStyles}
        />
      </SimpleGrid>

      <Textarea
        label="Incoming Report"
        placeholder="Paste the incoming report or intelligence text here..."
        value={report}
        onChange={(e) => setReport(e.target.value)}
        styles={inputStyles}
        minRows={3}
        autosize
        maxRows={8}
        mb="sm"
        required
      />

      <Textarea
        label="Key Questions (used for relevancy check and sent to all specialists)"
        placeholder="1. Question one&#10;2. Question two"
        value={keyQuestions}
        onChange={(e) => setKeyQuestions(e.target.value)}
        styles={inputStyles}
        minRows={3}
        autosize
        mb="sm"
        required
      />

      <Box mb="sm">
        <Text
          size="xs"
          style={{
            color: "var(--hud-cyan)",
            cursor: "pointer",
            letterSpacing: "1px",
            textTransform: "uppercase",
            fontSize: 11,
            userSelect: "none",
          }}
          onClick={() => setShowAdvanced((v) => !v)}
        >
          {showAdvanced ? "▾" : "▸"} Advanced options
        </Text>
        <Collapse in={showAdvanced}>
          <Box mt="sm">
            <Textarea
              label="Baseline Override (optional — skips baseline fetch and uses this text instead)"
              placeholder="Paste an existing baseline narrative here to override the stored baseline..."
              value={baselineOverride}
              onChange={(e) => setBaselineOverride(e.target.value)}
              styles={inputStyles}
              minRows={3}
              autosize
            />
          </Box>
        </Collapse>
      </Box>

      <Group justify="flex-end">
        <Button
          type="submit"
          disabled={!canRun}
          loading={isRunning}
          variant="outline"
          color="hud-cyan"
          radius={0}
          styles={{
            root: {
              fontFamily: "monospace",
              letterSpacing: "2px",
              fontSize: 12,
              textTransform: "uppercase",
              borderColor: canRun ? "var(--hud-cyan)" : "var(--hud-border)",
            },
          }}
        >
          {isRunning ? "RUNNING..." : "RUN PIPELINE"}
        </Button>
      </Group>
    </form>
  );
}
