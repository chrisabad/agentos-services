"""MEMORY.md storage layer.

Reads parse bullet entries with their date sections + provenance.
Writes append new sections under a date heading.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE_AGENTS = Path.home() / ".openclaw" / "workspace" / "agents"

DATE_HEADING = re.compile(r"^##\s+Promoted From Short-Term Memory\s+\(([0-9]{4}-[0-9]{2}-[0-9]{2})\)\s*$")
PROVENANCE_COMMENT = re.compile(r"^<!--\s*openclaw-memory-promotion:(.+?)\s*-->\s*$")
BULLET = re.compile(r"^-\s+(.+)$")


@dataclass
class MemoryEntry:
    agent: str
    text: str
    section_date: str | None
    provenance: str | None
    line_no: int

    def memory_id(self) -> str:
        return f"{self.agent}:{self.line_no}"


def agent_dir(agent: str) -> Path:
    return WORKSPACE_AGENTS / agent


def memory_md_path(agent: str) -> Path:
    return agent_dir(agent) / "MEMORY.md"


def read_entries(agent: str) -> list[MemoryEntry]:
    """Parse all bullet entries from agent's MEMORY.md.

    Returns entries in file order with their containing date section + last-seen provenance comment.
    """
    path = memory_md_path(agent)
    if not path.exists():
        return []

    entries: list[MemoryEntry] = []
    current_section: str | None = None
    pending_provenance: str | None = None

    for idx, raw_line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue

        m = DATE_HEADING.match(line)
        if m:
            current_section = m.group(1)
            pending_provenance = None
            continue

        m = PROVENANCE_COMMENT.match(line)
        if m:
            pending_provenance = m.group(1)
            continue

        m = BULLET.match(line)
        if m:
            entries.append(
                MemoryEntry(
                    agent=agent,
                    text=m.group(1).strip(),
                    section_date=current_section,
                    provenance=pending_provenance,
                    line_no=idx,
                )
            )
            pending_provenance = None
            continue

    return entries


def append_entry(
    agent: str,
    text: str,
    *,
    kind: str = "manual",
    source: str | None = None,
    now: datetime | None = None,
) -> tuple[str, Path]:
    """Append a new memory entry under a section heading for today's date.

    Creates the agent directory + MEMORY.md if missing. Returns (memory_id, path).
    """
    if not text.strip():
        raise ValueError("text must not be empty")

    now = now or datetime.now(timezone.utc)
    section_date = now.strftime("%Y-%m-%d")
    memory_id = f"{agent}:{section_date}:{uuid.uuid4().hex[:8]}"

    path = memory_md_path(agent)
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        path.write_text("# Long-Term Memory\n\n", encoding="utf-8")

    existing = path.read_text(encoding="utf-8")
    section_header = f"## Promoted From Short-Term Memory ({section_date})"
    block_lines: list[str] = []
    if section_header not in existing:
        block_lines.append("")
        block_lines.append(section_header)
        block_lines.append("")

    provenance = source or f"{kind}:{memory_id}"
    block_lines.append(f"<!-- agentos-memory-append:{provenance} -->")
    block_lines.append(f"- {text.strip()}")
    block_lines.append("")

    appended = "\n".join(block_lines).rstrip() + "\n"
    with path.open("a", encoding="utf-8") as fh:
        if not existing.endswith("\n"):
            fh.write("\n")
        fh.write(appended)

    return memory_id, path
