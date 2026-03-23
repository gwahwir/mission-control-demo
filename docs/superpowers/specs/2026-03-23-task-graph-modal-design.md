# Task Graph Modal with Per-Node Outputs

**Date:** 2026-03-23
**Status:** Draft

## Overview

Replace the `TaskDetailDrawer` with a full-screen modal that shows the agent's LangGraph execution graph when a task is clicked. Nodes are color-coded by execution state and clickable — clicking a node shows its actual output in a panel. Works for both live (running) and completed tasks.

## Goals

- Show per-node execution output for any task (running or completed)
- Replace the existing flat `TaskDetailDrawer` everywhere — drawer is removed entirely
- Live updates: nodes light up as they complete during a running task
- Consistent with the existing HUD visual style

## Non-Goals

- No changes to the agent graph topology endpoint (`/graph`)
- No new authentication or authorization
- No changes to how tasks are dispatched or cancelled (cancel button stays in the modal)

---

## Backend Changes

### 1. `agents/base/executor.py`

After each LangGraph node completes, the executor already emits a status event with `"Running node: {node_name}"`. Extend this to also emit a second, additional status event in the same loop iteration.

**Insertion point in executor.py:** in the `async for event in self.graph.astream(...)` loop, the new emit goes after the existing `_emit_status("Running node: ...")` call AND after the `result.update(update)` accumulation — so it fires once the node output is available:

```python
node_name = next(iter(event))
await self._emit_status(..., f"Running node: {node_name}")  # existing
update = event[node_name]
if update:
    result.update(update)
# NEW: emit the node output
await self._emit_status(..., f"NODE_OUTPUT::{node_name}::{json.dumps(update or {})}")
```

The second emit also uses `state=working` (not terminal), consistent with the existing emit in the same iteration.

**Message format:**

```
NODE_OUTPUT::{node_name}::{json_string}
```

The `json_string` is `json.dumps(update or {})` where `update = event[node_name]`.

**Parsing rule:** use `split("::", 2)` — split on the first two `::` only, yielding exactly `["NODE_OUTPUT", node_name, json_string]`. This preserves any `::` inside the JSON payload.

This reuses the existing `_emit_status` / `TaskStatusUpdateEvent` mechanism. No new A2A protocol fields are added.

### 2. `control_plane/task_store.py`

Add `node_outputs: dict[str, str]` to `TaskRecord`. Each key is a bare node name (e.g., `"receive"`); each value is the raw JSON string of that node's state update.

- Default: empty dict `{}`
- `to_dict()` includes `"node_outputs": self.node_outputs` so it flows through the WebSocket
- `from_row()` deserializes it from a JSON string: `json.loads(row.get("node_outputs", "{}"))`
- **Postgres:** add `node_outputs TEXT NOT NULL DEFAULT '{}'` via `ALTER TABLE … ADD COLUMN IF NOT EXISTS`. Also update the `_UPSERT` SQL to include `node_outputs` in both the `INSERT` column list (positional param `$13`) and the `ON CONFLICT DO UPDATE SET` clause (`node_outputs = EXCLUDED.node_outputs`). Update `PostgresTaskStore.save()` to pass `json.dumps(record.node_outputs)` as the 13th parameter.
- In-memory backend: no schema change needed

### 3. `control_plane/routes.py`

Switch `_run_task` from `send_message` to `stream_message`. The `_run_task` signature is unchanged; `baselines` and `key_questions` are already fetched from `record` (as in the current code at routes.py lines 168–170) and passed to `stream_message`.

**SSE event structure:** each yielded dict from `stream_message` is a `TaskStatusUpdateEvent` payload. Extract fields as:
- State string: `event.get("result", {}).get("status", {}).get("state", "")`
- Message text: `(event.get("result", {}).get("status", {}).get("message", {}).get("parts") or [{}])[0].get("text", "")`

**`NODE_OUTPUT` event invariant:** the executor always emits `NODE_OUTPUT::` events with `state=working`. They are never terminal events. The terminal state check after the `NODE_OUTPUT` block is therefore safe to skip via `continue`.

**Stream processing loop:**

```python
gen = client.stream_message(text, baselines=record.baselines, key_questions=record.key_questions)
try:
    async for event in gen:
        state_str = event.get("result", {}).get("status", {}).get("state", "")
        msg = event.get("result", {}).get("status", {}).get("message", {})
        text_val = (msg.get("parts") or [{}])[0].get("text", "")

        if text_val.startswith("NODE_OUTPUT::"):
            parts = text_val.split("::", 2)
            if len(parts) == 3:
                _, node_name, json_payload = parts
                try:
                    json.loads(json_payload)  # validate before storing
                    record.node_outputs[node_name] = json_payload
                    await _task_store.save(record)
                    await _broker.publish(task_id, record.to_dict())
                except json.JSONDecodeError:
                    logger.warning("node_output_invalid_json", task_id=task_id, node=node_name)
            continue  # NODE_OUTPUT events are always working-state; skip terminal check

        if state_str in ("completed", "failed", "canceled"):
            record.state = TaskState(state_str)
            record.output_text = text_val
            if record.state == TaskState.FAILED:
                record.error = text_val or "Agent returned failed state with no details"
            break
    else:
        # Loop exhausted without a terminal event (stream closed unexpectedly)
        record.state = TaskState.FAILED
        record.error = "Stream ended without a terminal status event"
finally:
    await gen.aclose()  # close streaming HTTP connection on break or exception
```

**Note:** `stream_message` returns an `AsyncGenerator[dict[str, Any], None]` (not just `AsyncIterator`) to support `.aclose()`. Update the return type annotation accordingly in `a2a_client.py`. This is a prerequisite for `routes.py` — implement section 4 (`a2a_client.py`) before section 3.

**Exception handling:** the same five exception types (`A2AError`, `HTTPStatusError`, `ConnectError`, `TimeoutException`, `Exception`) are caught around the entire block above. The `finally` block (`client.close()`, `instance.active_tasks` decrement) is unchanged. `task_duration`, `tasks_completed`, `tasks_failed` metrics are recorded after the loop, same as before. `_task_store.save(record)` and `_broker.publish(task_id, record.to_dict())` are called once after the loop (or in exception handlers), same as the original.

### 4. `control_plane/a2a_client.py`

Add `baselines: str = ""` and `key_questions: str = ""` keyword arguments to `stream_message`, matching `send_message`. Pass them in `message["metadata"]` using the same keys (`"baselines"`, `"keyQuestions"`) as `send_message`, and only when non-empty — matching the `if baselines:` / `if key_questions:` guards in `send_message`.

---

## Frontend Changes

### 1. `App.jsx`

- Remove `TaskDetailDrawer` import and usage
- Replace with `<TaskGraphModal task={selectedTask} graphData={graphData} onClose={() => setSelectedTask(null)} onCancelled={handleTaskCancelled} />`
- `TaskBoard` and `TaskHistory` both call `onSelectTask` as before — no change to those components

### 2. `dashboard/src/components/TaskGraphModal.jsx` (new)

Full-screen Mantine `<Modal fullScreen opened={!!task} onClose={onClose}>` with no padding and a custom title showing `[ TASK GRAPH ]`.

**Layout (Layout C from design session):**
```
┌─────────────────────────────────────┬──────────────────────┐
│                                     │  Task metadata        │
│                                     │  (ID, agent, state,   │
│         TaskFlowGraph               │   created, cancel)    │
│         (65% width, full height)    ├──────────────────────┤
│                                     │  NodeOutputPanel      │
│                                     │  (fills remaining     │
│                                     │   height)             │
└─────────────────────────────────────┴──────────────────────┘
```

**Props:** `{ task, graphData, onClose, onCancelled }`

**State:**
- `taskState` — local copy of the task, initialized from `task` prop, updated by WS messages
- `selectedNodeId` — always a **bare** node name string (e.g., `"receive"`), never agent-prefixed. Set by `onNodeSelect` which receives a pre-stripped bare name from `TaskFlowGraph`. All comparisons against `selectedNodeId` elsewhere use bare names directly — no further stripping needed.
- `runningNode` — bare node name of the currently-executing node, or `null`. Updated from incoming WS messages: when the status message text matches `"Running node: {name}"` (and does NOT start with `NODE_OUTPUT::`), set `runningNode = name`. Cleared to `null` when `taskState.state` reaches a terminal value.

**WS subscription:** `subscribeToTask` is already exported from `dashboard/src/hooks/useApi.js` (line 70). It takes `(taskId, onMessage)` and returns a cleanup function. Call it in a `useEffect` on mount; call the returned cleanup function on unmount. On each message, replace `taskState` entirely with the incoming object (simple replacement — the backend accumulates `node_outputs` before publishing, so each message is a complete snapshot). Also extract the current status text from the message to update `runningNode`.

Only subscribe when the task is in a live state (`submitted` or `working`). For completed/failed/canceled tasks, skip the subscription — `node_outputs` is already fully populated in the `task` prop.

**Cancel:** calls `cancelTask(task.agent_id, task.task_id)` from `useApi.js` (already exported, used by the now-deleted `TaskDetailDrawer`), then calls `onCancelled(task.task_id)`.

### 3. `dashboard/src/components/TaskGraphModal/TaskFlowGraph.jsx` (new)

ReactFlow graph for the task's single agent.

**Graph data:** filter `graphData.agents` to the single entry where `agent.id === task.agent_id`, then call `computeLayout({ agents: [agentData], cross_agent_edges: [] })`. This renders only the task's agent graph. Cross-agent edges are omitted in this view.

**Node ID format:** `computeLayout()` creates ReactFlow node IDs as `{agent_id}:{node_id}` (e.g., `"echo-agent:process"`). When looking up a node's execution state in `task.node_outputs`, strip the agent prefix: use `nodeId.split(":").slice(1).join(":")` to get the bare node name key.

**Node state logic** (evaluated per node, in priority order):
1. **selected** — `nodeId === selectedNodeId` (bare name comparison after stripping prefix)
2. **running** — most recent non-`NODE_OUTPUT` status message parsed from WS is `"Running node: {bare_name}"`; track this as `runningNode` in `TaskGraphModal` state
3. **completed** — bare node name is a key in `taskState.node_outputs`
4. **failed** — `taskState.state === "failed"` and bare node name equals `runningNode` at time of failure
5. **pending** — none of the above

**Node visual style (Style B):**

| State | Background | Border | Dot | Text | Opacity |
|---|---|---|---|---|---|
| pending | `#0d1117` | `#374151` | none | `#6b7280` | 0.5 |
| running | `#1a1200` | `#f59e0b` | amber | `#fbbf24` | 1.0 + `box-shadow: 0 0 12px rgba(245,158,11,0.5)` |
| completed | `#0a1a0a` | `#22c55e` | green | `#4ade80` | 1.0 |
| failed | `#1a0505` | `#ef4444` | red | `#f87171` | 1.0 |
| selected | `#001a2a` | `#00d4ff` 2px | cyan | `#00d4ff` | 1.0 + `box-shadow: 0 0 14px rgba(0,212,255,0.3)` |

Dot is a 6×6px circle `display:inline-block` in the node label, colored per state (hidden for pending).

**Props:** `{ agentData, taskState, selectedNodeId, runningNode, onNodeSelect }`
- `agentData`: the single agent object from `graphData.agents` matching `task.agent_id`, or `null`
- `taskState`: full task dict (including `node_outputs`)
- `selectedNodeId`: bare node name string, or `null`
- `runningNode`: bare node name of the currently-running node (tracked in `TaskGraphModal` state, derived from non-`NODE_OUTPUT` status messages that match `"Running node: {name}"`), or `null`
- `onNodeSelect(bareNodeId)`: callback

**Missing agent fallback:** if `agentData` is `null` (agent was deregistered after task ran), render a centered message `"Graph topology unavailable — agent is no longer registered"` in dimmed text instead of the ReactFlow canvas.

ReactFlow config: `elementsSelectable={true}`, `nodesDraggable={false}`, `nodesConnectable={false}`, `onNodeClick={(_, node) => onNodeSelect(node.id.split(":").slice(1).join(":"))}`.

Use a custom `executionNode` node type (local to this file) that accepts `data.executionState` and renders the dot + colored styling. After `computeLayout` returns, patch each node's `data` field to add `executionState` based on the node state logic above.

### 4. `dashboard/src/components/TaskGraphModal/NodeOutputPanel.jsx` (new)

Displays the output of the selected node.

**Props:** `{ nodeId, nodeOutputJson, nodeState, onClose }`
- `nodeId`: bare node name string (e.g., `"receive"`)
- `nodeOutputJson`: computed in `TaskGraphModal` as:
  - `undefined` if `taskState.node_outputs` is absent (old pre-migration record — `task.node_outputs` key not present at all)
  - `undefined` if the node name is not a key in `node_outputs` (node hasn't run yet — JS object property access returns `undefined` for missing keys, not `null`)
  - a JSON string (e.g., `"{}"` or `'{"output":"..."}'`) if the node has completed
  - The backend never writes `null` into `node_outputs` values
- `nodeState`: one of `"pending"`, `"running"`, `"completed"`, `"failed"` — used to select the appropriate empty-state message
- `onClose`: deselects the node

**Tabs:** Mantine `<Tabs>` with two values: `"formatted"` (default) and `"raw"`.

**FORMATTED tab:** parse `nodeOutputJson` with `JSON.parse`. For each top-level key-value pair:
- `string` → `<Text size="sm">` plain text
- `number` / `boolean` → `<Text size="sm" style={{ color: "var(--hud-green)", fontFamily: "monospace" }}>`
- `array` of short strings (each ≤ 40 chars) → row of cyan `<Badge variant="outline">` chips
- `array` of long strings → `<List>` bullet list
- `object` → `<Code block>` with `JSON.stringify(value, null, 2)`

Each key rendered as an uppercase dimmed label (`var(--hud-text-dimmed)`, `letterSpacing: "1px"`, `fontSize: 11`) above the value.

**RAW tab:** `<Code block style={{ color: "var(--hud-cyan)", backgroundColor: "var(--hud-bg-surface)" }}>` with `JSON.stringify(JSON.parse(nodeOutputJson), null, 2)`.

**Empty/loading states (checked in order, before rendering tabs):**
1. `taskState.node_outputs === undefined` (key absent) → `"Output not available for this task"` (pre-migration record); `nodeOutputJson` is not needed for this check — do it in `TaskGraphModal` before passing the prop
2. `nodeOutputJson === undefined` and `nodeState === "running"` → `"Node is running…"` with blinking cursor animation
3. `nodeOutputJson === undefined` and `nodeState === "pending"` → `"Node has not run yet"`
4. `nodeOutputJson === "{}"` → `"Node produced no output"`
5. JSON parse error → display the raw string in a `<Code block>` with an amber `"Parse error"` badge; do not throw

**Header:** `[ {NODE_NAME} ] OUTPUT` in cyan uppercase, close button (×) on the right calls `onClose`.

---

## File Summary

| File | Change |
|---|---|
| `agents/base/executor.py` | Emit `NODE_OUTPUT::` event after each node |
| `control_plane/task_store.py` | Add `node_outputs` field; update `_UPSERT` SQL |
| `control_plane/routes.py` | Switch to `stream_message`, parse node output events per spec |
| `control_plane/a2a_client.py` | Add `baselines`/`key_questions` to `stream_message` |
| `dashboard/src/App.jsx` | Replace `TaskDetailDrawer` with `TaskGraphModal` |
| `dashboard/src/components/TaskDetailDrawer.jsx` | Delete |
| `dashboard/src/components/TaskGraphModal.jsx` | New — modal shell, WS subscription, layout |
| `dashboard/src/components/TaskGraphModal/TaskFlowGraph.jsx` | New — ReactFlow with execution state overlay |
| `dashboard/src/components/TaskGraphModal/NodeOutputPanel.jsx` | New — formatted/raw output panel |

---

## Data Flow

### Live task

```
LangGraph node completes in agent process
  → executor emits "NODE_OUTPUT::receive::{...json...}" via SSE event
  → _run_task stream loop detects NODE_OUTPUT:: prefix
  → splits with split("::", 2) → node_name="receive", json_payload="{...}"
  → record.node_outputs["receive"] = json_payload
  → task_store.save(record) + broker.publish(task_id, record.to_dict())
  → WS pushes full updated task dict to dashboard
  → TaskGraphModal replaces taskState with incoming dict
  → TaskFlowGraph: strips agent prefix, looks up "receive" in node_outputs → green
  → user clicks "receive" node → NodeOutputPanel renders its output
```

### Completed task

```
User clicks task in TaskBoard or TaskHistory
  → App.jsx sets selectedTask (task already has node_outputs populated)
  → TaskGraphModal opens, skips WS subscription
  → all completed nodes shown green immediately
  → user clicks any node → NodeOutputPanel renders its stored output
```

---

## Error Handling

- **Malformed NODE_OUTPUT JSON:** log warning, skip node update, continue stream — do not crash
- **split("::", 2) produces fewer than 3 parts:** log warning, skip — indicates malformed emit
- **Empty node state update `{}`:** store `"{}"` — output panel shows "Node produced no output"
- **Agent doesn't support streaming:** `stream_message` raises an HTTP error → caught by existing exception handlers → task fails with an error message; `node_outputs` stays `{}`, graph renders all nodes as pending, output panel shows "Output not available"

---

## Testing

- Existing `pytest` control plane tests remain valid (they mock HTTP, not SSE)
- Add unit test: `NODE_OUTPUT::` parsing in `_run_task` — valid JSON, `::` in JSON payload, missing parts
- Add unit test: `TaskRecord.node_outputs` serialization round-trip (in-memory and Postgres path)
- Add unit test: `stream_message` with `baselines`/`key_questions` params passes them in metadata
- Frontend: manual test with echo agent (simple graph) and lead analyst (fan-out with parallel nodes)
