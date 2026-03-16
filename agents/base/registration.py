"""Self-registration helper for agents.

On startup, agents call ``register_with_control_plane()`` to announce
themselves.  If ``CONTROL_PLANE_URL`` is not set the call is a no-op,
preserving backward compatibility with manual ``AGENT_URLS`` config.
"""

from __future__ import annotations

import asyncio
import os

import httpx
from dotenv import load_dotenv
load_dotenv()


async def register_with_control_plane(type_name: str, agent_url: str) -> None:
    """POST to the control plane to register this agent instance.

    Retries with exponential backoff so agents that start before the
    control plane still get registered once it comes up.
    """
    cp_url = os.getenv("CONTROL_PLANE_URL", "").rstrip("/")
    print(f"Registering to {cp_url}")
    if not cp_url:
        return

    for attempt in range(5):
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.post(
                    f"{cp_url}/register",
                    json={"type_name": type_name, "agent_url": agent_url},
                )
                r.raise_for_status()
                print(f"[registration] Registered with control plane: {r.json()}")
                return
        except Exception as e:
            wait = 2 ** attempt
            print(f"[registration] Attempt {attempt + 1} failed ({e}), retrying in {wait}s...")
            await asyncio.sleep(wait)

    print("[registration] WARNING: Failed to register after 5 attempts")


async def deregister_from_control_plane(type_name: str, agent_url: str) -> None:
    """POST to the control plane to deregister this agent instance on shutdown."""
    cp_url = os.getenv("CONTROL_PLANE_URL", "").rstrip("/")
    if not cp_url:
        return

    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=3, follow_redirects=True) as client:
                r = await client.post(
                    f"{cp_url}/deregister",
                    json={"type_name": type_name, "agent_url": agent_url},
                )
                r.raise_for_status()
                print(f"[registration] Deregistered from control plane: {r.json()}")
                return
        except Exception as e:
            if attempt < 2:
                await asyncio.sleep(1)
            else:
                print(f"[registration] Deregistration failed ({e}), control plane will detect via health poll")
