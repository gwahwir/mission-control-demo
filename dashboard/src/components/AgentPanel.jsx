import { useEffect, useState } from "react";
import { SimpleGrid, Title, Text, Alert } from "@mantine/core";
import { fetchAgents } from "../hooks/useApi";
import AgentCard from "./AgentCard";

export default function AgentPanel({ onSelectAgent }) {
  const [agents, setAgents] = useState([]);
  const [error, setError] = useState(null);

  useEffect(() => {
    const load = () =>
      fetchAgents()
        .then(setAgents)
        .catch((e) => setError(e.message));

    load();
    const interval = setInterval(load, 10000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div>
      <Title order={3} mb="md">
        Agents
      </Title>
      {error && (
        <Alert color="red" mb="sm">
          Failed to load agents: {error}
        </Alert>
      )}
      <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }}>
        {agents.map((agent) => (
          <AgentCard key={agent.id} agent={agent} onSelect={onSelectAgent} />
        ))}
      </SimpleGrid>
      {agents.length === 0 && !error && (
        <Text size="sm" c="dimmed">
          No agents registered.
        </Text>
      )}
    </div>
  );
}
