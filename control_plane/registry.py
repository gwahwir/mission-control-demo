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
        self._db_pool = None  # set via init_db() when DATABASE_URL is available

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

    # ---- State persistence (PostgreSQL) -------------------------------------

    _CREATE_AGENTS_TABLE = """
    CREATE TABLE IF NOT EXISTS registered_agents (
        type_id  TEXT NOT NULL,
        url      TEXT NOT NULL,
        PRIMARY KEY (type_id, url)
    );
    """

    async def init_db(self, database_url: str) -> None:
        """Initialise the DB pool and create the agents table if needed."""
        import asyncpg
        self._db_pool = await asyncpg.create_pool(dsn=database_url, min_size=1, max_size=5)
        async with self._db_pool.acquire() as conn:
            await conn.execute(self._CREATE_AGENTS_TABLE)
        logger.info("registry_db_ready")

    async def _save_instance(self, type_id: str, url: str) -> None:
        """Persist a single agent instance to the database."""
        if not self._db_pool:
            return
        try:
            async with self._db_pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO registered_agents (type_id, url) VALUES ($1, $2) "
                    "ON CONFLICT (type_id, url) DO NOTHING",
                    type_id, url,
                )
        except Exception as e:
            logger.warning("registry_save_failed", type_id=type_id, url=url, error=str(e))

    async def _delete_instance(self, type_id: str, url: str) -> None:
        """Remove a single agent instance from the database."""
        if not self._db_pool:
            return
        try:
            async with self._db_pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM registered_agents WHERE type_id = $1 AND url = $2",
                    type_id, url,
                )
        except Exception as e:
            logger.warning("registry_delete_failed", type_id=type_id, url=url, error=str(e))

    async def load_state(self) -> None:
        """Load previously registered agents from the database and health-check them."""
        if not self._db_pool:
            logger.info("registry_state_skip", reason="no database configured")
            return
        try:
            async with self._db_pool.acquire() as conn:
                rows = await conn.fetch("SELECT type_id, url FROM registered_agents")
        except Exception as e:
            logger.warning("registry_state_load_failed", error=str(e))
            return

        count = 0
        for row in rows:
            await self.register_instance(row["type_id"], row["url"])
            count += 1
        logger.info("registry_state_loaded", count=count)

    # ---- Registration -----------------------------------------------------

    async def register(self, endpoint: AgentEndpoint) -> None:
        """Register one agent instance under its type and fetch its card."""
        type_id = endpoint.name or "unknown"
        await self.register_instance(type_id, endpoint.url)

    async def register_instance(self, type_id: str, url: str) -> AgentInstance:
        """Register or re-register a single instance. Idempotent by URL."""
        url = url.rstrip("/")
        if type_id not in self._types:
            self._types[type_id] = AgentType(id=type_id)

        agent_type = self._types[type_id]
        for inst in agent_type.instances:
            if inst.url == url:
                await self._refresh_instance(type_id, inst)
                if inst.status == AgentStatus.OFFLINE:
                    asyncio.create_task(self._retry_refresh_on_registration(type_id, inst))
                return inst

        instance = AgentInstance(url=url)
        agent_type.instances.append(instance)
        await self._refresh_instance(type_id, instance)
        await self._save_instance(type_id, url)
        if instance.status == AgentStatus.OFFLINE:
            asyncio.create_task(self._retry_refresh_on_registration(type_id, instance))
        return instance

    async def _retry_refresh_on_registration(
        self,
        type_id: str,
        instance: AgentInstance,
        attempts: int = 5,
        delay: float = 1.0,
    ) -> None:
        """Retry fetching the agent card shortly after registration.

        Handles the race where the agent's HTTP server isn't fully ready
        at the moment the control plane pings back, without waiting for the
        30-second health-poll cycle.
        """
        for attempt in range(1, attempts + 1):
            await asyncio.sleep(delay)
            if instance.status == AgentStatus.ONLINE:
                return  # Already came online (e.g. via concurrent poll)
            await self._refresh_instance(type_id, instance)
            if instance.status == AgentStatus.ONLINE:
                logger.info(
                    "instance_online_after_registration_retry",
                    type_id=type_id,
                    url=instance.url,
                    attempt=attempt,
                )
                return
            delay = min(delay * 2, 8.0)  # exponential back-off, capped at 8 s
        logger.warning(
            "instance_offline_after_registration_retries",
            type_id=type_id,
            url=instance.url,
        )

    async def remove_instance(self, type_id: str, url: str) -> bool:
        """Remove an instance by URL. Returns True if found and removed."""
        url = url.rstrip("/")
        agent_type = self._types.get(type_id)
        if not agent_type:
            return False
        for i, inst in enumerate(agent_type.instances):
            if inst.url == url:
                agent_type.instances.pop(i)
                logger.info("instance_removed", type_id=type_id, url=url)
                if not agent_type.instances:
                    del self._types[type_id]
                await self._delete_instance(type_id, url)
                return True
        return False

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
        if self._db_pool:
            await self._db_pool.close()
