// dashboard/src/components/TaskGraphModal.jsx
import { useState, useEffect } from "react";
import { Modal, Stack, Text, Badge, Code, Button, Group } from "@mantine/core";
import { cancelTask, subscribeToTask } from "../hooks/useApi";
import TaskFlowGraph from "./TaskGraphModal/TaskFlowGraph";
import NodeOutputPanel from "./TaskGraphModal/NodeOutputPanel";

const STATE_COLORS = {
  completed: "hud-green", working: "hud-amber", submitted: "gray",
  canceled: "hud-red", failed: "hud-red", "input-required": "hud-violet",
};

const LIVE_STATES = new Set(["submitted", "working"]);

export default function TaskGraphModal({ task, graphData, onClose, onCancelled }) {
  const [taskState, setTaskState] = useState(task);
  const [selectedNodeId, setSelectedNodeId] = useState(null);
  const [cancelling, setCancelling] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);

  useEffect(() => {
    setTaskState(task);
    setSelectedNodeId(null);
    setCancelling(false);
    setConfirmCancel(false);
  }, [task?.task_id]);

  useEffect(() => {
    if (!task || !LIVE_STATES.has(task.state)) return;
    const unsub = subscribeToTask(task.task_id, (msg) => setTaskState(msg));
    return unsub;
  }, [task?.task_id, task?.state]);

  if (!task) return null;

  const agentData = graphData?.agents?.find((a) => a.id === taskState?.agent_id) ?? null;
  const canCancel = LIVE_STATES.has(taskState?.state);

  const handleCancel = async () => {
    if (!confirmCancel) { setConfirmCancel(true); return; }
    setCancelling(true);
    try {
      await cancelTask(taskState.agent_id, taskState.task_id);
      onCancelled(taskState.task_id);
    } catch (err) {
      alert("Cancel failed: " + err.message);
    } finally {
      setCancelling(false);
      setConfirmCancel(false);
    }
  };

  // nodeOutputJson: undefined if node_outputs absent (old record), undefined if key missing, else string
  const nodeOutputs = taskState?.node_outputs;
  const nodeOutputJson = selectedNodeId
    ? (nodeOutputs === undefined ? undefined : nodeOutputs?.[selectedNodeId])
    : undefined;

  const nodeState = (() => {
    if (!selectedNodeId) return "pending";
    const runningNode = taskState?.running_node;
    if (taskState?.state === "failed" && nodeOutputs && !(selectedNodeId in nodeOutputs)) return "failed";
    if (nodeOutputs?.[selectedNodeId] !== undefined) return "completed";
    if (runningNode && selectedNodeId === runningNode) return "running";
    return "pending";
  })();

  return (
    <Modal
      opened={!!task}
      onClose={onClose}
      fullScreen
      title={<Text fw={600} style={{ textTransform: "uppercase", letterSpacing: "2px", fontSize: 14 }}>[ TASK GRAPH ]</Text>}
      styles={{ body: { padding: 0, height: "calc(100vh - 60px)", display: "flex" }, content: { display: "flex", flexDirection: "column" } }}
    >
      <div style={{ display: "flex", flex: 1, overflow: "hidden" }}>
        {/* Graph — 65% */}
        <div style={{ flex: "0 0 65%", borderRight: "1px solid var(--hud-border)" }}>
          <TaskFlowGraph
            agentData={agentData}
            taskState={taskState}
            selectedNodeId={selectedNodeId}
            onNodeSelect={setSelectedNodeId}
          />
        </div>

        {/* Right panel — 35% */}
        <div style={{ flex: "0 0 35%", display: "flex", flexDirection: "column", overflow: "hidden" }}>
          {/* Metadata */}
          <Stack gap="xs" p="md" style={{ borderBottom: "1px solid var(--hud-border)", flexShrink: 0 }}>
            <div>
              <Text size="xs" style={{ color: "var(--hud-text-dimmed)", letterSpacing: "1px" }} tt="uppercase">Task ID</Text>
              <Code style={{ fontSize: 11 }}>{taskState?.task_id}</Code>
            </div>
            <div>
              <Text size="xs" style={{ color: "var(--hud-text-dimmed)", letterSpacing: "1px" }} tt="uppercase">Agent</Text>
              <Text size="sm">{taskState?.agent_id}</Text>
            </div>
            <div>
              <Text size="xs" style={{ color: "var(--hud-text-dimmed)", letterSpacing: "1px" }} tt="uppercase">State</Text>
              <Badge color={STATE_COLORS[taskState?.state] || "gray"} variant="light">{taskState?.state}</Badge>
            </div>
            <div>
              <Text size="xs" style={{ color: "var(--hud-text-dimmed)", letterSpacing: "1px" }} tt="uppercase">Created</Text>
              <Text size="sm">{taskState?.created_at ? new Date(taskState.created_at * 1000).toLocaleString() : "—"}</Text>
            </div>
            {canCancel && (
              <Button
                color="hud-red"
                variant={confirmCancel ? "filled" : "outline"}
                size="xs"
                onClick={handleCancel}
                loading={cancelling}
                style={confirmCancel ? { boxShadow: "0 0 12px rgba(255,61,61,0.3)" } : { borderColor: "var(--hud-red)", color: "var(--hud-red)" }}
              >
                {confirmCancel ? "CLICK AGAIN TO CONFIRM" : "CANCEL TASK"}
              </Button>
            )}
          </Stack>

          {/* Node output */}
          <div style={{ flex: 1, overflow: "auto", backgroundColor: "var(--hud-bg-surface)" }}>
            {selectedNodeId ? (
              <NodeOutputPanel
                nodeId={selectedNodeId}
                nodeOutputJson={nodeOutputJson}
                nodeState={nodeState}
                onClose={() => setSelectedNodeId(null)}
              />
            ) : (
              <div style={{ padding: 12 }}>
                <Text size="xs" style={{ color: "var(--hud-text-dimmed)" }}>
                  Click a node to view its output
                  <span style={{ animation: "blink-cursor 1s step-end infinite" }}>_</span>
                </Text>
              </div>
            )}
          </div>
        </div>
      </div>
    </Modal>
  );
}
