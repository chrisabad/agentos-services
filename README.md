# agentos-services

Standalone HTTP services backing the AgentOS Intelligence & Memory layer. Decouples memory + attention broker + ingestion from the OpenClaw gateway runtime so any agent harness (Hermes, Codex, future) can consume them via plain HTTP.

PRD: `~/.openclaw/workspace/memory/prds/2026-05-02-hermes-native-im-and-juno.md`

## Services

| Service | Port | Status |
|---------|------|--------|
| Memory  | 8010 | Phase 0 — `/health` + `/memory/{search,append,promote}` with hybrid retrieval (keyword + embedding rerank + Graphiti supplement) |
| Attention Broker | 8011 | Not started (Phase 1) |
| Ingestion | 8012 | Not started (Phase 2) |

## Quick start

```bash
# 1. Install dependencies into a local venv
python3 -m venv .venv
./.venv/bin/pip install -e ".[dev]"

# 2. Run tests
./.venv/bin/pytest -q

# 3. Run the memory service locally
export PAPERCLIP_BOARD_KEY=...   # bearer token
./.venv/bin/uvicorn services.memory.app:app --host 127.0.0.1 --port 8010
```

Or use the Make targets:

```bash
make test
make dev
```

## Install as a launchd service

```bash
make install     # loads com.agentos.memory-service from deploy/
make logs        # tails ~/.agentos/logs/memory-service.log
make uninstall   # removes the launchd job
```

The plist binds to `127.0.0.1:8010` and reads `PAPERCLIP_BOARD_KEY` from the user environment. The loader script (`deploy/launchctl-load.sh`) creates the venv if missing and runs `pip install -e .`.

## Auth

All paths except `/health` require `Authorization: Bearer <PAPERCLIP_BOARD_KEY>`. If the env var is missing, the service returns `503 service_misconfigured` rather than letting requests through.

## Layout

```
agentos-services/
  services/
    memory/
      app.py           # FastAPI app factory
      auth.py          # Bearer middleware
      config.py        # Settings + version helper
      tests/
  deploy/
    com.agentos.memory-service.plist
    launchctl-load.sh
  pyproject.toml
  Makefile
  README.md
```

## Memory Service performance

Phase 0.3 measured the search path with prewarmed cache + 1s embedding-rerank timeout (defaults). On a sequential local Gemma-300M embedding server (`max_concurrent: 1`):

- p50 ~ 400 ms
- p95 / p99 capped at the 1s embedding timeout (degrades to keyword-only on slow paths)
- 8/8 quality hits on the curated golden set

The 500 ms p99 target is **not achievable in the synchronous request path** with the current sequential embedding architecture. AGE-12075 tracks moving the rerank to a background task with a side-cache, which should drop p99 below 500 ms.

### Tuning knobs

| Env var | Default | Purpose |
|---------|---------|---------|
| `AGENTOS_MEMORY_PREWARM_AGENTS` | (empty) | Comma-separated agent names. On startup, the service embeds each agent's MEMORY.md entries into the LRU cache so subsequent searches hit cache. |
| `AGENTOS_MEMORY_EMBED_RERANK_TIMEOUT_S` | `1.0` | Per-call cap on the embedding rerank. On timeout, search degrades gracefully to keyword-only. |
| `AGENTOS_MEMORY_EMBEDDING_ENABLED` | `true` | Set to `0` to disable embedding rerank entirely (keyword-only mode). |
| `AGENTOS_MEMORY_GRAPHITI_ENABLED` | `true` | Set to `0` to disable the Graphiti supplement. |
| `AGENTOS_EMBEDDING_QUERY_CACHE_SIZE` | `256` | LRU size for query + document embedding cache. |

## Eval + bench tools

```bash
# Quality eval against curated golden set
PAPERCLIP_BOARD_KEY=... .venv/bin/python tools/eval_quality.py

# Latency benchmark
PAPERCLIP_BOARD_KEY=... .venv/bin/python tools/bench_latency.py --n 100 --warmup 10

# Sustained-rate soak with rolling p50/p99 report
PAPERCLIP_BOARD_KEY=... .venv/bin/python tools/soak.py --minutes 30 --rps 1.0
```

## Adding a new service

When Phase 1 (broker) and Phase 2 (ingestion) land, add `services/broker/` and `services/ingestion/` packages following the same shape as `services/memory/`. Each gets its own plist in `deploy/` and its own port. Auth + config patterns are shared.
