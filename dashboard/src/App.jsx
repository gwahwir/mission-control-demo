import { useState, useEffect, useCallback } from "react";
import { useDisclosure } from "@mantine/hooks";
import {
  AppShell,
  Group,
  Title,
  Text,
  Tabs,
  Badge,
  Box,
  Tooltip,
} from "@mantine/core";
import AgentPanel from "./components/AgentPanel";
import TaskLauncher from "./components/TaskLauncher";
import TaskBoard from "./components/TaskBoard";
import TaskHistory from "./components/TaskHistory";
import TaskGraphModal from "./components/TaskGraphModal";
import AgentDetailDrawer from "./components/AgentDetailDrawer";
import AgentFlowDiagram from "./components/AgentFlowDiagram";
import AnalystFlowPage from "./components/AnalystFlowPage/AnalystFlowPage";
import BaselinesPage from "./components/BaselinesPage/BaselinesPage";
import CommsPage from "./components/CommsPage/CommsPage";
import PipelinePage from "./components/PipelinePage/PipelinePage";
import { fetchAgents, fetchGraph, fetchTasks, deleteTask, deleteAllTasks } from "./hooks/useApi";

function UtcClock() {
  const [time, setTime] = useState(() => new Date().toISOString().slice(11, 19));
  useEffect(() => {
    const id = setInterval(() => {
      setTime(new Date().toISOString().slice(11, 19));
    }, 1000);
    return () => clearInterval(id);
  }, []);
  return (
    <Text size="xs" style={{ color: "var(--hud-text-dimmed)", letterSpacing: "1px" }}>
      {time} UTC
    </Text>
  );
}

function App() {
  const [sidebarOpen, { toggle: toggleSidebar }] = useDisclosure(true);
  const [agents, setAgents] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [graphData, setGraphData] = useState(null);
  const [selectedTask, setSelectedTask] = useState(null);
  const [selectedAgent, setSelectedAgent] = useState(null);
  const [tab, setTab] = useState("flow");
  const [analystTask, setAnalystTask] = useState(null);   // {taskId, agentId}
  const [analystTaskState, setAnalystTaskState] = useState(null);
  const [baselinesRefreshKey, setBaselinesRefreshKey] = useState(0);

  const loadAgents = useCallback(() => {
    fetchAgents().then(setAgents).catch(() => {});
  }, []);

  const loadTasks = useCallback(() => {
    fetchTasks().then(setTasks).catch(() => {});
  }, []);

  const loadGraph = useCallback(() => {
    fetchGraph().then(setGraphData).catch(() => {});
  }, []);

  useEffect(() => {
    loadAgents();
    loadTasks();
    loadGraph();
    const i1 = setInterval(loadAgents, 10000);
    const i2 = setInterval(loadTasks, 3000);
    const i3 = setInterval(loadGraph, 30000);
    return () => {
      clearInterval(i1);
      clearInterval(i2);
      clearInterval(i3);
    };
  }, [loadAgents, loadTasks, loadGraph]);

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

  const handleDeleteTask = (taskId) => {
    deleteTask(taskId)
      .then(() => {
        setTasks((prev) => prev.filter((t) => t.task_id !== taskId));
        if (selectedTask?.task_id === taskId) setSelectedTask(null);
      })
      .catch(() => {});
  };

  const handleClearAll = () => {
    deleteAllTasks()
      .then(() => {
        setTasks([]);
        setSelectedTask(null);
      })
      .catch(() => {});
  };

  const onlineCount = agents.filter((a) => a.status === "online").length;

  return (
    <AppShell
      header={{ height: 60 }}
      navbar={{ width: 600, breakpoint: "sm", collapsed: { desktop: !sidebarOpen } }}
      padding="md"
    >
      <AppShell.Header
        style={{
          boxShadow: "0 1px 12px rgba(0, 212, 255, 0.08)",
        }}
      >
        <Group h="100%" px="md" justify="space-between">
          <Group>
            <Tooltip label={sidebarOpen ? "Collapse sidebar" : "Expand sidebar"} position="right">
              <Box
                w={36}
                h={36}
                onClick={toggleSidebar}
                style={{
                  borderRadius: 0,
                  border: "1px solid var(--hud-cyan)",
                  background: "transparent",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  color: "var(--hud-cyan)",
                  fontWeight: 700,
                  fontSize: 13,
                  letterSpacing: "1px",
                  cursor: "pointer",
                  transition: "background 0.15s, box-shadow 0.15s",
                  userSelect: "none",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = "rgba(0,212,255,0.08)";
                  e.currentTarget.style.boxShadow = "0 0 10px rgba(0,212,255,0.2)";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = "transparent";
                  e.currentTarget.style.boxShadow = "none";
                }}
              >
                {sidebarOpen ? "◀" : "▶"}
              </Box>
            </Tooltip>
            <Title
              order={4}
              style={{
                textTransform: "uppercase",
                letterSpacing: "2px",
                color: "var(--hud-text-primary)",
                fontSize: 14,
              }}
            >
              [ BEESTORM ]
            </Title>
          </Group>
          <Group gap="md">
            <UtcClock />
            <Badge
              variant="dot"
              color={onlineCount > 0 ? "hud-green" : "hud-red"}
              size="lg"
              styles={{
                root: {
                  "--badge-dot-size": "8px",
                },
              }}
              leftSection={
                <span
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    backgroundColor: onlineCount > 0 ? "var(--hud-green)" : "var(--hud-red)",
                    display: "inline-block",
                    animation: "pulse-glow 2s ease-in-out infinite",
                    color: onlineCount > 0 ? "var(--hud-green)" : "var(--hud-red)",
                  }}
                />
              }
            >
              {onlineCount}/{agents.length} AGENTS ONLINE
            </Badge>
          </Group>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar>
        <AgentPanel onSelectAgent={setSelectedAgent} />
      </AppShell.Navbar>

      <AppShell.Main>
        <Box maw={2000} mx="auto">
          <Tabs value={tab} onChange={setTab}>
            <Tabs.List mb="md">
              <Tabs.Tab value="dispatch">[00] DISPATCH TASK</Tabs.Tab>
              <Tabs.Tab value="flow">[01] TOPOLOGY</Tabs.Tab>
              <Tabs.Tab value="board">[02] TASK BOARD</Tabs.Tab>
              <Tabs.Tab value="history">[03] HISTORY</Tabs.Tab>
              <Tabs.Tab value="pipeline">[04] PIPELINE</Tabs.Tab>
              <Tabs.Tab value="comms">[05] COMMS</Tabs.Tab>
              <Tabs.Tab value="analyst">[06] ANALYST FLOW</Tabs.Tab>
              <Tabs.Tab value="baselines">[07] BASELINES</Tabs.Tab>
            </Tabs.List>

            <Tabs.Panel value="dispatch">
              <TaskLauncher agents={agents} graphData={graphData} onTaskCreated={handleTaskCreated} />
            </Tabs.Panel>

            <Tabs.Panel value="flow">
              <AgentFlowDiagram graphData={graphData} />
            </Tabs.Panel>

            <Tabs.Panel value="board">
              <TaskBoard tasks={tasks} onSelectTask={setSelectedTask} />
            </Tabs.Panel>

            <Tabs.Panel value="history">
              <TaskHistory
                tasks={tasks}
                onSelectTask={setSelectedTask}
                onDeleteTask={handleDeleteTask}
                onClearAll={handleClearAll}
              />
            </Tabs.Panel>

            <Tabs.Panel value="pipeline">
              <PipelinePage
                agents={agents}
                onAnalystTaskStarted={(taskId, agentId) => {
                  setAnalystTask({ taskId, agentId });
                  setAnalystTaskState(null);
                }}
                onAnalystTaskState={setAnalystTaskState}
                onBaselineWritten={() => setBaselinesRefreshKey((k) => k + 1)}
              />
            </Tabs.Panel>

            <Tabs.Panel value="comms">
              <CommsPage analystTask={analystTask} taskState={analystTaskState} />
            </Tabs.Panel>

            <Tabs.Panel value="analyst">
              <AnalystFlowPage
                agents={agents}
                onBaselineWritten={() => setBaselinesRefreshKey((k) => k + 1)}
              />
            </Tabs.Panel>

            <Tabs.Panel value="baselines">
              <BaselinesPage refreshKey={baselinesRefreshKey} />
            </Tabs.Panel>
          </Tabs>
        </Box>
      </AppShell.Main>

      <TaskGraphModal
        task={selectedTask}
        graphData={graphData}
        onClose={() => setSelectedTask(null)}
        onCancelled={handleTaskCancelled}
      />

      <AgentDetailDrawer
        agent={selectedAgent}
        onClose={() => setSelectedAgent(null)}
      />
    </AppShell>
  );
}

export default App;
