import { useState } from "react";
import { Drawer, Stack, Text, Badge, Code, Button, Group } from "@mantine/core";
import { cancelTask } from "../hooks/useApi";

const stateColors = {
  completed: "green",
  working: "yellow",
  submitted: "gray",
  canceled: "red",
  failed: "red",
  "input-required": "violet",
};

export default function TaskDetailDrawer({ task, onClose, onCancelled }) {
  const [cancelling, setCancelling] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);

  if (!task) return null;

  const canCancel = ["submitted", "working"].includes(task.state);

  const handleCancel = async () => {
    if (!confirmCancel) {
      setConfirmCancel(true);
      return;
    }
    setCancelling(true);
    try {
      await cancelTask(task.agent_id, task.task_id);
      onCancelled(task.task_id);
    } catch (err) {
      alert("Cancel failed: " + err.message);
    } finally {
      setCancelling(false);
      setConfirmCancel(false);
    }
  };

  const handleClose = () => {
    setConfirmCancel(false);
    onClose();
  };

  return (
    <Drawer
      opened={!!task}
      onClose={handleClose}
      title="Task Detail"
      position="right"
      size="lg"
    >
      <Stack gap="md">
        <div>
          <Text size="xs" c="dimmed" tt="uppercase" fw={500}>
            Task ID
          </Text>
          <Code>{task.task_id}</Code>
        </div>

        <div>
          <Text size="xs" c="dimmed" tt="uppercase" fw={500}>
            Agent
          </Text>
          <Text size="sm">{task.agent_id}</Text>
        </div>

        <div>
          <Text size="xs" c="dimmed" tt="uppercase" fw={500}>
            State
          </Text>
          <Badge color={stateColors[task.state] || "gray"} variant="light">
            {task.state}
          </Badge>
        </div>

        <div>
          <Text size="xs" c="dimmed" tt="uppercase" fw={500}>
            Input
          </Text>
          <Code block mt="xs">
            {task.input_text || "—"}
          </Code>
        </div>

        <div>
          <Text size="xs" c="dimmed" tt="uppercase" fw={500}>
            Output
          </Text>
          <Code block mt="xs">
            {task.output_text || "—"}
          </Code>
        </div>

        <div>
          <Text size="xs" c="dimmed" tt="uppercase" fw={500}>
            Created
          </Text>
          <Text size="sm">
            {task.created_at
              ? new Date(task.created_at * 1000).toLocaleString()
              : "—"}
          </Text>
        </div>

        {canCancel && (
          <Group mt="md">
            <Button
              color={confirmCancel ? "red" : "gray"}
              variant={confirmCancel ? "filled" : "light"}
              onClick={handleCancel}
              loading={cancelling}
            >
              {confirmCancel ? "Click again to confirm" : "Cancel Task"}
            </Button>
          </Group>
        )}
      </Stack>
    </Drawer>
  );
}
