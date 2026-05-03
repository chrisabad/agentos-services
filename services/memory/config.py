"""Settings for the memory service. Loads from environment."""

from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel


class Settings(BaseModel):
    service_name: str = "memory"
    host: str = "127.0.0.1"
    port: int = 8010
    auth_token_env: str = "PAPERCLIP_BOARD_KEY"
    log_dir: Path = Path.home() / ".agentos" / "logs"
    repo_root: Path = Path(__file__).resolve().parents[2]


@lru_cache
def get_settings() -> Settings:
    return Settings(
        host=os.environ.get("AGENTOS_MEMORY_HOST", "127.0.0.1"),
        port=int(os.environ.get("AGENTOS_MEMORY_PORT", "8010")),
    )


@lru_cache
def get_version() -> str:
    """Best-effort short SHA. Falls back to 'unknown' outside a git checkout."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(Path(__file__).resolve().parent),
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            return result.stdout.strip() or "unknown"
    except Exception:
        pass
    return "unknown"


def get_auth_token() -> str | None:
    """Return the bearer token the service expects, or None if unset."""
    return os.environ.get(get_settings().auth_token_env)
