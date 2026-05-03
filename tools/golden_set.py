"""Golden-set queries for the Memory Service quality evaluation (Phase 0.3).

Each entry is a (agent, query, expected_substring) triple. The evaluation passes
if at least one of the top-3 results contains the expected_substring (case-insensitive).

The set is intentionally small (~5-10) and biased toward observable, high-signal facts
that are present in actual agent MEMORY.md files at authoring time. Queries should be
re-validated if MEMORY.md content changes substantially.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GoldenQuery:
    agent: str
    query: str
    expected_substring: str
    notes: str = ""


GOLDEN_SET: list[GoldenQuery] = [
    GoldenQuery(
        agent="lev",
        query="paperclip identifiers",
        expected_substring="paperclip",
        notes="lev/MEMORY.md has 'PaperClip IDs' explicitly",
    ),
    GoldenQuery(
        agent="lev",
        query="absolute paths workspace",
        expected_substring="absolute paths",
        notes="lev/MEMORY.md: 'Use absolute paths for all main workspace references'",
    ),
    GoldenQuery(
        agent="lev",
        query="storing secrets",
        expected_substring="secrets",
        notes="lev/MEMORY.md: 'Never store secrets here'",
    ),
    GoldenQuery(
        agent="cass",
        query="never edit tool log appending",
        expected_substring="never edit",
        notes="cass/MEMORY.md: 'Always read + append log using exec (never Edit)'",
    ),
    GoldenQuery(
        agent="arlo",
        query="prior experiments repeated",
        expected_substring="experiments",
        notes="arlo/MEMORY.md: 'Read log first — prior experiments must not be repeated'",
    ),
    GoldenQuery(
        agent="maren",
        query="API keys workspace storage",
        expected_substring="api keys",
        notes="maren/MEMORY.md: 'Never store API keys or secrets in this workspace'",
    ),
    GoldenQuery(
        agent="finn",
        query="build state persistence runs",
        expected_substring="build state",
        notes="finn/MEMORY.md: 'Read log first — build state must persist across runs'",
    ),
    GoldenQuery(
        agent="sage",
        query="accountability issues weekly",
        expected_substring="accountability",
        notes="sage/MEMORY.md: '...accountability issues must not disappear weekly'",
    ),
]
