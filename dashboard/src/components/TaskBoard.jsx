import { SimpleGrid, Card, Text, Badge, Title, Group, Stack } from "@mantine/core";

const STATE_COLUMNS = [
  { key: "submitted", label: "Queued", color: "gray" },
  { key: "working", label: "Working", color: "yellow" },
  { key: "input-required", label: "Input Required", color: "violet" },
  { key: "completed", label: "Done", color: "green" },
  { key: "canceled", label: "Cancelled", color: "red" },
  { key: "failed", label: "Failed", color: "red" },
];

export default function TaskBoard({ tasks, onSelectTask }) {
  const grouped = {};
  for (const col of STATE_COLUMNS) grouped[col.key] = [];
  for (const t of tasks) {
    if (grouped[t.state]) grouped[t.state].push(t);
  }

  return (
    <div>
      <Title order={3} mb="md">
        Active Tasks
      </Title>
      <SimpleGrid cols={{ base: 2, md: 3, lg: 6 }}>
        {STATE_COLUMNS.map((col) => (
          <div key={col.key}>
            <Group gap="xs" mb="xs">
              <Text size="sm" fw={500} c={col.color}>
                {col.label}
              </Text>
              <Badge size="xs" variant="default" circle>
                {grouped[col.key].length}
              </Badge>
            </Group>
            <Stack gap="xs">
              {grouped[col.key].map((task) => (
                <Card
                  key={task.task_id}
                  shadow="xs"
                  padding="xs"
                  withBorder
                  onClick={() => onSelectTask(task)}
                  style={{ cursor: "pointer" }}
                >
                  <Text size="xs" lineClamp={1}>
                    {task.input_text}
                  </Text>
                  <Text size="xs" c="dimmed" mt={4}>
                    {task.agent_id}
                  </Text>
                </Card>
              ))}
            </Stack>
          </div>
        ))}
      </SimpleGrid>
    </div>
  );
}
