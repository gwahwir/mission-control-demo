---
name: baseline-store implementation progress
description: Current progress on the baseline store service implementation — where to resume
type: project
---

Implementation of the Baseline Store (plain FastAPI service, port 8010) is in progress on branch `feature/baseline-store` in worktree `.worktrees/baseline-store`.

**Plan:** `docs/superpowers/plans/2026-03-25-baseline-store.md`
**Spec:** `docs/superpowers/specs/2026-03-25-baseline-store-design.md`

## Task progress

| Task | Status |
|---|---|
| Task 1: Package scaffold + stores.py | ✅ complete |
| Task 2: POST /topics, GET /topics | ✅ complete |
| Task 3: POST /baselines/{topic_path}/versions | ✅ complete |
| Task 4: POST /baselines/{topic_path}/deltas | ✅ complete |
| Task 5: GET /current and GET /history | ⏸ **RESUMPTION POINT** (see below) |
| Task 6: GET /rollup and GET /similar | pending |
| Task 7: Finalise server.py and write README | pending |
| Task 8: Deployment wiring | pending |

## Resumption point

**Task 5 code quality review is pending.**

- Implementation committed as `4aa27bf` in the worktree
- Spec compliance review ✅ passed
- Next step: dispatch code quality reviewer subagent for Task 5, then continue with Task 6

**Important notes for Task 6:**
- `GET /baselines/similar` must be registered **before** `GET /baselines/{topic_path}/...` routes in `routes.py` to avoid FastAPI treating "similar" as a `topic_path` parameter
- Tests patch `baseline_store.routes.get_pgvector_pool` (NOT `baseline_store.stores.get_pgvector_pool`) — this is consistent across all tasks

**Why:** Plan said to patch `baseline_store.stores.get_pgvector_pool` but `routes.py` does `from baseline_store.stores import get_pgvector_pool`, so patching the routes module name is the correct Python mock target.

## Current test count

14 tests in `tests/test_baseline_store.py`, all passing. 4 pre-existing failures in `test_lead_analyst_meta_analysis.py` are unrelated — ignore.
