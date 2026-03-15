import { useState } from "react";
import { Group, Select, TextInput, Button, Title, Alert } from "@mantine/core";
import { dispatchTask } from "../hooks/useApi";

export default function TaskLauncher({ agents, onTaskCreated }) {
  const [agentId, setAgentId] = useState(null);
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!agentId || !text.trim()) return;

    setLoading(true);
    setError(null);
    try {
      const result = await dispatchTask(agentId, text.trim());
      onTaskCreated(result);
      setText("");
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const agentOptions = agents
    .filter((a) => a.status === "online")
    .map((a) => ({ value: a.id, label: a.name }));

  return (
    <div>
      <Title order={3} mb="md">
        Launch Task
      </Title>
      <form onSubmit={handleSubmit}>
        <Group align="end" grow>
          <Select
            label="Agent"
            placeholder="Select agent..."
            data={agentOptions}
            value={agentId}
            onChange={setAgentId}
            style={{ flex: "0 0 200px" }}
          />
          <TextInput
            label="Prompt"
            placeholder="Enter a task prompt..."
            value={text}
            onChange={(e) => setText(e.currentTarget.value)}
          />
          <Button
            type="submit"
            loading={loading}
            disabled={!agentId || !text.trim()}
            style={{ flex: "0 0 auto" }}
          >
            Send
          </Button>
        </Group>
      </form>
      {error && (
        <Alert color="red" mt="sm">
          {error}
        </Alert>
      )}
    </div>
  );
}
