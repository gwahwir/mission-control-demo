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

export async function fetchTask(agentId, taskId) {
  const res = await fetch(`${API_BASE}/agents/${agentId}/tasks/${taskId}`);
  if (!res.ok) throw new Error("Failed to fetch task");
  return res.json();
}

export async function fetchBaseline(topicPath) {
  const res = await fetch(`${API_BASE}/baselines/${topicPath}/current`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error("Failed to fetch baseline");
  return res.json();
}

export async function ensureTopicRegistered(topicPath, displayName) {
  const res = await fetch(`${API_BASE}/topics`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ topic_path: topicPath, display_name: displayName || topicPath }),
  });
  if (!res.ok && res.status !== 409) throw new Error("Failed to register topic");
  return res.json().catch(() => null);
}

export async function writeBaselineVersion(topicPath, narrative) {
  const res = await fetch(`${API_BASE}/baselines/${topicPath}/versions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ narrative }),
  });
  if (!res.ok) throw new Error("Failed to write baseline version");
  return res.json();
}

export async function writeBaselineDelta(topicPath, delta) {
  const res = await fetch(`${API_BASE}/baselines/${topicPath}/deltas`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(delta),
  });
  if (!res.ok) throw new Error("Failed to write baseline delta");
  return res.json();
}
