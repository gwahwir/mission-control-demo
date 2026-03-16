"""Reusable base class that bridges A2A requests to LangGraph graph execution.

Subclasses only need to implement ``build_graph()`` to return a compiled
LangGraph ``StateGraph``.  The base class handles:

* Translating A2A messages into LangGraph input state
* Streaming ``TaskStatusUpdateEvent``s at each node transition
* Clean cancellation via ``CancellableMixin``
* Publishing the final result as an A2A artifact
"""

from __future__ import annotations

import asyncio
import uuid
from abc import abstractmethod
from typing import Any

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import (
    Artifact,
    Message,
    Part,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)
from langgraph.graph.state import CompiledStateGraph

from agents.base.cancellation import CancellableMixin


class LangGraphA2AExecutor(AgentExecutor, CancellableMixin):
    """Base executor that runs a LangGraph graph behind an A2A interface.

    Subclasses must implement:
        ``build_graph()`` – return a compiled ``StateGraph``

    Optionally override:
        ``prepare_input(context)`` – customise the dict passed to the graph
        ``format_output(result)`` – customise the text extracted from the result
    """

    def __init__(self) -> None:
        CancellableMixin.__init__(self)
        self._graph: CompiledStateGraph | None = None
        self._running_tasks: dict[str, asyncio.Task[None]] = {}

    @property
    def graph(self) -> CompiledStateGraph:
        if self._graph is None:
            self._graph = self.build_graph()
        return self._graph

    # ------------------------------------------------------------------
    # Graph introspection
    # ------------------------------------------------------------------

    def get_graph_topology(self) -> dict[str, Any]:
        """Return the graph's nodes and edges as a serialisable dict."""
        drawable = self.graph.get_graph()
        nodes = [
            {"id": nid, "name": n.name}
            for nid, n in drawable.nodes.items()
            if nid not in ("__start__", "__end__")
        ]
        edges = [
            {"source": e.source, "target": e.target}
            for e in drawable.edges
            if e.source != "__start__" and e.target != "__end__"
        ]
        # Find the entry node (target of __start__)
        entry_node = None
        for e in drawable.edges:
            if e.source == "__start__":
                entry_node = e.target
                break
        return {"nodes": nodes, "edges": edges, "entry_node": entry_node}

    # ------------------------------------------------------------------
    # Abstract / overridable hooks
    # ------------------------------------------------------------------

    @abstractmethod
    def build_graph(self) -> CompiledStateGraph:
        """Return a compiled LangGraph StateGraph."""

    def prepare_input(self, context: RequestContext) -> dict[str, Any]:
        """Convert the A2A request into graph input state.

        Default: ``{"input": "<user text>"}``
        """
        user_text = context.get_user_input() or ""
        return {"input": user_text}

    def format_output(self, result: dict[str, Any]) -> str:
        """Extract a human-readable string from the graph result.

        Default: returns ``result["output"]`` or the full dict as a string.
        """
        if "output" in result:
            return str(result["output"])
        return str(result)

    # ------------------------------------------------------------------
    # A2A interface
    # ------------------------------------------------------------------

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        task_id = context.task_id or str(uuid.uuid4())
        context_id = context.context_id or str(uuid.uuid4())
        self.register_task(task_id)

        try:
            # Signal: working
            await self._emit_status(
                event_queue, task_id, context_id, TaskState.working, "Processing…"
            )

            graph_input = self.prepare_input(context)

            # Stream through graph nodes
            result: dict[str, Any] = {}
            async for event in self.graph.astream(
                graph_input,
                config={"configurable": {"executor": self, "task_id": task_id}},
                stream_mode="updates",
            ):
                # Each event is {node_name: state_update}
                self.check_cancelled(task_id)

                node_name = next(iter(event))
                await self._emit_status(
                    event_queue,
                    task_id,
                    context_id,
                    TaskState.working,
                    f"Running node: {node_name}",
                )
                result.update(event[node_name])

            # Build final output
            output_text = self.format_output(result)

            # Emit completed status with artifact
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
                    status=TaskStatus(
                        state=TaskState.completed,
                        message=final_msg,
                    ),
                    final=True,
                )
            )

        except asyncio.CancelledError:
            await self._emit_status(
                event_queue,
                task_id,
                context_id,
                TaskState.canceled,
                "Task was cancelled.",
                final=True,
            )
        except Exception as exc:
            import traceback
            tb = traceback.format_exc()
            await self._emit_status(
                event_queue,
                task_id,
                context_id,
                TaskState.failed,
                f"{type(exc).__name__}: {exc}\n\n{tb}",
                final=True,
            )
        finally:
            self.cleanup_task(task_id)

    async def cancel(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        task_id = context.task_id or ""
        context_id = context.context_id or ""
        self.request_cancel(task_id)

        await self._emit_status(
            event_queue,
            task_id,
            context_id,
            TaskState.canceled,
            "Task cancelled by user.",
            final=True,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _emit_status(
        self,
        event_queue: EventQueue,
        task_id: str,
        context_id: str,
        state: TaskState,
        text: str,
        *,
        final: bool = False,
    ) -> None:
        msg = Message(
            kind="message",
            role="agent",
            message_id=str(uuid.uuid4()),
            task_id=task_id,
            context_id=context_id,
            parts=[Part(root=TextPart(text=text))],
        )
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                status=TaskStatus(state=state, message=msg),
                final=final,
            )
        )
