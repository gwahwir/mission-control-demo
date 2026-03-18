import { Card, Group, Text, Badge } from "@mantine/core";

export default function AgentCard({ agent, onSelect }) {
  const isOnline = agent.status === "online";
  const instances = agent.instances || [];
  const onlineInstances = instances.filter((i) => i.status === "online");
  const totalActive = instances.reduce((sum, i) => sum + (i.active_tasks || 0), 0);

  const statusColor = isOnline ? "var(--hud-green)" : "var(--hud-red)";

  return (
    <Card
      padding="xs"
      onClick={() => onSelect(agent)}
      style={{
        cursor: "pointer",
        position: "relative",
        transition: "border-color 0.2s, box-shadow 0.2s",
        animation: "fade-in-up 0.3s ease-out",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.borderColor = "var(--hud-cyan)";
        e.currentTarget.style.boxShadow = "0 0 12px rgba(0, 212, 255, 0.15)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.borderColor = "var(--hud-border)";
        e.currentTarget.style.boxShadow = "none";
      }}
    >
      {/* Corner bracket decoration */}
      <div
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          width: 10,
          height: 10,
          borderTop: "2px solid var(--hud-cyan)",
          borderLeft: "2px solid var(--hud-cyan)",
        }}
      />

      <Group justify="space-between" mb={4} wrap="nowrap">
        <Text
          fw={900}
          size="m"
          style={{ flex: 1, minWidth: 0, color: "var(--hud-text-primary)" }}
          lineClamp={1}
        >
          {agent.name}
        </Text>
        <span
          style={{
            width: 7,
            height: 7,
            borderRadius: "50%",
            backgroundColor: statusColor,
            display: "inline-block",
            animation: "pulse-glow 2s ease-in-out infinite",
            color: statusColor,
            flexShrink: 0,
          }}
        />
      </Group>

      <Text size="s" style={{ color: "var(--hud-text-dimmed)" }} lineClamp={1} mb={4}>
        {agent.description}
      </Text>

      <Group gap={4} wrap="wrap">
        {agent.skills?.length > 0 &&
          agent.skills.slice(0, 2).map((skill) => (
            <Badge
              key={skill.id}
              variant="outline"
              size="xs"
              style={{
                borderColor: "var(--hud-border)",
                color: "var(--hud-text-dimmed)",
                textTransform: "uppercase",
                fontSize: 9,
              }}
            >
              {skill.name}
            </Badge>
          ))}
        {agent.skills?.length > 2 && (
          <Badge
            variant="outline"
            size="xs"
            style={{
              borderColor: "var(--hud-border)",
              color: "var(--hud-text-dimmed)",
              fontSize: 9,
            }}
          >
            +{agent.skills.length - 2}
          </Badge>
        )}
        {instances.length > 1 && (
          <Badge variant="light" color="hud-cyan" size="xs" style={{ fontSize: 9 }}>
            {onlineInstances.length}/{instances.length}
          </Badge>
        )}
        {totalActive > 0 && (
          <Badge variant="light" color="hud-amber" size="xs" style={{ fontSize: 9 }}>
            {totalActive} active
          </Badge>
        )}
      </Group>
    </Card>
  );
}
