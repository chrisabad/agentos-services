.PHONY: help dev test fmt install uninstall logs

PORT ?= 8010

help:
	@echo "agentos-services — Makefile targets"
	@echo "  make dev        — run memory service on :$(PORT) with live reload"
	@echo "  make test       — run pytest"
	@echo "  make fmt        — ruff format + lint"
	@echo "  make install    — load com.agentos.memory-service launchd plist"
	@echo "  make uninstall  — unload the launchd plist"
	@echo "  make logs       — tail the memory-service log"

dev:
	uvicorn services.memory.app:app --host 127.0.0.1 --port $(PORT) --reload

VENV_PATH := ./.venv

test:
	$(VENV_PATH)/bin/pytest -q

fmt:
	ruff format .
	ruff check --fix .

install:
	bash deploy/launchctl-load.sh

uninstall:
	launchctl bootout gui/$$(id -u) ~/Library/LaunchAgents/com.agentos.memory-service.plist || true

logs:
	tail -f $$HOME/.agentos/logs/memory-service.log
