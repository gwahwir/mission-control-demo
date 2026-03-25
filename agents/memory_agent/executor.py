"""Executor for the memory agent, with multi-skill dispatch."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from a2a.server.agent_execution import RequestContext
from a2a.server.events import EventQueue
from a2a.types import (
    Message,
    Part,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)
from langgraph.graph.state import CompiledStateGraph

from agents.base.executor import LangGraphA2AExecutor
from agents.memory_agent.graph import build_memory_write_graph
from agents.memory_agent.stores import embed_text, get_neo4j_graph, get_pgvector_pool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Direct async helpers (no LangGraph)
# ---------------------------------------------------------------------------

async def _search_memories(
    executor: LangGraphA2AExecutor,
    task_id: str,
    input_json: dict[str, Any],
) -> dict[str, Any]:
    """Semantic search over the memories table."""
    query = input_json.get("query", "")
    namespace = input_json.get("namespace", "")
    limit = int(input_json.get("limit", 5))

    executor.check_cancelled(task_id)
    query_vec = await embed_text(query)
    executor.check_cancelled(task_id)

    vec_str = "[" + ",".join(str(x) for x in query_vec) + "]"
    pool = await get_pgvector_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT content, metadata, "
            "1 - (embedding <=> $1::vector) AS score "
            "FROM memories "
            "WHERE namespace = $2 "
            "ORDER BY embedding <=> $1::vector "
            "LIMIT $3",
            vec_str, namespace, limit,
        )

    results = []
    for row in rows:
        raw_meta = row["metadata"]
        metadata = json.loads(raw_meta) if isinstance(raw_meta, str) else raw_meta
        results.append({
            "content": row["content"],
            "score": float(row["score"]),
            "metadata": metadata,
        })
    return {"results": results}


async def _traverse_graph(
    executor: LangGraphA2AExecutor,
    task_id: str,
    input_json: dict[str, Any],
) -> dict[str, Any]:
    """Graph traversal from a named entity up to a given depth."""
    entity = input_json.get("entity", "")
    namespace = input_json.get("namespace", "")
    depth = int(input_json.get("depth", 2))

    executor.check_cancelled(task_id)

    neo4j = get_neo4j_graph()
    cypher = (
        "MATCH (n:Entity {name: $entity, namespace: $ns})"
        f"-[r*1..{depth}]-(m:Entity {{namespace: $ns}}) "
        "RETURN n, r, m"
    )
    rows = await asyncio.to_thread(
        neo4j.query, cypher, {"entity": entity, "ns": namespace}
    )

    nodes: list[dict] = []
    edges: list[dict] = []
    seen_nodes: set[str] = set()

    for row in rows:
        for node_key in ("n", "m"):
            n = row.get(node_key, {})
            name = n.get("name", "")
            if name and name not in seen_nodes:
                seen_nodes.add(name)
                nodes.append({
                    "name": name,
                    "type": n.get("type", "unknown"),
                    "namespace": n.get("namespace", namespace),
                })
        for rel in (row.get("r") or []):
            if isinstance(rel, dict):
                edges.append({
                    "subject": row.get("n", {}).get("name", ""),
                    "predicate": rel.get("predicate", ""),
                    "object": row.get("m", {}).get("name", ""),
                    "namespace": rel.get("namespace", namespace),
                })

    return {"nodes": nodes, "edges": edges}


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------

class MemoryAgentExecutor(LangGraphA2AExecutor):
    """A2A executor for the memory agent — dispatches write/search/traverse."""

    def build_graph(self) -> CompiledStateGraph:
        return build_memory_write_graph()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        # Replicate base class task ID derivation exactly (mirrors agents/base/executor.py)
        cp_task_id = None
        if context.message and context.message.metadata:
            cp_task_id = context.message.metadata.get("controlPlaneTaskId")
        task_id = cp_task_id or context.task_id or str(uuid.uuid4())
        context_id = context.context_id or str(uuid.uuid4())
        self.register_task(task_id)

        parent_span_id = None
        if context.message and context.message.metadata:
            parent_span_id = context.message.metadata.get("parentSpanId")

        from agents.base.tracing import build_langfuse_handler
        langfuse_handler, langfuse_client = build_langfuse_handler(context_id, parent_span_id)
        callbacks = [langfuse_handler] if langfuse_handler else []

        try:
            await self._emit_status(event_queue, task_id, context_id, TaskState.working, "Processing…")

            raw = context.get_user_input() or ""
            try:
                input_json = json.loads(raw)
            except json.JSONDecodeError:
                await self._emit_status(
                    event_queue, task_id, context_id, TaskState.failed,
                    "Input must be a JSON object. Provide 'text' to write, 'query' to search, or 'entity' to traverse.", final=True,
                )
                return

            # Infer operation from which fields are present
            if input_json.get("text"):
                operation = "write"
            elif input_json.get("entity"):
                operation = "traverse"
            elif input_json.get("query"):
                operation = "search"
            else:
                operation = ""

            if operation == "write":
                graph_input = {
                    "input": input_json.get("text", ""),
                    "namespace": input_json.get("namespace", ""),
                    "extracted": None,
                    "retry_count": 0,
                    "last_raw": "",
                    "last_error": "",
                    "stored": False,
                    "entities_added": 0,
                    "relationships_added": 0,
                }
                result: dict[str, Any] = {}
                async for event in self.graph.astream(
                    graph_input,
                    config={
                        "configurable": {
                            "executor": self,
                            "task_id": task_id,
                            "context_id": context_id,
                        },
                        "callbacks": callbacks,
                    },
                    stream_mode="updates",
                ):
                    self.check_cancelled(task_id)
                    node_name = next(iter(event))
                    await self._emit_status(
                        event_queue, task_id, context_id,
                        TaskState.working, f"Running node: {node_name}",
                    )
                    update = event[node_name]
                    if update:
                        result.update(update)
                    await self._emit_status(
                        event_queue, task_id, context_id, TaskState.working,
                        f"NODE_OUTPUT::{node_name}::{json.dumps(update or {})}",
                    )
                output_text = json.dumps({
                    "stored": result.get("stored", False),
                    "namespace": input_json.get("namespace", ""),
                    "entities_added": result.get("entities_added", 0),
                    "relationships_added": result.get("relationships_added", 0),
                })

            elif operation == "search":
                search_result = await _search_memories(self, task_id, input_json)
                output_text = json.dumps(search_result)

            elif operation == "traverse":
                traverse_result = await _traverse_graph(self, task_id, input_json)
                output_text = json.dumps(traverse_result)

            else:
                await self._emit_status(
                    event_queue, task_id, context_id, TaskState.failed,
                    "Could not infer operation. Provide 'text' to write, 'query' to search, or 'entity' to traverse.",
                    final=True,
                )
                return

            final_msg = Message(
                kind="message",
                role="agent",
                message_id=str(uuid.uuid4()),
                task_id=task_id,
                context_id=context_id,
                parts=[Part(root=TextPart(text=output_text))],
            )
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=task_id,
                    context_id=context_id,
                    status=TaskStatus(state=TaskState.completed, message=final_msg),
                    final=True,
                )
            )

        except asyncio.CancelledError:
            await self._emit_status(
                event_queue, task_id, context_id, TaskState.canceled,
                "Task was cancelled.", final=True,
            )
        except Exception as exc:
            import traceback
            tb = traceback.format_exc()
            await self._emit_status(
                event_queue, task_id, context_id, TaskState.failed,
                f"{type(exc).__name__}: {exc}\n\n{tb}", final=True,
            )
        finally:
            if langfuse_client:
                await asyncio.to_thread(langfuse_client.flush)
            self.cleanup_task(task_id)
