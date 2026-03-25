# Echo Agent

A proof-of-concept agent that echoes messages back in uppercase. Demonstrates LangGraph + A2A integration with cancellation support and optional downstream agent forwarding.

## Graph

```
receive → process → forward_downstream → respond
```

- **receive** — reads and validates input
- **process** — transforms the message to uppercase
- **forward_downstream** — optionally forwards output to a downstream agent via A2A (if `DOWNSTREAM_AGENT_URL` is set)
- **respond** — formats the final output

## Running

```bash
python -m agents.echo.server
```

Default port: **8001**

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `ECHO_AGENT_URL` | No | `http://localhost:8001` | This agent's externally-reachable URL (used for self-registration) |
| `CONTROL_PLANE_URL` | No | — | Control plane URL for self-registration/deregistration |
| `DOWNSTREAM_AGENT_URL` | No | — | URL of a downstream A2A agent to forward output to (e.g. the summarizer) |

Falls back to the generic `AGENT_URL` env var if `ECHO_AGENT_URL` is not set.

## Input

Single text field — the message to echo.

## Output

The input message in uppercase, prefixed with `ECHO:`. If a downstream agent is configured, the downstream agent's response is returned instead.
