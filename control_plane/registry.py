"""Agent Registry — discovers and tracks A2A agents.

On startup, fetches each agent's Agent Card from /.well-known/agent.json.
Periodically polls agents to update their online/offline status.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum

import httpx

from control_plane.config import AgentEndpoint

logger = logging.getLogger(__name__)


class AgentStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"


@dataclass
class RegisteredAgent:
    """An agent known to the control plane."""

    id: str
    url: str
    status: AgentStatus = AgentStatus.OFFLINE
    card: dict | None = None

    @property
    def name(self) -> str:
        if self.card:
            return self.card.get("name", self.id)
        return self.id

    @property
    def description(self) -> str:
        if self.card:
            return self.card.get("description", "")
        return ""

    @property
    def skills(self) -> list[dict]:
        if self.card:
            return self.card.get("skills", [])
        return []

    @property
    def capabilities(self) -> dict:
        if self.card:
            return self.card.get("capabilities", {})
        return {}

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "url": self.url,
            "name": self.name,
            "description": self.description,
            "status": self.status.value,
            "skills": self.skills,
            "capabilities": self.capabilities,
        }


class AgentRegistry:
    """Manages agent discovery and health tracking."""

    def __init__(self, poll_interval: int = 30) -> None:
        self._agents: dict[str, RegisteredAgent] = {}
        self._poll_interval = poll_interval
        self._poll_task: asyncio.Task | None = None
        self._client = httpx.AsyncClient(timeout=5)

    @property
    def agents(self) -> dict[str, RegisteredAgent]:
        return dict(self._agents)

    def get(self, agent_id: str) -> RegisteredAgent | None:
        return self._agents.get(agent_id)

    async def register(self, endpoint: AgentEndpoint) -> RegisteredAgent:
        """Register an agent and fetch its Agent Card."""
        agent_id = endpoint.name or endpoint.url
        agent = RegisteredAgent(id=agent_id, url=endpoint.url.rstrip("/"))
        self._agents[agent_id] = agent
        await self._refresh_agent(agent)
        return agent

    async def register_all(self, endpoints: list[AgentEndpoint]) -> None:
        """Register multiple agents concurrently."""
        await asyncio.gather(
            *(self.register(ep) for ep in endpoints),
            return_exceptions=True,
        )

    async def _refresh_agent(self, agent: RegisteredAgent) -> None:
        """Fetch the agent's card and update status."""
        try:
            r = await self._client.get(
                f"{agent.url}/.well-known/agent.json"
            )
            r.raise_for_status()
            agent.card = r.json()
            agent.status = AgentStatus.ONLINE
            logger.info("Agent %s is online: %s", agent.id, agent.name)
        except Exception:
            agent.status = AgentStatus.OFFLINE
            logger.warning("Agent %s is offline at %s", agent.id, agent.url)

    async def refresh_all(self) -> None:
        """Refresh status of all registered agents."""
        await asyncio.gather(
            *(self._refresh_agent(a) for a in self._agents.values()),
            return_exceptions=True,
        )

    def start_polling(self) -> None:
        """Start periodic health polling in the background."""
        if self._poll_task is None:
            self._poll_task = asyncio.create_task(self._poll_loop())

    def stop_polling(self) -> None:
        """Stop the health polling loop."""
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
