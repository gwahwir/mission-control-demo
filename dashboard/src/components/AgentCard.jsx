import { Card, Group, Text, Badge, Stack } from "@mantine/core";

export default function AgentCard({ agent, onSelect }) {
  const isOnline = agent.status === "online";

  return (
    <Card
      shadow="sm"
      padding="md"
      withBorder
      onClick={() => onSelect(agent)}
      style={{ cursor: "pointer" }}
    >
      <Group justify="space-between" mb="xs">
        <Text fw={600} size="lg">
          {agent.name}
        </Text>
        <Badge
          color={isOnline ? "green" : "red"}
          variant="light"
          size="sm"
          circle={false}
          leftSection={
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: "50%",
                backgroundColor: isOnline ? "var(--mantine-color-green-5)" : "var(--mantine-color-red-5)",
                display: "inline-block",
              }}
            />
          }
        >
          {agent.status}
        </Badge>
      </Group>

      <Text size="sm" c="dimmed" lineClamp={2} mb="sm">
        {agent.description}
      </Text>

      {agent.skills?.length > 0 && (
        <Group gap="xs">
          {agent.skills.map((skill) => (
            <Badge key={skill.id} variant="default" size="xs">
              {skill.name}
            </Badge>
          ))}
        </Group>
      )}
    </Card>
  );
}
