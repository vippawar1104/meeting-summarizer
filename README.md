# Reviewly

AI pull request reviewer delivered as a GitHub App. Work in progress: see milestone status below.
No performance or quality numbers are published here until they have been measured.

## Local dev
```
uv sync
make test      # pytest
make lint      # ruff + mypy
make up        # docker compose: postgres+pgvector, redis, migrate, api, worker
```

## Status
- [x] M1 skeleton: webhook verify, dedupe, idempotent enqueue, migrations, CI config
- [ ] M2 worker + queue semantics
- [ ] M3 diff parsing + LLM review
- [ ] M4 RAG
- [ ] M5 cost controls + safety
- [ ] M6 eval harness
- [ ] M7 dashboard, feedback, billing
- [ ] M8 observability, load test, deploy
