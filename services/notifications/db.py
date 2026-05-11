"""Database engine and session factory for the Notification Service."""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from services.notifications.config import get_settings


@lru_cache
def _engine():
    url = get_settings().database_url
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args, pool_pre_ping=True)


def get_session_factory() -> sessionmaker:
    return sessionmaker(bind=_engine(), autocommit=False, autoflush=False)


def get_db() -> Session:
    """FastAPI dependency — yields a session, closes on exit."""
    factory = get_session_factory()
    db = factory()
    try:
        yield db
    finally:
        db.close()
