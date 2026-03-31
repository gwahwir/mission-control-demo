# Synthetic Baseline Seeder — Design

**Date:** 2026-03-31
**File:** `scripts_for_testing/generate_synthetic_baselines.py`

## Purpose

A standalone script that takes a seed paragraph and uses an OpenAI LLM to generate
fictitious but coherent baseline data, then writes it into the baseline store. Used
to populate the store with realistic-looking test data for development and demo purposes.

## CLI Interface

```
python scripts_for_testing/generate_synthetic_baselines.py \
  --seed "Iran nuclear negotiations are..." \
  --topics 4 \
  --versions-per-topic 3 \
  --baseline-url http://localhost:8010 \
  --model gpt-4o-mini \
  --dry-run
```

| Arg | Default | Description |
|---|---|---|
| `--seed TEXT` | required | Seed paragraph; determines domain and topic area |
| `--topics N` | `4` | Number of leaf topics to generate |
| `--versions-per-topic N` | `3` | Versions (and deltas) per topic |
| `--baseline-url URL` | `http://localhost:8010` | Baseline store base URL |
| `--model MODEL` | `gpt-4o-mini` | OpenAI model |
| `--dry-run` | false | Print plan, skip all HTTP writes |

`--seed` may also be piped via stdin when omitted.

## Architecture

### Single LLM Call

One OpenAI call (with `response_format={"type": "json_object"}`) receives the seed
and is prompted to produce a complete JSON plan. No multi-step pipeline — the full
hierarchy and all versions are generated in one shot.

### LLM Output Schema

```json
{
  "topics": [
    {
      "topic_path": "geo.middle_east",
      "display_name": "Middle East",
      "versions": [
        {
          "narrative": "Plain-text present-tense narrative...",
          "citations": [
            {
              "article_id": "art-001",
              "title": "Headline",
              "url": "https://example.com/article",
              "source": "Reuters",
              "published_at": "2026-01-15T10:00:00Z",
              "excerpt": "Short verbatim excerpt..."
            }
          ],
          "delta_summary": "Initial baseline established.",
          "claims_added": ["Claim A", "Claim B"],
          "claims_superseded": []
        }
      ]
    },
    {
      "topic_path": "geo.middle_east.iran",
      "display_name": "Iran",
      "versions": [ ... ]
    }
  ]
}
```

- Topics array is **ordered parents-before-children** (the LLM is instructed to do this).
- Each topic gets exactly `--versions-per-topic` version entries.
- Each version entry includes the delta fields inline; the script derives
  `from_version`/`to_version` from array position.
- Parent topics are included in the hierarchy and receive versions too (enables rollup).

### Write Sequence

For each topic, in order:

1. `POST /topics` — register the topic path
2. For each version entry (index 0 … N−1):
   a. `POST /baselines/{topic_path}/versions` → capture returned `version_number`
   b. `POST /baselines/{topic_path}/deltas` with `from_version=null` (first) or previous `version_number`

All HTTP calls use `async httpx.AsyncClient`.

### Error Handling

- **409 on `POST /topics`** — topic already exists; skip and continue.
- **Any other non-2xx** — print error details and abort.
- **OpenAI errors** — propagate with a clear message; no retries.

## Data Flow

```
seed paragraph
      │
      ▼
 OpenAI call ──► JSON plan (topics + versions + deltas)
      │
      ▼
 print plan summary (always)
      │
   dry-run? ──yes──► exit 0
      │ no
      ▼
 for each topic (parents first):
   POST /topics
   for each version:
     POST /versions → version_number
     POST /deltas
      │
      ▼
 print summary: N topics, M versions written
```

## Dependencies

- `httpx` (async) — already in requirements
- `openai` — already in requirements
- `argparse`, `asyncio`, `json` — stdlib
