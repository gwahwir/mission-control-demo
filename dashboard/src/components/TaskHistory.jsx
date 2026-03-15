import { useState } from "react";
import { TextInput, Select, Table, Title, Group, Badge, Text } from "@mantine/core";

const stateColors = {
  completed: "green",
  working: "yellow",
  submitted: "gray",
  canceled: "red",
  failed: "red",
  "input-required": "violet",
};

export default function TaskHistory({ tasks, onSelectTask }) {
  const [search, setSearch] = useState("");
  const [filterState, setFilterState] = useState(null);

  const filtered = tasks.filter((t) => {
    const matchesSearch =
      !search ||
      t.input_text.toLowerCase().includes(search.toLowerCase()) ||
      t.agent_id.toLowerCase().includes(search.toLowerCase()) ||
      t.task_id.toLowerCase().includes(search.toLowerCase());
    const matchesState = !filterState || t.state === filterState;
    return matchesSearch && matchesState;
  });

  return (
    <div>
      <Title order={3} mb="md">
        Task History
      </Title>

      <Group mb="md">
        <TextInput
          placeholder="Search tasks..."
          value={search}
          onChange={(e) => setSearch(e.currentTarget.value)}
          style={{ flex: 1, minWidth: 200 }}
        />
        <Select
          placeholder="All states"
          clearable
          data={[
            { value: "completed", label: "Completed" },
            { value: "working", label: "Working" },
            { value: "submitted", label: "Submitted" },
            { value: "canceled", label: "Cancelled" },
            { value: "failed", label: "Failed" },
          ]}
          value={filterState}
          onChange={setFilterState}
        />
      </Group>

      <Table striped highlightOnHover>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Task ID</Table.Th>
            <Table.Th>Agent</Table.Th>
            <Table.Th>Input</Table.Th>
            <Table.Th>State</Table.Th>
            <Table.Th>Time</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {filtered.map((task) => (
            <Table.Tr
              key={task.task_id}
              onClick={() => onSelectTask(task)}
              style={{ cursor: "pointer" }}
            >
              <Table.Td>
                <Text size="xs" ff="monospace">
                  {task.task_id.slice(0, 8)}...
                </Text>
              </Table.Td>
              <Table.Td>{task.agent_id}</Table.Td>
              <Table.Td maw={200} style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {task.input_text}
              </Table.Td>
              <Table.Td>
                <Badge color={stateColors[task.state] || "gray"} variant="light" size="sm">
                  {task.state}
                </Badge>
              </Table.Td>
              <Table.Td>
                <Text size="xs" c="dimmed">
                  {new Date(task.created_at * 1000).toLocaleString()}
                </Text>
              </Table.Td>
            </Table.Tr>
          ))}
          {filtered.length === 0 && (
            <Table.Tr>
              <Table.Td colSpan={5}>
                <Text ta="center" c="dimmed" py="md">
                  No tasks found.
                </Text>
              </Table.Td>
            </Table.Tr>
          )}
        </Table.Tbody>
      </Table>
    </div>
  );
}
