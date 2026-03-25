# Extraction Agent

Extracts structured information from unstructured text (articles, news, reports) using an OpenAI-compatible LLM. Returns a comprehensive JSON object with entities, events, financials, topics, relationships, and metadata.

## Graph

```
parse_input → extract_using_llm
```

- **parse_input** — extracts the `text` field from the JSON input
- **extract_using_llm** — calls the LLM with a detailed extraction prompt and parses the structured JSON response (has retry policy: 3 attempts with exponential backoff)

## Running

```bash
OPENAI_API_KEY=sk-... python -m agents.extraction_agent.server
```

Default port: **8004**

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `OPENAI_API_KEY` | Yes | — | API key for the OpenAI-compatible endpoint |
| `OPENAI_BASE_URL` | No | OpenAI default | Custom base URL (e.g. OpenRouter, local model) |
| `OPENAI_MODEL` | No | `gpt-4o-mini` | Model to use for extraction |
| `EXTRACTION_AGENT_URL` | No | `http://localhost:8004` | This agent's externally-reachable URL (used for self-registration) |
| `CONTROL_PLANE_URL` | No | — | Control plane URL for self-registration/deregistration |

Falls back to the generic `AGENT_URL` env var if `EXTRACTION_AGENT_URL` is not set.

## Input

Single text field — the article or text to extract information from.

## Output

JSON object with structured data including:

- `title` — inferred headline
- `summary` — 2-3 sentence summary
- `entities` — persons, organizations, locations, products
- `temporal` — publication date and events with dates
- `financials` — monetary amounts with context
- `topics` and `categories` — classification tags
- `claims` — factual claims with attribution
- `relationships` — subject-predicate-object triples between entities
- `metadata` — language, word count, tone, confidence score
