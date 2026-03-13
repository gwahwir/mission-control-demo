"""Control Plane configuration."""

from __future__ import annotations

from pydantic import BaseModel


class AgentEndpoint(BaseModel):
    """A registered agent's connection info."""

    url: str
    name: str | None = None


class ControlPlaneSettings(BaseModel):
    """Top-level settings for the Control Plane."""

    host: str = "0.0.0.0"
    port: int = 8000
    agents: list[AgentEndpoint] = []
    health_poll_interval_seconds: int = 30


def load_settings() -> ControlPlaneSettings:
    """Load settings from environment / defaults.

    For now returns a default config pointing at the echo agent.
    Later this can read from a YAML/JSON config file or env vars.
    """
    return ControlPlaneSettings(
        agents=[
            AgentEndpoint(url="http://localhost:8001", name="echo-agent"),
        ]
    )
