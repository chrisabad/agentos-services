# agentos-services

Standalone HTTP services backing the AgentOS Intelligence & Memory layer. Decouples memory + attention broker + ingestion from the OpenClaw gateway runtime so any agent harness (Hermes, Codex, future) can consume them via plain HTTP.

PRD: `~/.openclaw/workspace/memory/prds/2026-05-02-hermes-native-im-and-juno.md`

## Services

| Service | Port | Status |
|---------|------|--------|
| Memory  | 8010 | Phase 0.1 — skeleton (`/health` + bearer auth) |
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

## Adding a new service

When Phase 1 (broker) and Phase 2 (ingestion) land, add `services/broker/` and `services/ingestion/` packages following the same shape as `services/memory/`. Each gets its own plist in `deploy/` and its own port. Auth + config patterns are shared.
