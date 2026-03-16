"""Agent Registry — discovers and tracks A2A agents.

Supports multiple instances of the same agent type for horizontal scaling.
Instances are grouped under an AgentType. Dispatch uses least-active-tasks
selection so work is spread evenly across healthy instances.

Configuration (via AGENT_URLS env var)::

    # Two instances of the same type, one instance of another type
    AGENT_URLS=echo-agent@http://echo-1:8001,echo-agent@http://echo-2:8001,summariser@http://sum-1:8002

    # Backward-compatible: type name derived from URL hostname
    AGENT_URLS=http://localhost:8001
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum

import httpx

from control_plane.config import AgentEndpoint
from control_plane.log import get_logger

logger = get_logger(__name__)


class AgentStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"


# ---------------------------------------------------------------------------
# AgentInstance — one running process
# ---------------------------------------------------------------------------

@dataclass
class AgentInstance:
    """A single running process for an agent type."""

    url: str
    status: AgentStatus = AgentStatus.OFFLINE
    card: dict | None = None
    active_tasks: int = 0

    @property
    def name(self) -> str:
        return self.card.get("name", self.url) if self.card else self.url


# ---------------------------------------------------------------------------
# AgentType — logical agent with ≥1 instances
# ---------------------------------------------------------------------------

@dataclass
class AgentType:
    """A logical agent, potentially backed by multiple instances."""

    id: str
    instances: list[AgentInstance] = field(default_factory=list)

    def pick(self) -> AgentInstance | None:
        """Return the online instance with fewest active tasks (least-connections)."""
        online = [i for i in self.instances if i.status == AgentStatus.ONLINE]
        if not online:
            return None
        return min(online, key=lambda i: i.active_tasks)

    # ---- Aggregate properties (derived from instances) --------------------

    @property
    def status(self) -> str:
        return "online" if any(i.status == AgentStatus.ONLINE for i in self.instances) else "offline"

    @property
    def name(self) -> str:
        for i in self.instances:
            if i.card:
                return i.card.get("name", self.id)
        return self.id

    @property
    def description(self) -> str:
        for i in self.instances:
            if i.card:
                return i.card.get("description", "")
        return ""

    @property
    def skills(self) -> list[dict]:
        for i in self.instances:
            if i.card:
                return i.card.get("skills", [])
        return []

    @property
    def capabilities(self) -> dict:
        for i in self.instances:
            if i.card:
                return i.card.get("capabilities", {})
        return {}

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "status": self.status,
            "skills": self.skills,
            "capabilities": self.capabilities,
            "instances": [
                {
                    "url": i.url,
                    "status": i.status.value,
                    "active_tasks": i.active_tasks,
                }
                for i in self.instances
            ],
        }


# ---------------------------------------------------------------------------
# AgentRegistry
# ---------------------------------------------------------------------------

class AgentRegistry:
    """Manages agent type discovery, instance health, and dispatch selection."""

    def __init__(self, poll_interval: int = 30) -> None:
        self._types: dict[str, AgentType] = {}
        self._poll_interval = poll_interval
        self._poll_task: asyncio.Task | None = None
        self._client = httpx.AsyncClient(timeout=5)

    # ---- Public read API --------------------------------------------------

    @property
    def agents(self) -> dict[str, AgentType]:
        """All registered agent types, keyed by type ID."""
        return dict(self._types)

    def get(self, agent_type_id: str) -> AgentType | None:
        return self._types.get(agent_type_id)

    def pick_instance(self, agent_type_id: str) -> AgentInstance | None:
        """Return the best available instance for a type (least-connections)."""
        agent_type = self.get(agent_type_id)
        return agent_type.pick() if agent_type else None

    # ---- Registration -----------------------------------------------------

    async def register(self, endpoint: AgentEndpoint) -> None:
        """Register one agent instance under its type and fetch its card."""
        type_id = endpoint.name or "unknown"
        if type_id not in self._types:
            self._types[type_id] = AgentType(id=type_id)

        instance = AgentInstance(url=endpoint.url.rstrip("/"))
        self._types[type_id].instances.append(instance)
        await self._refresh_instance(type_id, instance)

    async def register_all(self, endpoints: list[AgentEndpoint]) -> None:
        await asyncio.gather(
            *(self.register(ep) for ep in endpoints),
            return_exceptions=True,
        )

    # ---- Health polling ---------------------------------------------------

    async def _refresh_instance(self, type_id: str, instance: AgentInstance) -> None:
        # Try the current spec path first, fall back to the deprecated one
        for path in ("/.well-known/agent-card.json", "/.well-known/agent.json"):
            try:
                r = await self._client.get(f"{instance.url}{path}")
                r.raise_for_status()
                instance.card = r.json()
                instance.status = AgentStatus.ONLINE
                logger.info("instance_online", type_id=type_id, url=instance.url, name=instance.name)
                return
            except Exception:
                continue
        instance.status = AgentStatus.OFFLINE
        logger.warning("instance_offline", type_id=type_id, url=instance.url)

    async def refresh_all(self) -> None:
        tasks = [
            self._refresh_instance(type_id, inst)
            for type_id, agent_type in self._types.items()
            for inst in agent_type.instances
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    def start_polling(self) -> None:
        if self._poll_task is None:
            self._poll_task = asyncio.create_task(self._poll_loop())

    def stop_polling(self) -> None:
        if self._poll_task:
            self._poll_task.cancel()
            self._poll_task = None

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self._poll_interval)
            await self.refresh_all()

    async def close(self) -> None:
        self.stop_polling()
        await self._client.aclose()
