import { useEffect, useState, useMemo } from "react";
import { Stack, Title, Text, Alert, TextInput, Select, Group, Badge, Box, ScrollArea } from "@mantine/core";
import { fetchAgents } from "../hooks/useApi";
import AgentCard from "./AgentCard";

export default function AgentPanel({ onSelectAgent }) {
  const [agents, setAgents] = useState([]);
  const [error, setError] = useState(null);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState(null);

  useEffect(() => {
    const load = () =>
      fetchAgents()
        .then(setAgents)
        .catch((e) => setError(e.message));

    load();
    const interval = setInterval(load, 10000);
    return () => clearInterval(interval);
  }, []);

  const onlineCount = agents.filter((a) => a.status === "online").length;

  const filtered = useMemo(() => {
    return agents.filter((agent) => {
      const matchesSearch =
        !search ||
        agent.name.toLowerCase().includes(search.toLowerCase()) ||
        agent.id.toLowerCase().includes(search.toLowerCase()) ||
        (agent.description || "").toLowerCase().includes(search.toLowerCase()) ||
        (agent.skills || []).some((s) =>
          s.name.toLowerCase().includes(search.toLowerCase())
        );
      const matchesStatus =
        !statusFilter || agent.status === statusFilter;
      return matchesSearch && matchesStatus;
    });
  }, [agents, search, statusFilter]);

  return (
    <Box style={{ display: "flex", flexDirection: "column", height: "100%"}}>
      {/* Header */}
      <Box px="sm" pt="sm" pb={8}>
        <Group justify="space-between" mb={8}>
          <Title
            order={5}
            style={{ textTransform: "uppercase", letterSpacing: "2px", fontSize: 14 }}
          >
            [ AGENTS ]
          </Title>
          <Badge
            variant="light"
            color={onlineCount > 0 ? "hud-green" : "hud-red"}
            size="xs"
          >
            {onlineCount}/{agents.length}
          </Badge>
        </Group>

        <Stack gap={6}>
          <TextInput
            placeholder="Search agents..."
            size="xs"
            value={search}
            onChange={(e) => setSearch(e.currentTarget.value)}
          />
          <Select
            placeholder="All statuses"
            size="xs"
            clearable
            data={[
              { value: "online", label: "Online" },
              { value: "offline", label: "Offline" },
            ]}
            value={statusFilter}
            onChange={setStatusFilter}
          />
        </Stack>
      </Box>

      {/* Divider */}
      <Box
        mx="sm"
        mb={8}
        style={{ borderBottom: "1px solid var(--hud-border)" }}
      />

      {/* Scrollable card list */}
      <ScrollArea
        style={{ flex: 1 }}
        scrollbarSize={4}
        type="hover"
      >
        <Stack gap="xs" px="sm" pb="sm">
          {error && (
            <Alert color="red" py={6} px="xs" style={{ borderLeftColor: "var(--hud-red)", fontSize: 15 }}>
              Failed to load agents: {error}
            </Alert>
          )}
          {filtered.map((agent) => (
            <AgentCard key={agent.id} agent={agent} onSelect={onSelectAgent} />
          ))}
          {filtered.length === 0 && !error && (
            <Text size="s" style={{ color: "var(--hud-text-dimmed)" }} ta="center" py="md">
              {agents.length === 0
                ? <>No agents registered<span style={{ animation: "blink-cursor 1s step-end infinite" }}>_</span></>
                : "No agents match filters"
              }
            </Text>
          )}
        </Stack>
      </ScrollArea>
    </Box>
  );
}
