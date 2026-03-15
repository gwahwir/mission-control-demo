"""Control Plane configuration.

All settings are read from environment variables:

    AGENT_URLS    Comma-separated agent URLs. Each entry may optionally
                  prefix a type name with ``@`` to group multiple instances
                  under the same logical agent:

                      # Single instance (type name derived from hostname)
                      AGENT_URLS=http://localhost:8001

                      # Two instances of the same type + one of another
                      AGENT_URLS=echo-agent@http://echo-1:8001,echo-agent@http://echo-2:8001,summariser@http://sum-1:8002

    DATABASE_URL  asyncpg-compatible PostgreSQL DSN, e.g.:
                      postgresql://user:password@host:5432/dbname
                  When not set the control plane uses an in-memory store.

    REDIS_URL     Redis URL for cross-process WebSocket fan-out, e.g.:
                      redis://localhost:6379/0
                  When not set an in-memory broker is used (single process).

    LOG_LEVEL     Logging verbosity (DEBUG / INFO / WARNING / ERROR).
                  Defaults to INFO.
"""

from __future__ import annotations

import os

from pydantic import BaseModel


class AgentEndpoint(BaseModel):
    url: str
    name: str | None = None   # agent type name


class ControlPlaneSettings(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    agents: list[AgentEndpoint] = []
    health_poll_interval_seconds: int = 30
    database_url: str | None = None
    redis_url: str | None = None


def load_settings() -> ControlPlaneSettings:
    raw = os.getenv("AGENT_URLS", "http://localhost:8001")
    agents: list[AgentEndpoint] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if "@" in entry:
            type_name, url = entry.split("@", 1)
        else:
            url = entry
            # Derive type name from hostname (backward-compatible)
            type_name = url.rstrip("/").rsplit(":", 1)[0].rsplit("/", 1)[-1]
        agents.append(AgentEndpoint(url=url.strip(), name=type_name.strip()))

    return ControlPlaneSettings(
        agents=agents,
        database_url=os.getenv("DATABASE_URL"),
        redis_url=os.getenv("REDIS_URL"),
    )
