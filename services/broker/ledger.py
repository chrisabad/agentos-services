"""Attention Broker ledger — durable topic state.

Storage shape:
    {
      "version": 1,
      "topics": {
        "<fingerprint>": {
          "fingerprint": str,
          "canonical_name": str,
          "flow": "juno_to_chris" | "agent_to_juno",
          "consumer": "chris" | "juno",
          "business": str,
          "category": str,
          "resolved_channel": str | null,
          "related_issue_ids": [str, ...],
          "related_thread_ids": [str, ...],
          "state": "triggered" | "surfaced" | "acknowledged" | "resolved" | "muted",
          "first_seen": iso8601,
          "last_surfaced": iso8601 | null,
          "surface_count": int,
          "surface_tier": "immediate" | "daily_brief" | "weekly_brief" | "muted",
          "disposition": str | null,
          "disposition_source": str | null,
          "disposition_evidence": str | null,
          "producer_actions": [{"action": str, "evidence_ref": str, "ts": iso8601}, ...],
          "muted_until": iso8601 | null,
          "last_state_change": iso8601
        }
      }
    }

Persistence: single JSON file at $AGENTOS_BROKER_LEDGER_PATH (default
`~/.agentos/state/broker/ledger.json`). Atomic writes via tempfile + os.replace.
Concurrent access from a single Python process is safe through serialization at
the call site (the broker holds the ledger in-memory between load/save). Multi-
process write contention is not handled; the broker service is single-process.
"""

from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DEFAULT_LEDGER_PATH = Path.home() / ".agentos" / "state" / "broker" / "ledger.json"
LEDGER_VERSION = 1

VALID_STATES = {"triggered", "surfaced", "acknowledged", "resolved", "muted"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ledger_path() -> Path:
    override = os.environ.get("AGENTOS_BROKER_LEDGER_PATH")
    return Path(override) if override else DEFAULT_LEDGER_PATH


def _empty_ledger() -> dict[str, Any]:
    return {"version": LEDGER_VERSION, "topics": {}}


def load_ledger() -> dict[str, Any]:
    """Load the ledger from disk. Returns an empty ledger if file is missing."""
    path = ledger_path()
    if not path.exists():
        return _empty_ledger()
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        # Corrupt or unreadable — treat as empty rather than crash. The next save
        # will overwrite. Caller should log; we don't have a logger here.
        return _empty_ledger()
    if not isinstance(data, dict):
        return _empty_ledger()
    data.setdefault("version", LEDGER_VERSION)
    data.setdefault("topics", {})
    return data


def save_ledger(ledger: dict[str, Any]) -> None:
    """Atomic write to the ledger path. Creates parent dirs as needed."""
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".ledger-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(ledger, f, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def get_topic(ledger: dict[str, Any], fingerprint: str) -> dict[str, Any] | None:
    return ledger.get("topics", {}).get(fingerprint)


def upsert_topic(ledger: dict[str, Any], topic: dict[str, Any]) -> dict[str, Any]:
    """Insert or replace a topic by fingerprint. Mutates and returns the ledger."""
    fp = topic.get("fingerprint")
    if not fp:
        raise ValueError("topic missing 'fingerprint'")
    ledger.setdefault("topics", {})
    ledger["topics"][fp] = topic
    return ledger


def transition_topic(
    ledger: dict[str, Any],
    fingerprint: str,
    new_state: str,
    *,
    disposition: str | None = None,
    disposition_source: str | None = None,
    disposition_evidence: str | None = None,
) -> dict[str, Any]:
    """Move a topic into a new state, optionally setting disposition fields.

    Returns the (mutated) ledger. Raises ValueError if topic is unknown or state invalid.
    """
    if new_state not in VALID_STATES:
        raise ValueError(f"invalid state {new_state!r}; expected one of {sorted(VALID_STATES)}")
    topic = get_topic(ledger, fingerprint)
    if topic is None:
        raise ValueError(f"unknown topic {fingerprint!r}")
    topic["state"] = new_state
    topic["last_state_change"] = _now_iso()
    if disposition is not None:
        topic["disposition"] = disposition
    if disposition_source is not None:
        topic["disposition_source"] = disposition_source
    if disposition_evidence is not None:
        topic["disposition_evidence"] = disposition_evidence
    ledger["topics"][fingerprint] = topic
    return ledger


def record_surface(
    ledger: dict[str, Any],
    fingerprint: str,
    *,
    channel: str | None,
    tier: str,
) -> dict[str, Any]:
    """Increment surface_count + set last_surfaced + transition to 'surfaced'."""
    topic = get_topic(ledger, fingerprint)
    if topic is None:
        raise ValueError(f"unknown topic {fingerprint!r}")
    topic["surface_count"] = int(topic.get("surface_count", 0)) + 1
    topic["last_surfaced"] = _now_iso()
    topic["surface_tier"] = tier
    if channel:
        topic["resolved_channel"] = channel
    if topic.get("state") == "triggered":
        topic["state"] = "surfaced"
        topic["last_state_change"] = topic["last_surfaced"]
    ledger["topics"][fingerprint] = topic
    return ledger


def add_producer_action(
    ledger: dict[str, Any],
    fingerprint: str,
    action: str,
    evidence_ref: str = "",
) -> dict[str, Any]:
    """Append a producer-action record. Used to close the self-amnesia gap (AGE-7559)."""
    topic = get_topic(ledger, fingerprint)
    if topic is None:
        raise ValueError(f"unknown topic {fingerprint!r}")
    actions = topic.setdefault("producer_actions", [])
    actions.append({
        "action": action,
        "evidence_ref": evidence_ref,
        "ts": _now_iso(),
    })
    ledger["topics"][fingerprint] = topic
    return ledger


def prune_resolved(ledger: dict[str, Any], max_age_hours: int = 168) -> dict[str, Any]:
    """Drop resolved topics older than `max_age_hours` since their last_state_change.

    Keeps `muted` and `acknowledged` topics regardless (they may need re-surfacing).
    Default 168h = 7 days.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    surviving: dict[str, Any] = {}
    for fp, topic in ledger.get("topics", {}).items():
        if topic.get("state") != "resolved":
            surviving[fp] = topic
            continue
        ts = topic.get("last_state_change") or topic.get("first_seen")
        if not ts:
            surviving[fp] = topic
            continue
        try:
            ts_str = ts[:-1] + "+00:00" if isinstance(ts, str) and ts.endswith("Z") else ts
            ts_dt = datetime.fromisoformat(ts_str) if isinstance(ts_str, str) else None
        except ValueError:
            ts_dt = None
        if ts_dt is None or ts_dt > cutoff:
            surviving[fp] = topic
    ledger["topics"] = surviving
    return ledger


def get_stats(ledger: dict[str, Any]) -> dict[str, Any]:
    """Return a small summary suitable for /broker/stats and the CLI."""
    topics = ledger.get("topics", {})
    by_state: dict[str, int] = {}
    by_tier: dict[str, int] = {}
    total_surfaces = 0
    for t in topics.values():
        s = t.get("state", "unknown")
        by_state[s] = by_state.get(s, 0) + 1
        tier = t.get("surface_tier", "unknown")
        by_tier[tier] = by_tier.get(tier, 0) + 1
        total_surfaces += int(t.get("surface_count", 0))
    return {
        "total_topics": len(topics),
        "by_state": by_state,
        "by_tier": by_tier,
        "total_surfaces": total_surfaces,
        "version": ledger.get("version", LEDGER_VERSION),
    }


# Defensive copy helper exposed for callers who want immutability.
def snapshot(ledger: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(ledger)
