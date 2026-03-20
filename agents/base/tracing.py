"""Langfuse tracing helpers for LangGraph agents."""

from __future__ import annotations

import logging
import os
from typing import Any

# OTel emits "Failed to detach context / ValueError: Token was created in a
# different Context" when Langfuse callback spans are created in one asyncio
# Task and cleaned up in another.  This is cosmetic — traces still arrive in
# Langfuse — so suppress the noise here rather than everywhere.
logging.getLogger("opentelemetry.context").setLevel(logging.CRITICAL)


def build_langfuse_handler(
    trace_id: str,
    parent_span_id: str | None,
) -> tuple[Any, Any] | tuple[None, None]:
    """Return (CallbackHandler, Langfuse client) for Langfuse, or (None, None) if not configured.

    trace_id       = context_id (shared across the agent chain) — normalized to 32 hex chars
    parent_span_id = Langfuse span .id (16-char hex) from the calling agent's start_observation()
    """
    if not os.getenv("LANGFUSE_PUBLIC_KEY"):
        return None, None
    from langfuse import Langfuse
    from langfuse.langchain import CallbackHandler

    lf = Langfuse()
    trace_context: dict[str, str] = {"trace_id": trace_id.replace("-", "")}
    if parent_span_id:
        trace_context["parent_span_id"] = parent_span_id.replace("-", "")
    return CallbackHandler(trace_context=trace_context), lf
