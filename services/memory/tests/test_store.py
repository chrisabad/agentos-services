"""Tests for store.py — MEMORY.md read + append."""

from __future__ import annotations


import pytest

from services.memory import store


@pytest.fixture
def tmp_workspace(monkeypatch, tmp_path):
    fake_root = tmp_path / "workspace" / "agents"
    fake_root.mkdir(parents=True)
    monkeypatch.setattr(store, "WORKSPACE_AGENT_PATHS", [fake_root])
    return fake_root


def test_read_entries_handles_missing_file(tmp_workspace):
    assert store.read_entries("ghost") == []


def test_append_creates_file_and_section(tmp_workspace):
    memory_id, path = store.append_entry("alice", "Always wear sunscreen")
    assert path.exists()
    text = path.read_text()
    assert "# Long-Term Memory" in text
    assert "## Promoted From Short-Term Memory" in text
    assert "Always wear sunscreen" in text
    assert memory_id.startswith("alice:")


def test_append_then_read_roundtrip(tmp_workspace):
    store.append_entry("bob", "First memory")
    store.append_entry("bob", "Second memory", source="manual:test")
    entries = store.read_entries("bob")
    texts = [e.text for e in entries]
    assert "First memory" in texts
    assert "Second memory" in texts


def test_append_rejects_empty_text(tmp_workspace):
    with pytest.raises(ValueError):
        store.append_entry("carol", "   ")


def test_read_entries_parses_existing_format(tmp_workspace):
    agent_dir = tmp_workspace / "dave"
    agent_dir.mkdir(parents=True)
    md = agent_dir / "MEMORY.md"
    md.write_text(
        "# Long-Term Memory\n"
        "\n"
        "## Promoted From Short-Term Memory (2026-04-21)\n"
        "\n"
        "<!-- openclaw-memory-promotion:memory:memory/2026-04-20.md:5:7 -->\n"
        "- The first promoted entry [score=0.7]\n"
        "\n"
        "## Promoted From Short-Term Memory (2026-04-22)\n"
        "\n"
        "<!-- openclaw-memory-promotion:memory:memory/2026-04-21.md:9:11 -->\n"
        "- The second promoted entry [score=0.6]\n"
    )
    entries = store.read_entries("dave")
    assert len(entries) == 2
    assert entries[0].section_date == "2026-04-21"
    assert "memory/2026-04-20.md:5:7" in (entries[0].provenance or "")
    assert entries[1].section_date == "2026-04-22"
    assert entries[1].text.startswith("The second promoted entry")
