"""Thin async A2A client for the Control Plane.

Wraps JSON-RPC calls to agent A2A endpoints for:
- message/send (dispatch a task)
- tasks/cancel (cancel a running task)
"""

from __future__ import annotations

import uuid
from typing import Any, AsyncIterator

import httpx


class A2AClient:
    """Async client that talks A2A JSON-RPC to agent servers."""

    def __init__(self, base_url: str, timeout: float = 300) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout)
        self._request_id = 0

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def send_message(
        self,
        text: str,
        *,
        task_id: str | None = None,
        context_id: str | None = None,
        parent_span_id: str | None = None,
    ) -> dict[str, Any]:
        """Send a message/send JSON-RPC request and return the result."""
        message: dict[str, Any] = {
            "kind": "message",
            "role": "user",
            "messageId": str(uuid.uuid4()),
            "parts": [{"kind": "text", "text": text}],
        }
        if context_id:
            message["contextId"] = context_id
        if parent_span_id:
            message["metadata"] = {"parentSpanId": parent_span_id}

        params: dict[str, Any] = {"message": message}
        if task_id:
            params["id"] = task_id

        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "message/send",
            "params": params,
        }
        r = await self._client.post(f"{self._base_url}/", json=payload)
        r.raise_for_status()
        body = r.json()
        if "error" in body:
            raise A2AError(body["error"])
        return body.get("result", {})

    async def cancel_task(
        self,
        task_id: str,
    ) -> dict[str, Any]:
        """Send a tasks/cancel JSON-RPC request."""
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tasks/cancel",
            "params": {"id": task_id},
        }
        r = await self._client.post(f"{self._base_url}/", json=payload)
        r.raise_for_status()
        body = r.json()
        if "error" in body:
            raise A2AError(body["error"])
        return body.get("result", {})

    async def stream_message(
        self,
        text: str,
        *,
        task_id: str | None = None,
        context_id: str | None = None,
        parent_span_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Send message/stream and yield SSE events."""
        message: dict[str, Any] = {
            "kind": "message",
            "role": "user",
            "messageId": str(uuid.uuid4()),
            "parts": [{"kind": "text", "text": text}],
        }
        if task_id:
            message["taskId"] = task_id
        if context_id:
            message["contextId"] = context_id
        if parent_span_id:
            message["metadata"] = {"parentSpanId": parent_span_id}

        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "message/stream",
            "params": {"message": message},
        }
        async with self._client.stream(
            "POST", f"{self._base_url}/", json=payload
        ) as response:
            import json

            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    data = line[len("data:") :].strip()
                    if data:
                        yield json.loads(data)

    async def close(self) -> None:
        await self._client.aclose()


class A2AError(Exception):
    """Error returned by an A2A agent."""

    def __init__(self, error: dict[str, Any]) -> None:
        self.code = error.get("code", -1)
        self.error_message = error.get("message", "Unknown error")
        super().__init__(f"A2A error {self.code}: {self.error_message}")
