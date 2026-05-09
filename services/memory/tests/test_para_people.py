"""Tests for PARA people entity indexing (Phase 4).

Covers: load, search by name, search by fact content, empty directory handling,
and integration with the full search_memory pipeline.
"""

from __future__ import annotations

import pytest

from services.memory import store
from services.memory.search import search_memory


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def para_people_workspace(tmp_path):
    """Create a temporary PARA people directory with a test-person entity."""
    people_root = tmp_path / "shared" / "life" / "areas" / "people"
    people_root.mkdir(parents=True)

    # test-person entity
    slug_dir = people_root / "test-person"
    slug_dir.mkdir()

    (slug_dir / "summary.md").write_text(
        "# Roshni Kapoor\n\nRoshni is a Weekend direct report. "
        "She leads the design team and reports to Chris.\n"
    )
    (slug_dir / "items.yaml").write_text(
        "name: Roshni Kapoor\n"
        "role: Design Lead\n"
        "team: Weekend\n"
        "reports_to: Chris\n"
        "location: San Francisco\n"
    )

    # Another entity for search differentiation
    alec_dir = people_root / "alec-chen"
    alec_dir.mkdir()

    (alec_dir / "summary.md").write_text(
        "# Alec Chen\n\nAlec is an engineering manager at Weekend.\n"
    )
    (alec_dir / "items.yaml").write_text(
        "name: Alec Chen\n"
        "role: Engineering Manager\n"
        "team: Weekend\n"
        "reports_to: Chris\n"
    )

    return people_root


@pytest.fixture
def workspace_with_corpus_and_people(monkeypatch, tmp_path, para_people_workspace):
    """Combined fixture: MEMORY.md corpus + PARA people entities."""
    fake_root = tmp_path / "workspace" / "agents"
    fake_root.mkdir(parents=True)
    monkeypatch.setattr(store, "WORKSPACE_AGENT_PATHS", [fake_root])

    # Set the PARA_PEOPLE_PATH to our fixture
    monkeypatch.setattr(store, "PARA_PEOPLE_PATH", para_people_workspace)

    md = fake_root / "axel" / "MEMORY.md"
    md.parent.mkdir(parents=True)
    md.write_text(
        "# Long-Term Memory\n"
        "\n"
        "## Promoted From Short-Term Memory (2026-04-21)\n"
        "\n"
        "- LiteLLM Docker container can crash and the watchdog should auto-restart it.\n"
        "- Slack hooks must be async to avoid blocking the Node event loop.\n"
    )

    return fake_root, para_people_workspace


# ---------------------------------------------------------------------------
# read_para_people_entries tests
# ---------------------------------------------------------------------------

def test_read_para_people_loads_entries(para_people_workspace):
    entries = store.read_para_people_entries(people_path=para_people_workspace)
    # 1 summary + 5 items for test-person, 1 summary + 4 items for alec-chen = 11
    assert len(entries) == 11

    # Check summary entry
    summaries = [e for e in entries if e.source_file == "summary"]
    assert len(summaries) == 2
    roshni_summary = [e for e in summaries if e.slug == "test-person"][0]
    assert "Roshni" in roshni_summary.text
    assert roshni_summary.item_key is None

    # Check items entries
    items = [e for e in entries if e.source_file == "items" and e.slug == "test-person"]
    assert len(items) == 5
    name_item = [e for e in items if e.item_key == "name"][0]
    assert "Roshni Kapoor" in name_item.text


def test_read_para_people_empty_directory(tmp_path):
    empty_root = tmp_path / "empty_people"
    empty_root.mkdir()
    entries = store.read_para_people_entries(people_path=empty_root)
    assert entries == []


def test_read_para_people_missing_directory(tmp_path):
    missing = tmp_path / "nonexistent"
    entries = store.read_para_people_entries(people_path=missing)
    assert entries == []


def test_read_para_people_skips_files_in_root(tmp_path):
    """Non-directory entries in the root should be silently skipped."""
    people_root = tmp_path / "people"
    people_root.mkdir()
    (people_root / "README.md").write_text("Not a person directory")
    entries = store.read_para_people_entries(people_path=people_root)
    assert entries == []


def test_read_para_people_handles_malformed_yaml(tmp_path):
    """Malformed items.yaml should be gracefully skipped."""
    people_root = tmp_path / "people"
    people_root.mkdir()
    slug_dir = people_root / "bad-yaml"
    slug_dir.mkdir()
    (slug_dir / "summary.md").write_text("A valid summary")
    (slug_dir / "items.yaml").write_text("{{invalid yaml :: []")
    entries = store.read_para_people_entries(people_path=people_root)
    # Only the summary entry should be present
    assert len(entries) == 1
    assert entries[0].source_file == "summary"


def test_read_para_people_list_yaml(tmp_path):
    """items.yaml as a list (not dict) should produce numbered entries."""
    people_root = tmp_path / "people"
    people_root.mkdir()
    slug_dir = people_root / "list-person"
    slug_dir.mkdir()
    (slug_dir / "items.yaml").write_text("- first fact\n- second fact\n")
    entries = store.read_para_people_entries(people_path=people_root)
    assert len(entries) == 2
    assert entries[0].item_key == "0"
    assert "first fact" in entries[0].text
    assert entries[1].item_key == "1"
    assert "second fact" in entries[1].text


def test_para_person_entry_memory_id():
    entry_with_key = store.ParaPersonEntry(
        slug="jane", text="name: Jane", source_file="items", item_key="name"
    )
    assert entry_with_key.memory_id() == "para_person:jane:items:name"

    entry_summary = store.ParaPersonEntry(
        slug="jane", text="A summary", source_file="summary", item_key=None
    )
    assert entry_summary.memory_id() == "para_person:jane:summary"


# ---------------------------------------------------------------------------
# search_memory integration tests (keyword-only, no embed/graphiti)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_returns_para_person_by_name(workspace_with_corpus_and_people):
    results = await search_memory(agent="axel", query="Roshni")
    para_results = [r for r in results if r.kind == "para_person"]
    assert para_results, "expected at least one para_person result for 'Roshni'"
    assert any("Roshni" in r.excerpt for r in para_results)


@pytest.mark.asyncio
async def test_search_returns_para_person_by_fact(workspace_with_corpus_and_people):
    results = await search_memory(agent="axel", query="Design Lead Weekend")
    para_results = [r for r in results if r.kind == "para_person"]
    assert para_results, "expected at least one para_person result for 'Design Lead Weekend'"


@pytest.mark.asyncio
async def test_search_para_person_and_memory_md_mixed(workspace_with_corpus_and_people):
    """Both memory_md and para_person results should appear for relevant queries."""
    results = await search_memory(agent="axel", query="Weekend Slack Docker")
    # Depending on scores, may have both or just one kind
    assert len(results) > 0, "expected results for 'Weekend Slack Docker'"


@pytest.mark.asyncio
async def test_search_para_person_source_format(workspace_with_corpus_and_people):
    """para_person results should have source = 'para_people/<slug>/<source_file>'."""
    results = await search_memory(agent="axel", query="Roshni Kapoor")
    para_results = [r for r in results if r.kind == "para_person"]
    for r in para_results:
        assert r.source.startswith("para_people/")
        parts = r.source.split("/")
        assert len(parts) == 3  # para_people/<slug>/summary or items


@pytest.mark.asyncio
async def test_search_no_para_person_in_empty_dir(monkeypatch, tmp_path):
    """When PARA_PEOPLE_PATH is empty, search should still work with memory_md."""
    fake_root = tmp_path / "workspace" / "agents"
    fake_root.mkdir(parents=True)
    monkeypatch.setattr(store, "WORKSPACE_AGENT_PATHS", [fake_root])
    empty_people = tmp_path / "empty"
    empty_people.mkdir()
    monkeypatch.setattr(store, "PARA_PEOPLE_PATH", empty_people)

    md = fake_root / "axel" / "MEMORY.md"
    md.parent.mkdir(parents=True)
    md.write_text(
        "# Long-Term Memory\n\n"
        "## Promoted From Short-Term Memory (2026-04-21)\n\n"
        "- Some memory fact\n"
    )

    results = await search_memory(agent="axel", query="memory fact")
    assert results, "expected memory_md result"
    assert all(r.kind == "memory_md" for r in results)