# Tests

Integration test suite for the Mission Control Control Plane. Tests are fully hermetic — no running services are required. All HTTP calls to A2A agents are intercepted by `pytest-httpx`, and the FastAPI app is exercised via `TestClient`.

---

## Architecture

```
tests/
├── conftest.py              # Shared fixtures: registry, task store, wired app, TestClient
├── test_agents_api.py       # /agents endpoint tests
└── test_task_lifecycle.py   # Full task lifecycle: dispatch → fetch → cancel → history
```

### How it works

```
pytest
  │
  ├── conftest.py builds a wired FastAPI app
  │     ├── AgentRegistry (pre-populated with one fake online agent)
  │     ├── TaskStore (fresh in-memory store per test)
  │     └── router (routes.py) with metrics endpoint
  │
  ├── TestClient sends HTTP requests to the app (no network)
  │
  └── HTTPXMock intercepts all outbound httpx calls
        └── Returns crafted A2A JSON-RPC responses
```

No real Echo Agent or Control Plane process is started. Tests run in milliseconds.

---

## Test Coverage

### `test_agents_api.py`

| Test | What it checks |
|---|---|
| `test_list_agents_returns_registered_agent` | `GET /agents` returns the pre-registered fake agent |
| `test_get_agent_found` | `GET /agents/{id}` returns 200 for a known agent |
| `test_get_agent_not_found` | `GET /agents/{id}` returns 404 for an unknown agent |
| `test_agent_has_skills` | Agent response includes the expected skills array |

### `test_task_lifecycle.py`

| Test | What it checks |
|---|---|
| `test_dispatch_task_success` | `POST /agents/{id}/tasks` returns 200 with correct task state and output |
| `test_dispatch_task_agent_not_found` | Returns 404 when agent does not exist |
| `test_dispatch_task_agent_offline` | Returns 503 when agent is marked offline |
| `test_dispatch_task_agent_unreachable` | Returns 502 when the agent HTTP call fails |
| `test_get_task_after_dispatch` | `GET /agents/{id}/tasks/{taskId}` returns the stored task |
| `test_get_task_not_found` | Returns 404 for an unknown task ID |
| `test_cancel_task` | `DELETE /agents/{id}/tasks/{taskId}` marks task as `canceled` in the store |
| `test_cancel_task_not_found` | Returns 404 for an unknown task ID |
| `test_list_all_tasks` | `GET /tasks` includes all dispatched tasks |
| `test_metrics_endpoint` | `GET /metrics` returns 200 and includes `mc_tasks_dispatched_total` |

---

## Running the Tests

### Prerequisites

```bash
# From the repo root
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Run all tests

```bash
pytest
```

### Run with verbose output

```bash
pytest -v
```

### Run a single test file

```bash
pytest tests/test_task_lifecycle.py -v
```

### Run a single test

```bash
pytest tests/test_task_lifecycle.py::test_cancel_task -v
```

### Run with coverage

```bash
pip install pytest-cov
pytest --cov=control_plane --cov-report=term-missing
```

---

## Configuration

`pytest.ini` at the repo root sets:

```ini
[pytest]
asyncio_mode = auto   # all async tests run automatically
testpaths = tests
```

---

## Adding New Tests

1. Add a new file `tests/test_<feature>.py`.
2. Import fixtures from `conftest.py` — `client`, `registry`, `task_store` are available automatically.
3. Use `httpx_mock: HTTPXMock` as a parameter to intercept outbound A2A calls.

```python
def test_my_feature(client, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="http://echo-agent:8001/",
        method="POST",
        content=b'{"jsonrpc":"2.0","id":1,"result":{...}}',
        headers={"Content-Type": "application/json"},
    )
    resp = client.post("/agents/echo-agent/tasks", json={"text": "test"})
    assert resp.status_code == 200
```
