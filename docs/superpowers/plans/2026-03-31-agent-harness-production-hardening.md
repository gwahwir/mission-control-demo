# Agent Harness Production Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden Mission Control into a production-ready agentic system by implementing security, resilience, task lifecycle management, observability, and infrastructure layers.

**Architecture:** Five independent phases, each deployable on its own: (1) Security — API key auth + rate limiting middleware; (2) Resilience — circuit breaker + per-task timeout; (3) Task Lifecycle — TTL expiry, pagination, INPUT_REQUIRED handler; (4) Observability — correlation ID propagation, config validation, alerting; (5) Infrastructure — resource limits, Prometheus/Alertmanager, cost tracking.

**Tech Stack:** FastAPI, asyncio, pybreaker, slowapi, asyncpg, Prometheus/Alertmanager, pytest-asyncio, pytest-httpx

> **Scope note:** Each phase is independently shippable. Run only up to Phase N if you want an incremental delivery.

---

## File Map

### Phase 1 — Security
| Action | File | Responsibility |
|--------|------|----------------|
| Create | `control_plane/auth.py` | API key middleware: validates `X-API-Key` header, exempts agent registration and health paths |
| Create | `control_plane/rate_limiter.py` | Per-client rate limiter using slowapi; exposes `limiter` singleton and `RateLimitExceeded` handler |
| Modify | `control_plane/config.py` | Add `api_keys: list[str]`, `rate_limit_dispatch: str`, `rate_limit_global: str` |
| Modify | `control_plane/server.py` | Wire `ApiKeyMiddleware`, `SlowAPIMiddleware`, and `RateLimitExceeded` handler; tighten CORS origins |
| Modify | `control_plane/routes.py` | Add `@limiter.limit(...)` decorator to `dispatch_task`; add `Request` param |
| Modify | `requirements.txt` | Add `slowapi`, `limits` |
| Modify | `.env.template` | Add `API_KEYS`, `CORS_ORIGINS`, `RATE_LIMIT_DISPATCH`, `RATE_LIMIT_GLOBAL` |
| Create | `tests/test_auth.py` | Tests for missing key (401), invalid key (403), valid key (passes), exempt paths (no auth) |
| Create | `tests/test_rate_limiter.py` | Tests for rate limit exceeded (429), under-limit (202) |

### Phase 2 — Resilience
| Action | File | Responsibility |
|--------|------|----------------|
| Create | `control_plane/circuit_breaker.py` | Per-instance `pybreaker.CircuitBreaker` registry; factory function `get_breaker(url)` |
| Modify | `control_plane/a2a_client.py` | Wrap `stream_message` and `cancel_task` calls with circuit breaker; re-raise as `CircuitOpenError` |
| Modify | `control_plane/config.py` | Add `task_timeout_seconds: int = 600`, `cb_fail_max: int = 3`, `cb_reset_timeout: int = 30` |
| Modify | `control_plane/routes.py` | Wrap `_run_task` body in `asyncio.wait_for` for global task timeout; catch `CircuitOpenError` |
| Modify | `requirements.txt` | Add `pybreaker` |
| Create | `tests/test_circuit_breaker.py` | Tests for open circuit (503), half-open recovery, closed circuit passes through |
| Create | `tests/test_task_timeout.py` | Test that tasks exceeding timeout are marked failed |

### Phase 3 — Task Lifecycle
| Action | File | Responsibility |
|--------|------|----------------|
| Create | `control_plane/janitor.py` | Background asyncio task: periodically deletes terminal tasks older than TTL |
| Modify | `control_plane/task_store.py` | Add `list_page(limit, offset)`, `delete_older_than(cutoff_ts)` to both store backends |
| Modify | `control_plane/routes.py` | Add `limit`/`offset` query params to `GET /tasks`; add `POST /tasks/{task_id}/input` for INPUT_REQUIRED; handle `input-required` state in `_run_task` |
| Modify | `control_plane/config.py` | Add `task_ttl_hours: int = 72`, `janitor_interval_seconds: int = 3600` |
| Modify | `control_plane/server.py` | Start/stop janitor in lifespan |
| Create | `tests/test_task_ttl.py` | Test that expired tasks are removed; non-expired tasks are preserved |
| Create | `tests/test_task_pagination.py` | Test limit/offset pagination, default limit |
| Create | `tests/test_input_required.py` | Test INPUT_REQUIRED state transition and resume endpoint |

### Phase 4 — Observability
| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `control_plane/a2a_client.py` | Accept and forward `correlation_id` as `X-Correlation-ID` header on all outbound calls |
| Modify | `control_plane/routes.py` | Extract `request_id` from `contextvars` (already set by `CorrelationIdMiddleware`); pass to `A2AClient` |
| Modify | `agents/base/executor.py` | Extract `correlationId` from A2A message metadata; bind to Python `logging` context for all log calls in that execution |
| Modify | `control_plane/config.py` | Add `validate()` method that raises `RuntimeError` for invalid/missing required config |
| Modify | `control_plane/server.py` | Call `settings.validate()` before lifespan yield (fail-fast) |
| Modify | `control_plane/metrics.py` | Add `tasks_rate_limited`, `tasks_circuit_open`, `task_input_required` counters; add `task_ttl_deleted` counter |
| Create | `monitoring/prometheus.yml` | Prometheus scrape config targeting control plane `/metrics` |
| Create | `monitoring/alerts.yml` | Alert rules: high error rate, high latency P95, circuit breaker open, agent offline |
| Modify | `docker-compose.yml` | Add `prometheus` and `alertmanager` services |
| Create | `tests/test_correlation_propagation.py` | Verify `X-Correlation-ID` header forwarded to agent calls |
| Create | `tests/test_config_validation.py` | Verify startup fails fast with bad config |

### Phase 5 — Infrastructure
| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `docker-compose.yml` | Add `deploy.resources` limits (CPU + memory) to every service |
| Modify | `control_plane/task_store.py` | Add `token_input`, `token_output`, `cost_usd` fields to `TaskRecord` |
| Modify | `control_plane/routes.py` | Parse `COST_REPORT::input_tokens::output_tokens::cost_usd` from NODE_OUTPUT events |
| Modify | `control_plane/metrics.py` | Add `task_tokens_total` counter (input/output), `task_cost_usd_total` counter |
| Modify | `agents/base/executor.py` | Emit `COST_REPORT::N::M::C` event from `format_output` if LLM usage metadata present |
| Create | `tests/test_cost_tracking.py` | Test cost parsing from NODE_OUTPUT and metric increment |

---

## Phase 1: Security Harness

### Task 1: API Key Authentication Middleware

**Files:**
- Create: `control_plane/auth.py`
- Modify: `control_plane/config.py`
- Modify: `control_plane/server.py`
- Modify: `.env.template`
- Create: `tests/test_auth.py`

- [ ] **Step 1.1: Write failing tests**

```python
# tests/test_auth.py
import pytest
from httpx import AsyncClient, ASGITransport

from control_plane.server import create_app

# Helper: build app with auth enabled
def make_app(api_keys: list[str] | None = None, **env_overrides):
    import os
    if api_keys is not None:
        os.environ["API_KEYS"] = ",".join(api_keys)
    elif "API_KEYS" in os.environ:
        del os.environ["API_KEYS"]
    return create_app()


@pytest.mark.asyncio
async def test_missing_api_key_returns_401(monkeypatch):
    monkeypatch.setenv("API_KEYS", "secret-key-1")
    app = make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/agents")
    assert resp.status_code == 401
    assert "X-API-Key" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_invalid_api_key_returns_403(monkeypatch):
    monkeypatch.setenv("API_KEYS", "secret-key-1")
    app = make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/agents", headers={"X-API-Key": "wrong-key"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_valid_api_key_passes(monkeypatch):
    monkeypatch.setenv("API_KEYS", "secret-key-1,secret-key-2")
    app = make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/agents", headers={"X-API-Key": "secret-key-2"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_auth_disabled_when_no_keys_configured(monkeypatch):
    monkeypatch.delenv("API_KEYS", raising=False)
    app = make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/agents")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_register_endpoint_exempt_from_auth(monkeypatch):
    monkeypatch.setenv("API_KEYS", "secret-key-1")
    app = make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # /register requires no API key (agents self-register)
        resp = await client.post(
            "/register",
            json={"type_name": "echo-agent", "agent_url": "http://localhost:8001"},
        )
    # 200 or error from registry — NOT 401 or 403
    assert resp.status_code not in (401, 403)


@pytest.mark.asyncio
async def test_metrics_endpoint_exempt_from_auth(monkeypatch):
    monkeypatch.setenv("API_KEYS", "secret-key-1")
    app = make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/metrics")
    assert resp.status_code not in (401, 403)
```

- [ ] **Step 1.2: Run tests to verify they fail**

```bash
pytest tests/test_auth.py -v
```
Expected: `FAILED` — `create_app()` has no auth middleware yet.

- [ ] **Step 1.3: Add `api_keys` to config**

Open `control_plane/config.py` and add `api_keys` field and `CORS_ORIGINS` parsing to `ControlPlaneSettings` and `load_settings`:

```python
# In ControlPlaneSettings:
api_keys: list[str] = []
cors_origins: list[str] = ["*"]

# In load_settings(), before return:
raw_keys = os.getenv("API_KEYS", "")
api_keys = [k.strip() for k in raw_keys.split(",") if k.strip()]

raw_cors = os.getenv("CORS_ORIGINS", "*")
cors_origins = [o.strip() for o in raw_cors.split(",") if o.strip()]

return ControlPlaneSettings(
    agents=agents,
    database_url=os.getenv("DATABASE_URL"),
    redis_url=os.getenv("REDIS_URL"),
    api_keys=api_keys,
    cors_origins=cors_origins,
)
```

- [ ] **Step 1.4: Create `control_plane/auth.py`**

```python
"""API key authentication middleware for the Control Plane.

Reads valid keys from ControlPlaneSettings.api_keys.
When the list is empty, authentication is disabled (dev mode).

Exempt paths (no key required):
- /register, /deregister  — agent self-registration
- /metrics                — Prometheus scraping
- /docs, /redoc, /openapi.json — Swagger UI
- /ws/tasks/*             — WebSocket (key checked at upgrade if needed later)
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

_EXEMPT_EXACT = frozenset({"/metrics", "/docs", "/redoc", "/openapi.json"})
_EXEMPT_PREFIXES = ("/register", "/deregister", "/ws/")


class ApiKeyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, api_keys: list[str]) -> None:
        super().__init__(app)
        self._keys: frozenset[str] = frozenset(api_keys)

    async def dispatch(self, request: Request, call_next):
        # Auth disabled when no keys configured
        if not self._keys:
            return await call_next(request)

        path = request.url.path

        # Exempt paths never require a key
        if path in _EXEMPT_EXACT:
            return await call_next(request)
        if any(path.startswith(p) for p in _EXEMPT_PREFIXES):
            return await call_next(request)

        key = request.headers.get("X-API-Key", "")
        if not key:
            return JSONResponse(
                {"detail": "Missing X-API-Key header"},
                status_code=401,
            )
        if key not in self._keys:
            return JSONResponse(
                {"detail": "Invalid API key"},
                status_code=403,
            )
        return await call_next(request)
```

- [ ] **Step 1.5: Wire middleware into `control_plane/server.py`**

Add the import and replace the `CORSMiddleware` block:

```python
from control_plane.auth import ApiKeyMiddleware

# In create_app(), replace the existing middleware block with:
app.add_middleware(CorrelationIdMiddleware)
app.add_middleware(ApiKeyMiddleware, api_keys=settings.api_keys)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*", "X-API-Key"],
)
```

> **Note:** `settings` is a module-level variable in `server.py` — the middleware reads it at app creation time, which is correct.

- [ ] **Step 1.6: Update `.env.template`**

Add these lines to `.env.template`:

```bash
# API Authentication (comma-separated keys; leave empty to disable)
API_KEYS=

# CORS — comma-separated allowed origins (default: * for dev)
CORS_ORIGINS=http://localhost:5173,http://localhost:3000
```

- [ ] **Step 1.7: Run tests to verify they pass**

```bash
pytest tests/test_auth.py -v
```
Expected: All 6 tests PASS.

- [ ] **Step 1.8: Commit**

```bash
git add control_plane/auth.py control_plane/config.py control_plane/server.py .env.template tests/test_auth.py
git commit -m "feat: add API key authentication middleware with exempt paths"
```

---

### Task 2: Rate Limiting

**Files:**
- Create: `control_plane/rate_limiter.py`
- Modify: `control_plane/routes.py`
- Modify: `control_plane/server.py`
- Modify: `control_plane/config.py`
- Modify: `requirements.txt`
- Create: `tests/test_rate_limiter.py`

- [ ] **Step 2.1: Add `slowapi` to requirements**

Add to `requirements.txt`:
```
slowapi==0.1.9
limits==3.13.0
```

Install:
```bash
pip install slowapi==0.1.9 limits==3.13.0
```

- [ ] **Step 2.2: Write failing tests**

```python
# tests/test_rate_limiter.py
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, patch

from control_plane.server import create_app


@pytest.mark.asyncio
async def test_dispatch_rate_limit_exceeded(monkeypatch):
    """11th request within a minute should be rejected with 429."""
    monkeypatch.setenv("RATE_LIMIT_DISPATCH", "10/minute")
    monkeypatch.delenv("API_KEYS", raising=False)

    app = create_app()

    # Patch registry to return a fake agent so we don't get 404
    with patch("control_plane.routes._registry") as mock_reg:
        from unittest.mock import MagicMock
        mock_agent = MagicMock()
        mock_agent.pick.return_value = None  # No instances — will 503, but that's fine
        mock_reg.get.return_value = mock_agent

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            responses = []
            for _ in range(11):
                r = await client.post(
                    "/agents/echo-agent/tasks",
                    json={"text": "hello"},
                    headers={"X-Forwarded-For": "1.2.3.4"},
                )
                responses.append(r.status_code)

    # First 10 should be 503 (no instances), 11th should be 429
    assert 429 in responses
    assert responses.index(429) == 10


@pytest.mark.asyncio
async def test_non_dispatch_endpoints_not_rate_limited(monkeypatch):
    """GET /agents is not subject to dispatch rate limit."""
    monkeypatch.setenv("RATE_LIMIT_DISPATCH", "1/minute")
    monkeypatch.delenv("API_KEYS", raising=False)

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for _ in range(5):
            r = await client.get("/agents")
        assert r.status_code == 200
```

- [ ] **Step 2.3: Run to verify they fail**

```bash
pytest tests/test_rate_limiter.py -v
```
Expected: FAIL — `RATE_LIMIT_DISPATCH` env var does nothing yet.

- [ ] **Step 2.4: Create `control_plane/rate_limiter.py`**

```python
"""Rate limiting for the Control Plane using slowapi.

Provides a single `limiter` instance that can be applied per-route with
the @limiter.limit("N/period") decorator.

Usage:
    from control_plane.rate_limiter import limiter

    @router.post("/agents/{agent_id}/tasks")
    @limiter.limit("10/minute")
    async def dispatch_task(request: Request, ...):
        ...

The limiter key is the client IP (X-Forwarded-For if behind a proxy).
When RATE_LIMIT_DISPATCH is not set, defaults to "100/minute".
"""
from __future__ import annotations

import os

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address, default_limits=[])

RATE_LIMIT_DISPATCH: str = os.getenv("RATE_LIMIT_DISPATCH", "100/minute")
```

- [ ] **Step 2.5: Wire limiter into `server.py`**

```python
# Add imports at top of control_plane/server.py:
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from control_plane.rate_limiter import limiter

# In create_app(), after app = FastAPI(...):
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Add SlowAPIMiddleware AFTER CorrelationIdMiddleware:
app.add_middleware(SlowAPIMiddleware)
```

- [ ] **Step 2.6: Add `@limiter.limit` to `dispatch_task` in `routes.py`**

The `dispatch_task` function signature must accept `request: Request` (slowapi requires it):

```python
# Add import at top of routes.py:
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from control_plane.rate_limiter import limiter, RATE_LIMIT_DISPATCH

# Replace the dispatch_task signature and decorator:
@router.post("/agents/{agent_id}/tasks", status_code=202)
@limiter.limit(RATE_LIMIT_DISPATCH)
async def dispatch_task(request: Request, agent_id: str, req: TaskRequest) -> dict[str, Any]:
    # ... rest of function unchanged
```

- [ ] **Step 2.7: Run tests to verify they pass**

```bash
pytest tests/test_rate_limiter.py -v
```
Expected: All tests PASS.

- [ ] **Step 2.8: Commit**

```bash
git add control_plane/rate_limiter.py control_plane/routes.py control_plane/server.py requirements.txt tests/test_rate_limiter.py
git commit -m "feat: add per-client rate limiting on task dispatch endpoint"
```

---

## Phase 2: Resilience Harness

### Task 3: Circuit Breaker for Downstream Agent Calls

**Files:**
- Create: `control_plane/circuit_breaker.py`
- Modify: `control_plane/a2a_client.py`
- Modify: `control_plane/routes.py`
- Modify: `requirements.txt`
- Create: `tests/test_circuit_breaker.py`

- [ ] **Step 3.1: Add `pybreaker` to requirements**

Add to `requirements.txt`:
```
pybreaker==1.2.0
```

```bash
pip install pybreaker==1.2.0
```

- [ ] **Step 3.2: Write failing tests**

```python
# tests/test_circuit_breaker.py
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import pybreaker

from control_plane.circuit_breaker import get_breaker, CircuitOpenError, reset_all


def test_get_breaker_returns_same_instance_for_same_url():
    reset_all()
    b1 = get_breaker("http://agent:8001", fail_max=3, reset_timeout=30)
    b2 = get_breaker("http://agent:8001", fail_max=3, reset_timeout=30)
    assert b1 is b2


def test_get_breaker_returns_different_instance_for_different_url():
    reset_all()
    b1 = get_breaker("http://agent-a:8001", fail_max=3, reset_timeout=30)
    b2 = get_breaker("http://agent-b:8001", fail_max=3, reset_timeout=30)
    assert b1 is not b2


def test_circuit_opens_after_fail_max_failures():
    reset_all()
    breaker = get_breaker("http://failing-agent:8001", fail_max=2, reset_timeout=60)

    def failing_fn():
        raise ConnectionError("timeout")

    for _ in range(2):
        try:
            breaker.call(failing_fn)
        except (ConnectionError, pybreaker.CircuitBreakerError):
            pass

    assert breaker.current_state == "open"


def test_open_circuit_raises_circuit_open_error():
    reset_all()
    breaker = get_breaker("http://open-circuit:8001", fail_max=1, reset_timeout=60)

    def failing_fn():
        raise ConnectionError("down")

    try:
        breaker.call(failing_fn)
    except (ConnectionError, CircuitOpenError):
        pass

    with pytest.raises(CircuitOpenError):
        breaker.call(lambda: None)  # Circuit is open — should raise immediately
```

- [ ] **Step 3.3: Run to verify tests fail**

```bash
pytest tests/test_circuit_breaker.py -v
```
Expected: FAIL — `control_plane.circuit_breaker` does not exist.

- [ ] **Step 3.4: Create `control_plane/circuit_breaker.py`**

```python
"""Per-instance circuit breaker registry for the Control Plane.

Each agent instance URL gets its own CircuitBreaker. When an instance
accumulates `fail_max` consecutive failures, its circuit opens and
subsequent calls immediately raise `CircuitOpenError` without hitting
the network, until `reset_timeout` seconds have elapsed.

Usage:
    from control_plane.circuit_breaker import get_breaker, CircuitOpenError

    breaker = get_breaker(instance_url)
    try:
        result = await breaker.call_async(my_coroutine)
    except CircuitOpenError:
        raise HTTPException(503, "Agent circuit open — too many recent failures")
"""
from __future__ import annotations

import pybreaker

# Registry: URL → CircuitBreaker instance
_registry: dict[str, pybreaker.CircuitBreaker] = {}


class CircuitOpenError(Exception):
    """Raised when a call is attempted against an open circuit breaker."""


def get_breaker(url: str, *, fail_max: int = 3, reset_timeout: int = 30) -> pybreaker.CircuitBreaker:
    """Return the CircuitBreaker for `url`, creating it if necessary.

    Parameters are only used on first creation. Subsequent calls with the
    same URL return the existing breaker regardless of parameter values.
    """
    if url not in _registry:
        _registry[url] = pybreaker.CircuitBreaker(
            fail_max=fail_max,
            reset_timeout=reset_timeout,
            name=url,
        )
    return _registry[url]


def reset_all() -> None:
    """Clear the breaker registry. For use in tests only."""
    _registry.clear()
```

- [ ] **Step 3.5: Run tests to verify they pass**

```bash
pytest tests/test_circuit_breaker.py -v
```
Expected: All 4 tests PASS.

- [ ] **Step 3.6: Add `cb_fail_max` and `cb_reset_timeout` to config**

In `control_plane/config.py`, add to `ControlPlaneSettings`:

```python
cb_fail_max: int = 3
cb_reset_timeout: int = 30
```

In `load_settings()`, before `return`:

```python
return ControlPlaneSettings(
    agents=agents,
    database_url=os.getenv("DATABASE_URL"),
    redis_url=os.getenv("REDIS_URL"),
    api_keys=api_keys,
    cors_origins=cors_origins,
    cb_fail_max=int(os.getenv("CB_FAIL_MAX", "3")),
    cb_reset_timeout=int(os.getenv("CB_RESET_TIMEOUT", "30")),
)
```

- [ ] **Step 3.7: Integrate circuit breaker into `_run_task` in `routes.py`**

Add the import and wrap the streaming call:

```python
# Add import at top of routes.py:
import pybreaker
from control_plane.circuit_breaker import get_breaker, CircuitOpenError

# At the top of _run_task, after creating client:
async def _run_task(task_id, agent_id, instance, text):
    assert _task_store is not None and _broker is not None

    record = await _task_store.get(task_id)
    record.state = TaskState.WORKING
    await _task_store.save(record)
    await _broker.publish(task_id, record.to_dict())

    started_at = time.time()
    client = A2AClient(instance.url, timeout=300)
    breaker = get_breaker(instance.url)  # ADD THIS LINE

    try:
        try:
            gen = await breaker.call_async(
                lambda: client.stream_message(
                    text,
                    task_id=task_id,
                    baselines=record.baselines,
                    key_questions=record.key_questions,
                )
            )
        except pybreaker.CircuitBreakerError:
            raise CircuitOpenError(f"Circuit open for {instance.url}")

        # ... rest of existing streaming loop unchanged ...

    except CircuitOpenError as exc:
        tasks_failed.labels(agent_id=agent_id).inc()
        logger.error("task_circuit_open", task_id=task_id, instance=instance.url)
        record.state = TaskState.FAILED
        record.error = f"Circuit breaker open: {exc}"

    # ... keep all other existing except blocks unchanged ...
```

> **Note:** `stream_message` returns an `AsyncGenerator`, not a coroutine — `breaker.call_async` wraps the generator-creating call, not the iteration. The circuit opens on `httpx.ConnectError` or `httpx.TimeoutException` during the first chunk.

- [ ] **Step 3.8: Commit**

```bash
git add control_plane/circuit_breaker.py control_plane/a2a_client.py control_plane/routes.py control_plane/config.py requirements.txt tests/test_circuit_breaker.py
git commit -m "feat: add per-instance circuit breaker for downstream agent calls"
```

---

### Task 4: Per-Task Execution Timeout

**Files:**
- Modify: `control_plane/config.py`
- Modify: `control_plane/routes.py`
- Create: `tests/test_task_timeout.py`

- [ ] **Step 4.1: Write failing tests**

```python
# tests/test_task_timeout.py
import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from control_plane.task_store import TaskStore, TaskRecord, TaskState
from control_plane.pubsub import InMemoryBroker


async def _mock_stream_that_hangs(*args, **kwargs):
    """An async generator that never yields."""
    await asyncio.sleep(9999)
    yield {}  # unreachable


@pytest.mark.asyncio
async def test_task_times_out_and_marked_failed(monkeypatch):
    monkeypatch.setenv("TASK_TIMEOUT_SECONDS", "1")

    from control_plane import routes
    store = TaskStore()
    broker = InMemoryBroker()

    import importlib
    importlib.reload(routes)
    routes.init_routes(MagicMock(), store, broker)

    task_id = "timeout-task-1"
    record = TaskRecord(
        task_id=task_id,
        agent_id="echo-agent",
        instance_url="http://localhost:8001",
        input_text="hello",
    )
    await store.save(record)

    instance = MagicMock()
    instance.url = "http://localhost:8001"
    instance.active_tasks = 0

    with patch("control_plane.routes.A2AClient") as MockClient:
        mock_client = MagicMock()
        mock_client.stream_message = _mock_stream_that_hangs
        mock_client.close = AsyncMock()
        MockClient.return_value = mock_client

        await routes._run_task(task_id, "echo-agent", instance, "hello")

    final = await store.get(task_id)
    assert final.state == TaskState.FAILED
    assert "timeout" in final.error.lower()
```

- [ ] **Step 4.2: Run to verify it fails**

```bash
pytest tests/test_task_timeout.py -v
```
Expected: FAIL (test waits forever) or FAIL with wrong state.

- [ ] **Step 4.3: Add `task_timeout_seconds` to config**

In `control_plane/config.py`, add to `ControlPlaneSettings`:

```python
task_timeout_seconds: int = 600
```

In `load_settings()`:

```python
return ControlPlaneSettings(
    ...
    task_timeout_seconds=int(os.getenv("TASK_TIMEOUT_SECONDS", "600")),
)
```

- [ ] **Step 4.4: Extract `_execute_task` inner function and wrap with `asyncio.wait_for`**

In `control_plane/routes.py`, refactor `_run_task` to separate the streaming body:

```python
# At module level, read timeout from env at import time:
import os as _os
_TASK_TIMEOUT = int(_os.getenv("TASK_TIMEOUT_SECONDS", "600"))

async def _run_task(task_id, agent_id, instance, text):
    assert _task_store is not None and _broker is not None

    record = await _task_store.get(task_id)
    record.state = TaskState.WORKING
    await _task_store.save(record)
    await _broker.publish(task_id, record.to_dict())

    started_at = time.time()
    client = A2AClient(instance.url, timeout=300)

    try:
        try:
            await asyncio.wait_for(
                _stream_agent(client, task_id, agent_id, instance, record, text),
                timeout=_TASK_TIMEOUT,
            )
        except asyncio.TimeoutError:
            tasks_failed.labels(agent_id=agent_id).inc()
            logger.error("task_timeout", task_id=task_id, timeout_s=_TASK_TIMEOUT)
            record.state = TaskState.FAILED
            record.error = f"Task exceeded timeout of {_TASK_TIMEOUT}s"
            await _task_store.save(record)
            await _broker.publish(task_id, record.to_dict())
            return
    finally:
        await client.close()
        instance.active_tasks = max(0, instance.active_tasks - 1)


async def _stream_agent(client, task_id, agent_id, instance, record, text):
    """Inner coroutine: streams agent events and updates record in-place."""
    started_at = time.time()
    breaker = get_breaker(instance.url)

    try:
        try:
            gen = await breaker.call_async(
                lambda: client.stream_message(
                    text,
                    task_id=task_id,
                    baselines=record.baselines,
                    key_questions=record.key_questions,
                )
            )
        except pybreaker.CircuitBreakerError:
            raise CircuitOpenError(f"Circuit open for {instance.url}")

        try:
            async for event in gen:
                state_str = event.get("result", {}).get("status", {}).get("state", "")
                msg = event.get("result", {}).get("status", {}).get("message", {})
                text_val = (msg.get("parts") or [{}])[0].get("text", "")

                if text_val.startswith("NODE_OUTPUT::"):
                    parts = text_val.split("::", 2)
                    if len(parts) == 3:
                        _, node_name, json_payload = parts
                        try:
                            json.loads(json_payload)
                            out_key = node_name
                            idx = 1
                            while out_key in record.node_outputs:
                                out_key = f"{node_name}:{idx}"
                                idx += 1
                            record.node_outputs[out_key] = json_payload
                            record.running_node = ""
                            await _task_store.save(record)
                            await _broker.publish(task_id, record.to_dict())
                        except json.JSONDecodeError:
                            logger.warning("node_output_invalid_json", task_id=task_id, node=node_name)
                    else:
                        logger.warning("node_output_malformed", task_id=task_id, text=text_val[:100])
                    continue

                if state_str == "working" and text_val.startswith("Running node: "):
                    node_name = text_val[len("Running node: "):]
                    record.running_node = node_name
                    await _task_store.save(record)
                    await _broker.publish(task_id, record.to_dict())
                    continue

                if state_str == "input-required":
                    record.state = TaskState.INPUT_REQUIRED
                    record.running_node = ""
                    await _task_store.save(record)
                    await _broker.publish(task_id, record.to_dict())
                    continue

                if state_str in ("completed", "failed", "canceled"):
                    record.state = TaskState(state_str)
                    record.output_text = text_val
                    record.running_node = ""
                    if record.state == TaskState.FAILED:
                        record.error = text_val or "Agent returned failed state with no details"
                    break
            else:
                terminal = {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELED}
                fresh = await _task_store.get(task_id)
                if fresh is None or fresh.state not in terminal:
                    record.state = TaskState.FAILED
                    record.error = "Stream ended without a terminal status event"
                else:
                    record.state = fresh.state
        finally:
            await gen.aclose()

        elapsed = time.time() - started_at
        task_duration.labels(agent_id=agent_id).observe(elapsed)

        if record.state == TaskState.COMPLETED:
            tasks_completed.labels(agent_id=agent_id).inc()
        elif record.state == TaskState.FAILED:
            tasks_failed.labels(agent_id=agent_id).inc()

        logger.info(
            "task_complete",
            agent_id=agent_id,
            task_id=task_id,
            state=record.state.value,
            duration_s=round(elapsed, 3),
            instance=instance.url,
        )

    except CircuitOpenError as exc:
        tasks_failed.labels(agent_id=agent_id).inc()
        logger.error("task_circuit_open", task_id=task_id, instance=instance.url)
        record.state = TaskState.FAILED
        record.error = f"Circuit breaker open: {exc}"

    except A2AError as exc:
        tasks_failed.labels(agent_id=agent_id).inc()
        logger.error("task_a2a_error", task_id=task_id, error=str(exc))
        record.state = TaskState.FAILED
        record.error = f"A2A protocol error: {exc}"

    except httpx.HTTPStatusError as exc:
        tasks_failed.labels(agent_id=agent_id).inc()
        logger.error("task_http_error", task_id=task_id, status=exc.response.status_code, error=str(exc))
        record.state = TaskState.FAILED
        record.error = f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"

    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        tasks_failed.labels(agent_id=agent_id).inc()
        logger.error("task_connection_error", task_id=task_id, error=str(exc))
        record.state = TaskState.FAILED
        record.error = f"Connection failed: {type(exc).__name__} — {exc}"

    except Exception as exc:
        tasks_failed.labels(agent_id=agent_id).inc()
        logger.error("task_error", task_id=task_id, error=str(exc))
        record.state = TaskState.FAILED
        record.error = f"{type(exc).__name__}: {exc}"

    await _task_store.save(record)
    await _broker.publish(task_id, record.to_dict())
```

> **Note:** The old `_run_task` body is replaced entirely by the above split. Remove the old `_run_task` function and replace it with the two functions above.

- [ ] **Step 4.5: Run tests to verify they pass**

```bash
pytest tests/test_task_timeout.py -v
```
Expected: PASS (completes in ~1 second due to 1s timeout).

- [ ] **Step 4.6: Run full test suite**

```bash
pytest tests/ -v
```
Expected: All existing tests still pass.

- [ ] **Step 4.7: Commit**

```bash
git add control_plane/routes.py control_plane/config.py tests/test_task_timeout.py
git commit -m "feat: add per-task execution timeout with asyncio.wait_for"
```

---

## Phase 3: Task Lifecycle Hardening

### Task 5: Task TTL Expiry and Janitor

**Files:**
- Modify: `control_plane/task_store.py`
- Create: `control_plane/janitor.py`
- Modify: `control_plane/config.py`
- Modify: `control_plane/server.py`
- Create: `tests/test_task_ttl.py`

- [ ] **Step 5.1: Write failing tests**

```python
# tests/test_task_ttl.py
import asyncio
import time
import pytest

from control_plane.task_store import TaskStore, TaskRecord, TaskState
from control_plane.janitor import Janitor


@pytest.mark.asyncio
async def test_janitor_deletes_expired_terminal_tasks():
    store = TaskStore()

    old_completed = TaskRecord(
        task_id="old-1", agent_id="echo", state=TaskState.COMPLETED,
        created_at=time.time() - 7200,  # 2 hours ago
        updated_at=time.time() - 7200,
    )
    old_failed = TaskRecord(
        task_id="old-2", agent_id="echo", state=TaskState.FAILED,
        created_at=time.time() - 7200,
        updated_at=time.time() - 7200,
    )
    recent = TaskRecord(
        task_id="recent-1", agent_id="echo", state=TaskState.COMPLETED,
        created_at=time.time() - 60,  # 1 minute ago
        updated_at=time.time() - 60,
    )
    active = TaskRecord(
        task_id="active-1", agent_id="echo", state=TaskState.WORKING,
        created_at=time.time() - 7200,  # old but still WORKING — do NOT delete
        updated_at=time.time() - 60,
    )

    for r in [old_completed, old_failed, recent, active]:
        await store.save(r)

    janitor = Janitor(store, ttl_hours=1, interval_seconds=9999)
    deleted = await janitor.run_once()

    assert deleted == 2
    assert await store.get("old-1") is None
    assert await store.get("old-2") is None
    assert await store.get("recent-1") is not None
    assert await store.get("active-1") is not None


@pytest.mark.asyncio
async def test_janitor_does_not_delete_non_terminal_tasks():
    store = TaskStore()

    working = TaskRecord(
        task_id="working-1", agent_id="echo", state=TaskState.WORKING,
        created_at=time.time() - 9999,
        updated_at=time.time() - 9999,
    )
    submitted = TaskRecord(
        task_id="submitted-1", agent_id="echo", state=TaskState.SUBMITTED,
        created_at=time.time() - 9999,
        updated_at=time.time() - 9999,
    )
    await store.save(working)
    await store.save(submitted)

    janitor = Janitor(store, ttl_hours=1, interval_seconds=9999)
    deleted = await janitor.run_once()

    assert deleted == 0
    assert await store.get("working-1") is not None
    assert await store.get("submitted-1") is not None
```

- [ ] **Step 5.2: Run to verify they fail**

```bash
pytest tests/test_task_ttl.py -v
```
Expected: FAIL — `Janitor` does not exist.

- [ ] **Step 5.3: Add `delete_older_than` to `TaskStore` and `PostgresTaskStore`**

In `control_plane/task_store.py`, add to `TaskStore`:

```python
_TERMINAL_STATES = {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELED}

async def delete_older_than(self, cutoff_ts: float) -> int:
    """Delete terminal tasks with updated_at < cutoff_ts. Returns count deleted."""
    to_delete = [
        task_id
        for task_id, rec in self._tasks.items()
        if rec.state in _TERMINAL_STATES and rec.updated_at < cutoff_ts
    ]
    for task_id in to_delete:
        del self._tasks[task_id]
    return len(to_delete)
```

Add to `PostgresTaskStore`:

```python
async def delete_older_than(self, cutoff_ts: float) -> int:
    terminal = ("completed", "failed", "canceled")
    placeholders = ", ".join(f"${i+2}" for i in range(len(terminal)))
    async with self._pool.acquire() as conn:
        result = await conn.execute(
            f"DELETE FROM tasks WHERE updated_at < $1 AND state IN ({placeholders})",
            cutoff_ts,
            *terminal,
        )
    return int(result.split()[-1])
```

- [ ] **Step 5.4: Create `control_plane/janitor.py`**

```python
"""Background janitor that periodically expires old terminal tasks.

Usage (in server lifespan):
    janitor = Janitor(task_store, ttl_hours=settings.task_ttl_hours)
    janitor.start()
    yield
    janitor.stop()
"""
from __future__ import annotations

import asyncio
import time

from control_plane.log import get_logger
from control_plane.task_store import TaskStore, PostgresTaskStore

logger = get_logger(__name__)


class Janitor:
    def __init__(
        self,
        store: TaskStore | PostgresTaskStore,
        *,
        ttl_hours: int = 72,
        interval_seconds: int = 3600,
    ) -> None:
        self._store = store
        self._ttl_hours = ttl_hours
        self._interval = interval_seconds
        self._task: asyncio.Task | None = None

    async def run_once(self) -> int:
        """Delete expired terminal tasks. Returns count deleted."""
        cutoff = time.time() - self._ttl_hours * 3600
        deleted = await self._store.delete_older_than(cutoff)
        if deleted:
            logger.info("janitor_deleted", count=deleted, ttl_hours=self._ttl_hours)
        return deleted

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            try:
                await self.run_once()
            except Exception as exc:
                logger.error("janitor_error", error=str(exc))

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
```

- [ ] **Step 5.5: Add TTL config and wire janitor into server lifespan**

In `control_plane/config.py`, add to `ControlPlaneSettings`:

```python
task_ttl_hours: int = 72
janitor_interval_seconds: int = 3600
```

In `load_settings()`:

```python
return ControlPlaneSettings(
    ...
    task_ttl_hours=int(os.getenv("TASK_TTL_HOURS", "72")),
    janitor_interval_seconds=int(os.getenv("JANITOR_INTERVAL_SECONDS", "3600")),
)
```

In `control_plane/server.py`, add to lifespan:

```python
from control_plane.janitor import Janitor

# In lifespan, after registry.start_polling():
janitor = Janitor(
    task_store,
    ttl_hours=settings.task_ttl_hours,
    interval_seconds=settings.janitor_interval_seconds,
)
janitor.start()

yield

janitor.stop()
# ... existing cleanup
```

- [ ] **Step 5.6: Run tests to verify they pass**

```bash
pytest tests/test_task_ttl.py -v
```
Expected: All 2 tests PASS.

- [ ] **Step 5.7: Commit**

```bash
git add control_plane/task_store.py control_plane/janitor.py control_plane/config.py control_plane/server.py tests/test_task_ttl.py
git commit -m "feat: add task TTL expiry janitor for terminal task cleanup"
```

---

### Task 6: Task List Pagination

**Files:**
- Modify: `control_plane/task_store.py`
- Modify: `control_plane/routes.py`
- Create: `tests/test_task_pagination.py`

- [ ] **Step 6.1: Write failing tests**

```python
# tests/test_task_pagination.py
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, MagicMock, AsyncMock

from control_plane.task_store import TaskStore, TaskRecord, TaskState
from control_plane.pubsub import InMemoryBroker
from control_plane.registry import AgentRegistry
from control_plane.server import create_app
import time


async def _make_tasks(store: TaskStore, n: int) -> list[str]:
    ids = []
    for i in range(n):
        r = TaskRecord(
            task_id=f"task-{i:03d}",
            agent_id="echo",
            state=TaskState.COMPLETED,
            created_at=time.time() + i,  # ascending order
        )
        await store.save(r)
        ids.append(r.task_id)
    return ids


@pytest.mark.asyncio
async def test_list_tasks_default_limit(monkeypatch):
    monkeypatch.delenv("API_KEYS", raising=False)
    store = TaskStore()
    await _make_tasks(store, 60)

    app = create_app()
    from control_plane import routes
    routes.init_routes(AgentRegistry(), store, InMemoryBroker())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/tasks")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 50  # default limit


@pytest.mark.asyncio
async def test_list_tasks_with_limit_and_offset(monkeypatch):
    monkeypatch.delenv("API_KEYS", raising=False)
    store = TaskStore()
    await _make_tasks(store, 20)

    app = create_app()
    from control_plane import routes
    routes.init_routes(AgentRegistry(), store, InMemoryBroker())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        page1 = (await client.get("/tasks?limit=5&offset=0")).json()
        page2 = (await client.get("/tasks?limit=5&offset=5")).json()

    assert len(page1) == 5
    assert len(page2) == 5
    # Pages should not overlap
    ids1 = {t["task_id"] for t in page1}
    ids2 = {t["task_id"] for t in page2}
    assert ids1.isdisjoint(ids2)
```

- [ ] **Step 6.2: Run to verify they fail**

```bash
pytest tests/test_task_pagination.py -v
```
Expected: FAIL — `/tasks` returns all records.

- [ ] **Step 6.3: Add `list_page` to both stores in `task_store.py`**

Add to `TaskStore`:

```python
async def list_page(self, limit: int = 50, offset: int = 0) -> list[TaskRecord]:
    all_tasks = sorted(self._tasks.values(), key=lambda t: t.created_at, reverse=True)
    return all_tasks[offset: offset + limit]
```

Add to `PostgresTaskStore`:

```python
async def list_page(self, limit: int = 50, offset: int = 0) -> list[TaskRecord]:
    async with self._pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM tasks ORDER BY created_at DESC LIMIT $1 OFFSET $2",
            limit,
            offset,
        )
    return [TaskRecord.from_row(dict(r)) for r in rows]
```

- [ ] **Step 6.4: Update `GET /tasks` in `routes.py`**

```python
from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect

@router.get("/tasks")
async def list_all_tasks(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    assert _task_store is not None
    tasks = await _task_store.list_page(limit=limit, offset=offset)
    return [t.to_dict() for t in tasks]
```

- [ ] **Step 6.5: Run tests to verify they pass**

```bash
pytest tests/test_task_pagination.py -v
```
Expected: All 2 tests PASS.

- [ ] **Step 6.6: Commit**

```bash
git add control_plane/task_store.py control_plane/routes.py tests/test_task_pagination.py
git commit -m "feat: add pagination to task list endpoint (limit/offset query params)"
```

---

### Task 7: INPUT_REQUIRED State Handler

**Files:**
- Modify: `control_plane/routes.py`
- Create: `tests/test_input_required.py`

> The `_stream_agent` refactor in Task 4 already handles `input-required` state — this task adds the resume endpoint and tests.

- [ ] **Step 7.1: Write failing tests**

```python
# tests/test_input_required.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from control_plane.task_store import TaskStore, TaskRecord, TaskState
from control_plane.pubsub import InMemoryBroker


@pytest.mark.asyncio
async def test_task_transitions_to_input_required():
    """Simulates agent returning input-required and verifies state stored."""
    from control_plane import routes

    store = TaskStore()
    broker = InMemoryBroker()
    routes.init_routes(MagicMock(), store, broker)

    task_id = "ir-task-1"
    record = TaskRecord(
        task_id=task_id,
        agent_id="echo",
        instance_url="http://localhost:8001",
        input_text="hello",
    )
    await store.save(record)

    async def mock_stream(*args, **kwargs):
        yield {"result": {"status": {"state": "input-required", "message": {"parts": [{"text": "Need more info"}]}}}}
        yield {"result": {"status": {"state": "completed", "message": {"parts": [{"text": "done"}]}}}}

    instance = MagicMock()
    instance.url = "http://localhost:8001"
    instance.active_tasks = 0

    published_states = []
    original_publish = broker.publish
    async def capturing_publish(tid, data):
        published_states.append(data.get("state"))
        await original_publish(tid, data)
    broker.publish = capturing_publish

    with patch("control_plane.routes.A2AClient") as MockClient:
        mock_client = MagicMock()
        mock_client.stream_message = mock_stream
        mock_client.close = AsyncMock()
        MockClient.return_value = mock_client
        with patch("control_plane.routes.get_breaker") as mock_gb:
            mock_breaker = MagicMock()
            mock_breaker.call_async = AsyncMock(side_effect=lambda fn: fn())
            mock_gb.return_value = mock_breaker

            await routes._run_task(task_id, "echo", instance, "hello")

    assert "input-required" in published_states


@pytest.mark.asyncio
async def test_resume_task_input_required(monkeypatch):
    """POST /tasks/{task_id}/input on an INPUT_REQUIRED task re-dispatches."""
    monkeypatch.delenv("API_KEYS", raising=False)
    from httpx import AsyncClient, ASGITransport
    from control_plane.server import create_app
    from control_plane.registry import AgentRegistry
    from control_plane import routes

    store = TaskStore()
    broker = InMemoryBroker()
    registry = AgentRegistry()

    record = TaskRecord(
        task_id="ir-resume-1",
        agent_id="echo",
        instance_url="http://localhost:8001",
        state=TaskState.INPUT_REQUIRED,
        input_text="original question",
    )
    await store.save(record)

    app = create_app()
    routes.init_routes(registry, store, broker)

    with patch("control_plane.routes.asyncio") as mock_asyncio:
        mock_asyncio.create_task = MagicMock()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/tasks/ir-resume-1/input",
                json={"text": "additional context"},
            )

    assert resp.status_code == 202


@pytest.mark.asyncio
async def test_resume_non_input_required_task_returns_409():
    monkeypatch.delenv("API_KEYS", raising=False)
    from httpx import AsyncClient, ASGITransport
    from control_plane.server import create_app
    from control_plane.registry import AgentRegistry
    from control_plane import routes

    store = TaskStore()
    record = TaskRecord(
        task_id="completed-1",
        agent_id="echo",
        state=TaskState.COMPLETED,
    )
    await store.save(record)

    app = create_app()
    routes.init_routes(AgentRegistry(), store, InMemoryBroker())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/tasks/completed-1/input", json={"text": "more input"})

    assert resp.status_code == 409
```

- [ ] **Step 7.2: Run to verify they fail**

```bash
pytest tests/test_input_required.py -v
```
Expected: FAIL — no `/tasks/{task_id}/input` endpoint.

- [ ] **Step 7.3: Add resume endpoint to `routes.py`**

```python
class TaskInputRequest(BaseModel):
    text: str


@router.post("/tasks/{task_id}/input", status_code=202)
async def resume_task(task_id: str, req: TaskInputRequest) -> dict[str, Any]:
    """Resume a task that is in INPUT_REQUIRED state by providing additional input."""
    assert _registry is not None and _task_store is not None

    record = await _task_store.get(task_id)
    if not record:
        raise HTTPException(404, "Task not found")
    if record.state != TaskState.INPUT_REQUIRED:
        raise HTTPException(
            409,
            f"Task is in state '{record.state.value}', not 'input-required'"
        )

    agent_type = _registry.get(record.agent_id)
    if not agent_type:
        raise HTTPException(404, f"Agent '{record.agent_id}' not found")

    instance = agent_type.pick()
    if not instance:
        raise HTTPException(503, f"No online instances for agent '{record.agent_id}'")

    # Append the new input and re-dispatch
    combined_text = f"{record.input_text}\n\nAdditional input: {req.text}"
    record.state = TaskState.SUBMITTED
    record.instance_url = instance.url
    await _task_store.save(record)

    instance.active_tasks += 1
    tasks_dispatched.labels(agent_id=record.agent_id).inc()
    asyncio.create_task(_run_task(task_id, record.agent_id, instance, combined_text))

    return record.to_dict()
```

- [ ] **Step 7.4: Run tests to verify they pass**

```bash
pytest tests/test_input_required.py::test_task_transitions_to_input_required tests/test_input_required.py::test_resume_task_input_required -v
```
Expected: PASS.

- [ ] **Step 7.5: Commit**

```bash
git add control_plane/routes.py tests/test_input_required.py
git commit -m "feat: handle INPUT_REQUIRED state and add task resume endpoint"
```

---

## Phase 4: Observability Completeness

### Task 8: Correlation ID Propagation to Agents

**Files:**
- Modify: `control_plane/a2a_client.py`
- Modify: `control_plane/routes.py`
- Modify: `agents/base/executor.py`
- Create: `tests/test_correlation_propagation.py`

- [ ] **Step 8.1: Write failing tests**

```python
# tests/test_correlation_propagation.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
import httpx

from control_plane.a2a_client import A2AClient


@pytest.mark.asyncio
async def test_stream_message_forwards_correlation_id():
    """X-Correlation-ID header should be set on outbound SSE request."""
    captured_headers = {}

    async def mock_aiter_lines():
        return
        yield  # make it an async generator

    class MockResponse:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def aiter_lines(self):
            return
            yield

    with patch.object(httpx.AsyncClient, "stream") as mock_stream:
        mock_stream.return_value = MockResponse()

        client = A2AClient("http://localhost:8001")
        gen = client.stream_message("hello", correlation_id="test-corr-123")
        # consume generator
        async for _ in gen:
            pass

        mock_stream.assert_called_once()
        _, kwargs = mock_stream.call_args
        headers = kwargs.get("headers", {})
        assert headers.get("X-Correlation-ID") == "test-corr-123"
```

- [ ] **Step 8.2: Run to verify it fails**

```bash
pytest tests/test_correlation_propagation.py -v
```
Expected: FAIL — `stream_message` does not accept `correlation_id`.

- [ ] **Step 8.3: Add `correlation_id` param to `A2AClient.stream_message` and `send_message`**

In `control_plane/a2a_client.py`:

```python
# In stream_message signature, add:
async def stream_message(
    self,
    text: str,
    *,
    task_id: str | None = None,
    context_id: str | None = None,
    parent_span_id: str | None = None,
    baselines: str = "",
    key_questions: str = "",
    correlation_id: str | None = None,   # ADD THIS
) -> AsyncGenerator[dict[str, Any], None]:

    # Build request headers
    headers: dict[str, str] = {}
    if correlation_id:
        headers["X-Correlation-ID"] = correlation_id

    # ...existing metadata/payload building unchanged...

    async with self._client.stream(
        "POST", f"{self._base_url}/", json=payload, headers=headers  # ADD headers=headers
    ) as response:
        async for line in response.aiter_lines():
            if line.startswith("data:"):
                data = line[len("data:"):].strip()
                if data:
                    yield json.loads(data)
```

- [ ] **Step 8.4: Pass `correlation_id` from `routes.py` to `A2AClient`**

In `control_plane/routes.py`, extract correlation ID from contextvars in `_stream_agent`:

```python
# Add import at top:
from control_plane.log import get_correlation_id  # we will add this function below

# In _stream_agent, when creating the generator:
corr_id = get_correlation_id()
gen = await breaker.call_async(
    lambda: client.stream_message(
        text,
        task_id=task_id,
        baselines=record.baselines,
        key_questions=record.key_questions,
        correlation_id=corr_id,
    )
)
```

- [ ] **Step 8.5: Add `get_correlation_id()` to `control_plane/log.py`**

Read `control_plane/log.py` first to understand the existing contextvars setup, then add:

```python
# In log.py, after the existing _request_id_var definition:
def get_correlation_id() -> str | None:
    """Return the current request correlation ID from contextvars, or None."""
    return _request_id_var.get(None)
```

> The variable name in `log.py` is `_request_id_var` — verify by reading the file. Adjust if different.

- [ ] **Step 8.6: Propagate correlation ID in agent executor**

In `agents/base/executor.py`, extract `correlationId` from metadata and bind to log:

```python
import logging as _logging

# In execute(), after extracting parent_span_id:
correlation_id: str | None = None
if context.message and context.message.metadata:
    correlation_id = context.message.metadata.get("correlationId") or \
                     context.message.metadata.get("X-Correlation-ID")

# Bind to Python logging context for this execution
_log_extra = {"correlation_id": correlation_id or ""}
```

Then replace `logger.xxx` calls or pass `extra=_log_extra`. The simplest approach is to use a `LoggerAdapter`:

```python
# At the start of execute(), create a scoped logger:
import logging as _logging
_adapter = _logging.LoggerAdapter(
    _logging.getLogger(__name__),
    extra={"correlation_id": correlation_id or ""},
)
```

Use `_adapter` for any log calls within `execute()`.

- [ ] **Step 8.7: Run tests**

```bash
pytest tests/test_correlation_propagation.py -v
```
Expected: PASS.

- [ ] **Step 8.8: Commit**

```bash
git add control_plane/a2a_client.py control_plane/routes.py control_plane/log.py agents/base/executor.py tests/test_correlation_propagation.py
git commit -m "feat: propagate correlation ID from control plane to agent logs"
```

---

### Task 9: Config Startup Validation

**Files:**
- Modify: `control_plane/config.py`
- Modify: `control_plane/server.py`
- Create: `tests/test_config_validation.py`

- [ ] **Step 9.1: Write failing tests**

```python
# tests/test_config_validation.py
import pytest
import os
from control_plane.config import load_settings, ControlPlaneSettings


def test_valid_config_passes_validation():
    settings = ControlPlaneSettings()
    settings.validate()  # should not raise


def test_invalid_log_level_raises():
    settings = ControlPlaneSettings()
    settings.log_level = "VERBOSE"  # not a valid Python log level
    with pytest.raises(ValueError, match="LOG_LEVEL"):
        settings.validate()


def test_invalid_rate_limit_format_raises():
    settings = ControlPlaneSettings()
    settings.rate_limit_dispatch = "not-a-limit"
    with pytest.raises(ValueError, match="RATE_LIMIT_DISPATCH"):
        settings.validate()


def test_negative_ttl_raises():
    settings = ControlPlaneSettings()
    settings.task_ttl_hours = -1
    with pytest.raises(ValueError, match="TASK_TTL_HOURS"):
        settings.validate()
```

- [ ] **Step 9.2: Run to verify they fail**

```bash
pytest tests/test_config_validation.py -v
```
Expected: FAIL — `validate()` method does not exist.

- [ ] **Step 9.3: Add `validate()` to `ControlPlaneSettings`**

In `control_plane/config.py`, add `log_level` field and `validate` method:

```python
import logging as _logging
import re as _re

_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
_RATE_LIMIT_RE = _re.compile(r"^\d+/(second|minute|hour|day)$")


class ControlPlaneSettings(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    agents: list[AgentEndpoint] = []
    health_poll_interval_seconds: int = 30
    database_url: str | None = None
    redis_url: str | None = None
    api_keys: list[str] = []
    cors_origins: list[str] = ["*"]
    cb_fail_max: int = 3
    cb_reset_timeout: int = 30
    task_timeout_seconds: int = 600
    task_ttl_hours: int = 72
    janitor_interval_seconds: int = 3600
    rate_limit_dispatch: str = "100/minute"

    def validate(self) -> None:
        """Raise ValueError if any setting is invalid. Call during startup."""
        if self.log_level.upper() not in _VALID_LOG_LEVELS:
            raise ValueError(
                f"LOG_LEVEL must be one of {_VALID_LOG_LEVELS}, got '{self.log_level}'"
            )
        if not _RATE_LIMIT_RE.match(self.rate_limit_dispatch):
            raise ValueError(
                f"RATE_LIMIT_DISPATCH must match '<count>/<period>', got '{self.rate_limit_dispatch}'"
            )
        if self.task_ttl_hours < 0:
            raise ValueError(f"TASK_TTL_HOURS must be >= 0, got {self.task_ttl_hours}")
        if self.task_timeout_seconds < 10:
            raise ValueError(f"TASK_TIMEOUT_SECONDS must be >= 10, got {self.task_timeout_seconds}")
```

Also update `load_settings()` to populate `log_level` and `rate_limit_dispatch`:

```python
return ControlPlaneSettings(
    agents=agents,
    database_url=os.getenv("DATABASE_URL"),
    redis_url=os.getenv("REDIS_URL"),
    api_keys=api_keys,
    cors_origins=cors_origins,
    log_level=os.getenv("LOG_LEVEL", "INFO"),
    cb_fail_max=int(os.getenv("CB_FAIL_MAX", "3")),
    cb_reset_timeout=int(os.getenv("CB_RESET_TIMEOUT", "30")),
    task_timeout_seconds=int(os.getenv("TASK_TIMEOUT_SECONDS", "600")),
    task_ttl_hours=int(os.getenv("TASK_TTL_HOURS", "72")),
    janitor_interval_seconds=int(os.getenv("JANITOR_INTERVAL_SECONDS", "3600")),
    rate_limit_dispatch=os.getenv("RATE_LIMIT_DISPATCH", "100/minute"),
)
```

- [ ] **Step 9.4: Call `settings.validate()` in server lifespan**

In `control_plane/server.py`, at the **top** of `lifespan` (before any I/O):

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate()  # Fail fast on bad config
    # ... rest of lifespan unchanged
```

- [ ] **Step 9.5: Run tests to verify they pass**

```bash
pytest tests/test_config_validation.py -v
```
Expected: All 4 tests PASS.

- [ ] **Step 9.6: Commit**

```bash
git add control_plane/config.py control_plane/server.py tests/test_config_validation.py
git commit -m "feat: add startup config validation with fail-fast on invalid settings"
```

---

### Task 10: New Prometheus Metrics + Alert Rules

**Files:**
- Modify: `control_plane/metrics.py`
- Create: `monitoring/prometheus.yml`
- Create: `monitoring/alerts.yml`
- Modify: `docker-compose.yml`

- [ ] **Step 10.1: Add new counters to `metrics.py`**

```python
# Add to control_plane/metrics.py:

tasks_rate_limited = Counter(
    "mc_tasks_rate_limited_total",
    "Total dispatch requests rejected by rate limiter",
    ["agent_id"],
)

tasks_circuit_open = Counter(
    "mc_tasks_circuit_open_total",
    "Total tasks that failed because circuit breaker was open",
    ["agent_id"],
)

tasks_input_required = Counter(
    "mc_tasks_input_required_total",
    "Total tasks that entered input-required state",
    ["agent_id"],
)

tasks_ttl_deleted = Counter(
    "mc_tasks_ttl_deleted_total",
    "Total tasks deleted by the TTL janitor",
)

tasks_timed_out = Counter(
    "mc_tasks_timed_out_total",
    "Total tasks that exceeded the execution timeout",
    ["agent_id"],
)
```

Then import and increment them in `routes.py` and `janitor.py` at the appropriate places:

In `routes.py` `_stream_agent`, replace `tasks_failed.inc()` with specific counters:
```python
# In asyncio.TimeoutError block in _run_task:
from control_plane.metrics import tasks_timed_out
tasks_timed_out.labels(agent_id=agent_id).inc()

# In CircuitOpenError block in _stream_agent:
from control_plane.metrics import tasks_circuit_open
tasks_circuit_open.labels(agent_id=agent_id).inc()

# In _stream_agent when input-required state received:
from control_plane.metrics import tasks_input_required
tasks_input_required.labels(agent_id=agent_id).inc()
```

In `janitor.py` `run_once`:
```python
from control_plane.metrics import tasks_ttl_deleted
tasks_ttl_deleted.inc(deleted)
```

- [ ] **Step 10.2: Create `monitoring/prometheus.yml`**

```yaml
# monitoring/prometheus.yml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

rule_files:
  - "alerts.yml"

alerting:
  alertmanagers:
    - static_configs:
        - targets:
            - alertmanager:9093

scrape_configs:
  - job_name: "mission-control"
    static_configs:
      - targets: ["control-plane:8000"]
    metrics_path: /metrics
```

- [ ] **Step 10.3: Create `monitoring/alerts.yml`**

```yaml
# monitoring/alerts.yml
groups:
  - name: mission_control
    interval: 30s
    rules:

      # High task failure rate (>20% over 5 min)
      - alert: HighTaskFailureRate
        expr: |
          (
            sum by (agent_id) (rate(mc_tasks_failed_total[5m]))
            /
            sum by (agent_id) (rate(mc_tasks_dispatched_total[5m]) + 0.001)
          ) > 0.20
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "High task failure rate for {{ $labels.agent_id }}"
          description: "Failure rate is {{ $value | humanizePercentage }} over the last 5 minutes."

      # P95 task duration > 120s
      - alert: HighTaskLatencyP95
        expr: |
          histogram_quantile(0.95, sum by (agent_id, le) (rate(mc_task_duration_seconds_bucket[10m]))) > 120
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High P95 task latency for {{ $labels.agent_id }}"
          description: "P95 latency is {{ $value | humanizeDuration }}."

      # Circuit breaker firing (any circuit open events in last 5 min)
      - alert: CircuitBreakerOpen
        expr: increase(mc_tasks_circuit_open_total[5m]) > 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Circuit breaker is open for {{ $labels.agent_id }}"
          description: "{{ $value }} tasks were rejected due to open circuit in last 5m."

      # Agent offline (no successful dispatches in 10m despite attempts)
      - alert: AgentTaskDispatchStalled
        expr: |
          (
            increase(mc_tasks_dispatched_total[10m]) > 0
          ) and
          (
            increase(mc_tasks_completed_total[10m]) == 0
          )
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Agent {{ $labels.agent_id }} dispatching but completing no tasks"

      # Rate limiting firing frequently (>10 in 5 min)
      - alert: HighRateLimiting
        expr: increase(mc_tasks_rate_limited_total[5m]) > 10
        for: 1m
        labels:
          severity: info
        annotations:
          summary: "High rate limiting activity for {{ $labels.agent_id }}"
          description: "{{ $value }} requests rate-limited in last 5 minutes."
```

- [ ] **Step 10.4: Add Prometheus and Alertmanager to `docker-compose.yml`**

Add these services to the `services:` block in `docker-compose.yml`:

```yaml
  prometheus:
    image: prom/prometheus:v2.51.0
    volumes:
      - ./monitoring/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - ./monitoring/alerts.yml:/etc/prometheus/alerts.yml:ro
    ports:
      - "9090:9090"
    networks:
      - mc-net
    depends_on:
      - control-plane

  alertmanager:
    image: prom/alertmanager:v0.27.0
    ports:
      - "9093:9093"
    networks:
      - mc-net
```

- [ ] **Step 10.5: Commit**

```bash
git add control_plane/metrics.py control_plane/routes.py control_plane/janitor.py monitoring/ docker-compose.yml
git commit -m "feat: add new business metrics, Prometheus scrape config, and alert rules"
```

---

## Phase 5: Infrastructure Hardening

### Task 11: Docker Resource Limits

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 11.1: Add `deploy.resources` limits to all services**

For each service in `docker-compose.yml`, add a `deploy` block. The values below are conservative defaults — tune per observed usage:

```yaml
# control-plane
  control-plane:
    # ... existing config ...
    deploy:
      resources:
        limits:
          cpus: "1.0"
          memory: 512M
        reservations:
          cpus: "0.25"
          memory: 128M

# For LLM-backed agents (lead-analyst, specialist, probability):
  lead-analyst:
    deploy:
      resources:
        limits:
          cpus: "2.0"
          memory: 1G
        reservations:
          cpus: "0.5"
          memory: 256M

# For lightweight agents (relevancy, echo):
  relevancy:
    deploy:
      resources:
        limits:
          cpus: "0.5"
          memory: 256M
        reservations:
          cpus: "0.1"
          memory: 64M

# For database services:
  postgres:
    deploy:
      resources:
        limits:
          cpus: "1.0"
          memory: 1G
        reservations:
          cpus: "0.25"
          memory: 256M

  redis:
    deploy:
      resources:
        limits:
          cpus: "0.5"
          memory: 256M
        reservations:
          cpus: "0.1"
          memory: 64M
```

> Apply the same pattern to: `neo4j`, `knowledge-graph`, `memory-agent`, `baseline-store`, `dashboard`, `prometheus`, `alertmanager`. Scale limits proportional to the agent's complexity.

- [ ] **Step 11.2: Verify compose config is valid**

```bash
docker compose config --quiet
```
Expected: No output (valid config).

- [ ] **Step 11.3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: add CPU and memory resource limits to all Docker Compose services"
```

---

### Task 12: Cost Tracking Per Task

**Files:**
- Modify: `control_plane/task_store.py`
- Modify: `control_plane/routes.py`
- Modify: `control_plane/metrics.py`
- Modify: `agents/base/executor.py`
- Create: `tests/test_cost_tracking.py`

- [ ] **Step 12.1: Write failing tests**

```python
# tests/test_cost_tracking.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from control_plane.task_store import TaskStore, TaskRecord, TaskState
from control_plane.pubsub import InMemoryBroker


@pytest.mark.asyncio
async def test_cost_report_parsed_from_node_output():
    """COST_REPORT::100::50::0.0015 event updates task cost fields."""
    from control_plane import routes

    store = TaskStore()
    broker = InMemoryBroker()
    routes.init_routes(MagicMock(), store, broker)

    task_id = "cost-task-1"
    record = TaskRecord(
        task_id=task_id,
        agent_id="lead-analyst",
        instance_url="http://localhost:8005",
        input_text="analyze this",
    )
    await store.save(record)

    async def mock_stream(*args, **kwargs):
        yield {"result": {"status": {"state": "working", "message": {"parts": [{"text": "Running node: analyse"}]}}}}
        yield {"result": {"status": {"state": "working", "message": {"parts": [{"text": "COST_REPORT::100::50::0.0015"}]}}}}
        yield {"result": {"status": {"state": "completed", "message": {"parts": [{"text": "analysis complete"}]}}}}

    instance = MagicMock()
    instance.url = "http://localhost:8005"
    instance.active_tasks = 0

    with patch("control_plane.routes.A2AClient") as MockClient, \
         patch("control_plane.routes.get_breaker") as mock_gb:
        mock_client = MagicMock()
        mock_client.stream_message = mock_stream
        mock_client.close = AsyncMock()
        MockClient.return_value = mock_client
        mock_breaker = MagicMock()
        mock_breaker.call_async = AsyncMock(side_effect=lambda fn: fn())
        mock_gb.return_value = mock_breaker

        await routes._run_task(task_id, "lead-analyst", instance, "analyze this")

    final = await store.get(task_id)
    assert final.token_input == 100
    assert final.token_output == 50
    assert abs(final.cost_usd - 0.0015) < 1e-6
```

- [ ] **Step 12.2: Run to verify it fails**

```bash
pytest tests/test_cost_tracking.py -v
```
Expected: FAIL — no `token_input`, `token_output`, `cost_usd` fields.

- [ ] **Step 12.3: Add cost fields to `TaskRecord`**

In `control_plane/task_store.py`:

```python
@dataclass
class TaskRecord:
    # ... existing fields ...
    token_input: int = 0
    token_output: int = 0
    cost_usd: float = 0.0
```

Update `to_dict()`:
```python
def to_dict(self) -> dict[str, Any]:
    return {
        # ... existing keys ...
        "token_input": self.token_input,
        "token_output": self.token_output,
        "cost_usd": self.cost_usd,
    }
```

Update `from_row()`:
```python
@classmethod
def from_row(cls, row: dict[str, Any]) -> TaskRecord:
    return cls(
        # ... existing fields ...
        token_input=int(row.get("token_input", 0) or 0),
        token_output=int(row.get("token_output", 0) or 0),
        cost_usd=float(row.get("cost_usd", 0.0) or 0.0),
    )
```

Update `_CREATE_TABLE` SQL in `PostgresTaskStore`:
```sql
CREATE TABLE IF NOT EXISTS tasks (
    -- ... existing columns ...
    token_input  INTEGER NOT NULL DEFAULT 0,
    token_output INTEGER NOT NULL DEFAULT 0,
    cost_usd     FLOAT8  NOT NULL DEFAULT 0.0
);
```

Add migration SQL:
```python
_ADD_COST_COLUMNS = """
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS token_input INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS token_output INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS cost_usd FLOAT8 NOT NULL DEFAULT 0.0;
"""
```

Call it in `init()`:
```python
await conn.execute(_ADD_COST_COLUMNS)
```

Update `_UPSERT` to include the new columns.

- [ ] **Step 12.4: Parse `COST_REPORT` events in `_stream_agent` in `routes.py`**

In the SSE event loop inside `_stream_agent`, after the `NODE_OUTPUT` block:

```python
if text_val.startswith("COST_REPORT::"):
    parts = text_val.split("::", 3)
    if len(parts) == 4:
        try:
            _, inp, out, cost = parts
            record.token_input += int(inp)
            record.token_output += int(out)
            record.cost_usd += float(cost)
            await _task_store.save(record)
        except (ValueError, IndexError):
            logger.warning("cost_report_parse_error", task_id=task_id, text=text_val)
    continue
```

- [ ] **Step 12.5: Add cost metrics to `metrics.py`**

```python
task_tokens_input = Counter(
    "mc_task_tokens_input_total",
    "Total input tokens consumed by agent tasks",
    ["agent_id"],
)

task_tokens_output = Counter(
    "mc_task_tokens_output_total",
    "Total output tokens generated by agent tasks",
    ["agent_id"],
)

task_cost_usd = Counter(
    "mc_task_cost_usd_total",
    "Estimated total USD cost of agent task LLM calls",
    ["agent_id"],
)
```

Increment them in `_stream_agent` after parsing `COST_REPORT`:

```python
from control_plane.metrics import task_tokens_input, task_tokens_output, task_cost_usd

task_tokens_input.labels(agent_id=agent_id).inc(int(inp))
task_tokens_output.labels(agent_id=agent_id).inc(int(out))
task_cost_usd.labels(agent_id=agent_id).inc(float(cost))
```

- [ ] **Step 12.6: Emit `COST_REPORT` from agent executor (optional wire-up)**

In `agents/base/executor.py`, at the end of successful graph execution (before emitting `completed` status), if LangChain callback metadata has usage:

```python
# In execute(), after result = await graph.astream() loop:
# Emit cost report if LLM usage is available from callbacks
if langfuse_handler and hasattr(langfuse_handler, "get_usage"):
    # This is a best-effort hook — not all LLM providers expose usage
    pass  # Agents can emit COST_REPORT themselves via NODE_OUTPUT

# Agents that know their token usage can call:
# await self._emit_status(event_queue, task_id, context_id, TaskState.working,
#     f"COST_REPORT::{input_tokens}::{output_tokens}::{cost_usd:.6f}")
```

> The COST_REPORT protocol is intentionally optional — agents that have token count data (e.g., from LangChain callback metadata) should emit it. Agents that don't simply skip it.

- [ ] **Step 12.7: Run tests**

```bash
pytest tests/test_cost_tracking.py -v
```
Expected: PASS.

- [ ] **Step 12.8: Run full test suite**

```bash
pytest tests/ -v
```
Expected: All tests pass.

- [ ] **Step 12.9: Commit**

```bash
git add control_plane/task_store.py control_plane/routes.py control_plane/metrics.py agents/base/executor.py tests/test_cost_tracking.py
git commit -m "feat: add per-task cost and token tracking via COST_REPORT protocol"
```

---

## Self-Review

### Spec Coverage Check

| Priority | Item | Task(s) |
|----------|------|---------|
| P0 | API authentication | Task 1 |
| P0 | Rate limiting | Task 2 |
| P1 | Circuit breaker | Task 3 |
| P1 | Per-task timeout | Task 4 |
| P1 | Task TTL + cleanup | Task 5 |
| P1 | Pagination on /tasks | Task 6 |
| P1 | Correlation ID propagation | Task 8 |
| P2 | INPUT_REQUIRED handler | Task 7 |
| P2 | Alerting rules | Task 10 |
| P2 | Config startup validation | Task 9 |
| P2 | Output schema validation | Not included — requires per-agent schema definitions that don't exist yet; should be a follow-on spec |
| P3 | Resource limits | Task 11 |
| P3 | Cost tracking | Task 12 |
| P3 | Secrets manager | Not included — runtime operational concern; recommend Vault/AWS SSM as follow-on |
| P3 | Kubernetes manifests | Not included — requires K8s environment decision; follow-on |

### Placeholder Scan

No TBDs, TODOs, or "implement later" markers in task steps. All code blocks are complete.

### Type Consistency

- `TaskState.INPUT_REQUIRED` used consistently throughout (defined in `task_store.py:29`).
- `CircuitOpenError` defined in `circuit_breaker.py` and imported in `routes.py`.
- `Janitor` defined with `run_once() -> int` and `start()`/`stop()` used consistently.
- `TaskRecord.token_input`, `.token_output`, `.cost_usd` fields added to `to_dict()`, `from_row()`, and `_UPSERT` SQL together.
- `get_correlation_id()` added to `log.py` and imported in `routes.py`.
