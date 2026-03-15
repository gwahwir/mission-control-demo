import { useState, useEffect, useCallback } from "react";
import {
  AppShell,
  Group,
  Title,
  Text,
  Tabs,
  Badge,
  ActionIcon,
  Box,
} from "@mantine/core";
import AgentPanel from "./components/AgentPanel";
import TaskLauncher from "./components/TaskLauncher";
import TaskBoard from "./components/TaskBoard";
import TaskHistory from "./components/TaskHistory";
import TaskDetailDrawer from "./components/TaskDetailDrawer";
import { fetchAgents, fetchTasks } from "./hooks/useApi";

function App() {
  const [agents, setAgents] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [selectedTask, setSelectedTask] = useState(null);
  const [tab, setTab] = useState("board");

  const loadAgents = useCallback(() => {
    fetchAgents().then(setAgents).catch(() => {});
  }, []);

  const loadTasks = useCallback(() => {
    fetchTasks().then(setTasks).catch(() => {});
  }, []);

  useEffect(() => {
    loadAgents();
    loadTasks();
    const i1 = setInterval(loadAgents, 10000);
    const i2 = setInterval(loadTasks, 3000);
    return () => {
      clearInterval(i1);
      clearInterval(i2);
    };
  }, [loadAgents, loadTasks]);

  const handleTaskCreated = (task) => {
    setTasks((prev) => [task, ...prev]);
  };

  const handleTaskCancelled = (taskId) => {
    setTasks((prev) =>
      prev.map((t) =>
        t.task_id === taskId ? { ...t, state: "canceled" } : t
      )
    );
    setSelectedTask(null);
  };

  const onlineCount = agents.filter((a) => a.status === "online").length;

  return (
    <AppShell header={{ height: 60 }} padding="md">
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Group>
            <Box
              w={32}
              h={32}
              style={{
                borderRadius: "var(--mantine-radius-md)",
                background: "var(--mantine-color-indigo-6)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                color: "white",
                fontWeight: 700,
                fontSize: 14,
              }}
            >
              MC
            </Box>
            <Title order={4}>Mission Control</Title>
          </Group>
          <Group gap="xs">
            <Badge variant="dot" color={onlineCount > 0 ? "green" : "red"} size="lg">
              {onlineCount}/{agents.length} agents online
            </Badge>
          </Group>
        </Group>
      </AppShell.Header>

      <AppShell.Main>
        <Box maw={1200} mx="auto">
          <Box mb="xl">
            <AgentPanel onSelectAgent={() => {}} />
          </Box>

          <Box mb="xl">
            <TaskLauncher agents={agents} onTaskCreated={handleTaskCreated} />
          </Box>

          <Tabs value={tab} onChange={setTab}>
            <Tabs.List mb="md">
              <Tabs.Tab value="board">Task Board</Tabs.Tab>
              <Tabs.Tab value="history">History</Tabs.Tab>
            </Tabs.List>

            <Tabs.Panel value="board">
              <TaskBoard tasks={tasks} onSelectTask={setSelectedTask} />
            </Tabs.Panel>

            <Tabs.Panel value="history">
              <TaskHistory tasks={tasks} onSelectTask={setSelectedTask} />
            </Tabs.Panel>
          </Tabs>
        </Box>
      </AppShell.Main>

      <TaskDetailDrawer
        task={selectedTask}
        onClose={() => setSelectedTask(null)}
        onCancelled={handleTaskCancelled}
      />
    </AppShell>
  );
}

export default App;
