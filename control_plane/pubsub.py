"""Pub/sub broker for pushing task updates to WebSocket clients.

Two implementations share the same synchronous subscribe/unsubscribe
interface and an async publish method:

* ``InMemoryBroker``  — asyncio queues; works for a single control-plane
                        process (default, no extra dependencies).
* ``RedisBroker``     — Redis pub/sub; enables fan-out across multiple
                        control-plane instances. Activated when REDIS_URL
                        is set.

Usage in routes::

    queue: asyncio.Queue = asyncio.Queue()
    _broker.subscribe(task_id, queue)
    try:
        while True:
            data = await asyncio.wait_for(queue.get(), timeout=30)
            await websocket.send_json(data)
    finally:
        _broker.unsubscribe(task_id, queue)
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from control_plane.log import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# In-memory broker (default)
# ---------------------------------------------------------------------------

class InMemoryBroker:
    """Fan-out task updates to local asyncio queues.

    One queue per WebSocket connection. Suitable for a single-process
    control-plane deployment.
    """

    def __init__(self) -> None:
        self._subs: dict[str, list[asyncio.Queue]] = {}

    def subscribe(self, channel: str, queue: asyncio.Queue) -> None:
        self._subs.setdefault(channel, []).append(queue)

    def unsubscribe(self, channel: str, queue: asyncio.Queue) -> None:
        subs = self._subs.get(channel, [])
        if queue in subs:
            subs.remove(queue)

    async def publish(self, channel: str, data: dict[str, Any]) -> None:
        for queue in list(self._subs.get(channel, [])):
            await queue.put(data)

    async def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Redis broker (multi-instance)
# ---------------------------------------------------------------------------

class RedisBroker:
    """Fan-out task updates via Redis pub/sub.

    Enables multiple control-plane instances to push updates to any
    WebSocket client, regardless of which instance the client connected to.

    Activated when ``REDIS_URL`` is set in the environment.
    """

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._pub: Any = None                              # publish client
        self._sub_tasks: dict[asyncio.Queue, asyncio.Task] = {}

    async def init(self) -> None:
        import redis.asyncio as aioredis
        self._pub = aioredis.from_url(self._redis_url, decode_responses=False)
        logger.info("redis_broker_connected", url=self._redis_url)

    def subscribe(self, channel: str, queue: asyncio.Queue) -> None:
        task = asyncio.create_task(self._listen(channel, queue))
        self._sub_tasks[queue] = task

    def unsubscribe(self, channel: str, queue: asyncio.Queue) -> None:
        if task := self._sub_tasks.pop(queue, None):
            task.cancel()

    async def _listen(self, channel: str, queue: asyncio.Queue) -> None:
        import redis.asyncio as aioredis
        client = aioredis.from_url(self._redis_url, decode_responses=True)
        try:
            async with client.pubsub() as ps:
                await ps.subscribe(f"mc:task:{channel}")
                async for message in ps.listen():
                    if message["type"] == "message":
                        await queue.put(json.loads(message["data"]))
        except asyncio.CancelledError:
            pass
        finally:
            await client.aclose()

    async def publish(self, channel: str, data: dict[str, Any]) -> None:
        await self._pub.publish(f"mc:task:{channel}", json.dumps(data))

    async def close(self) -> None:
        for task in self._sub_tasks.values():
            task.cancel()
        if self._pub:
            await self._pub.aclose()
