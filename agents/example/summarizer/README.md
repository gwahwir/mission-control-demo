# Summarizer Agent

Receives text and uses an OpenAI-compatible LLM to produce a concise 2-3 sentence summary. Designed to be called directly or by upstream agents that forward their output here via A2A.

## Graph

```
summarize → respond
```

- **summarize** — sends the input text to the LLM with a summarization prompt
- **respond** — formats the summary as the final output

## Running

```bash
OPENAI_API_KEY=sk-... python -m agents.summarizer.server
```

Default port: **8002**

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `OPENAI_API_KEY` | Yes | — | API key for the OpenAI-compatible endpoint |
| `OPENAI_BASE_URL` | No | OpenAI default | Custom base URL (e.g. OpenRouter, local model) |
| `OPENAI_MODEL` | No | `gpt-4o-mini` | Model to use for summarization |
| `SUMMARIZER_AGENT_URL` | No | `http://localhost:8002` | This agent's externally-reachable URL (used for self-registration) |
| `CONTROL_PLANE_URL` | No | — | Control plane URL for self-registration/deregistration |

Falls back to the generic `AGENT_URL` env var if `SUMMARIZER_AGENT_URL` is not set.

## Input

Single text field — the text to summarize.

## Output

A concise 2-3 sentence summary as plain text.
