"""Executor for the wiki agent — dispatches ingest/query/lint."""
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
from agents.wiki_agent.graph import build_wiki_ingest_graph
from agents.wiki_agent.wiki_ops import run_lint, run_query

logger = logging.getLogger(__name__)


class WikiAgentExecutor(LangGraphA2AExecutor):
    """A2A executor for the wiki agent — dispatches ingest/query/lint."""

    def build_graph(self) -> CompiledStateGraph:
        return build_wiki_ingest_graph()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        # Mirror memory_agent task_id derivation exactly
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
                    "Input must be a JSON object. Provide 'input_text' to ingest, 'query' to query, or {} to lint.",
                    final=True,
                )
                return

            # Infer operation from which fields are present (mirrors memory_agent pattern)
            if input_json.get("input_text"):
                operation = "ingest"
            elif input_json.get("query"):
                operation = "query"
            else:
                operation = "lint"

            if operation == "ingest":
                graph_input = {
                    "input_text": input_json.get("input_text", ""),
                    "source_url": input_json.get("source_url", ""),
                    "source_title": input_json.get("source_title", ""),
                    "source_metadata": input_json.get("source_metadata", {}),
                    "namespace": input_json.get("namespace", "wiki_geo"),
                    "summary": "",
                    "extracted": {},
                    "related_pages": [],
                    "updated_pages": [],
                    "new_page_path": "",
                    "stored_to_memory": False,
                    "baseline_versions": {},
                    "files_written": [],
                    "retry_count": 0,
                    "last_error": "",
                    "output": "",
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
                output_text = result.get("output", json.dumps({"status": "ingest_complete"}))

            elif operation == "query":
                await self._emit_status(event_queue, task_id, context_id, TaskState.working, "Running query…")
                query_result = await run_query(self, task_id, input_json)
                output_text = json.dumps(query_result)

            else:  # lint
                await self._emit_status(event_queue, task_id, context_id, TaskState.working, "Running lint…")
                lint_result = await run_lint(self, task_id, input_json)
                output_text = json.dumps(lint_result)

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
