"""Settings for the notifications service."""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic import BaseModel


class Settings(BaseModel):
    service_name: str = "notifications"
    host: str = "127.0.0.1"
    port: int = 8012
    auth_token_env: str = "PAPERCLIP_BOARD_KEY"
    database_url: str = "postgresql+psycopg2://paperclip:paperclip@127.0.0.1:54329/paperclip"


@lru_cache
def get_settings() -> Settings:
    return Settings(
        host=os.environ.get("AGENTOS_NOTIFICATIONS_HOST", "127.0.0.1"),
        port=int(os.environ.get("AGENTOS_NOTIFICATIONS_PORT", "8012")),
        database_url=os.environ.get(
            "NOTIFICATIONS_DB_URL",
            "postgresql+psycopg2://paperclip:paperclip@127.0.0.1:54329/paperclip",
        ),
    )


def get_auth_token() -> str | None:
    return os.environ.get(get_settings().auth_token_env)
