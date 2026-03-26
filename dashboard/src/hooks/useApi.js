const API_BASE = import.meta.env.VITE_API_BASE ?? "";

export async function fetchAgents() {
  const res = await fetch(`${API_BASE}/agents`);
  if (!res.ok) throw new Error("Failed to fetch agents");
  return res.json();
}

export async function dispatchTask(agentId, payload) {
  // payload is either a string (backward compat) or an object {text, baselines, key_questions}
  const body = typeof payload === 'string'
    ? JSON.stringify({ text: payload })
    : JSON.stringify(payload);

  const res = await fetch(`${API_BASE}/agents/${agentId}/tasks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
  });
  if (!res.ok) throw new Error("Failed to dispatch task");
  return res.json();
}

export async function cancelTask(agentId, taskId) {
  const res = await fetch(`${API_BASE}/agents/${agentId}/tasks/${taskId}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error("Failed to cancel task");
  return res.json();
}

export async function fetchGraph() {
  const res = await fetch(`${API_BASE}/graph`);
  if (!res.ok) throw new Error("Failed to fetch graph");
  return res.json();
}

export async function fetchTasks() {
  const res = await fetch(`${API_BASE}/tasks`);
  if (!res.ok) throw new Error("Failed to fetch tasks");
  return res.json();
}

export async function deleteTask(taskId) {
  const res = await fetch(`${API_BASE}/tasks/${taskId}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error("Failed to delete task");
  return res.json();
}

export async function deleteAllTasks() {
  const res = await fetch(`${API_BASE}/tasks`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error("Failed to clear tasks");
  return res.json();
}

export async function deregisterAgent(typeName, agentUrl) {
  const res = await fetch(`${API_BASE}/deregister`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ type_name: typeName, agent_url: agentUrl }),
  });
  if (!res.ok) throw new Error("Failed to deregister agent");
  return res.json();
}

export function subscribeToTask(taskId, onMessage) {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${protocol}//${window.location.host}/ws/tasks/${taskId}`);
  ws.onmessage = (event) => onMessage(JSON.parse(event.data));
  ws.onerror = () => ws.close();
  return () => ws.close();
}
