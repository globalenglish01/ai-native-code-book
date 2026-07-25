from __future__ import annotations

from ainative_core.protocols import MemoryEntry
from ainative_memory.rendering import render_memory_entries


def test_render_empty_list_returns_empty_string():
    assert render_memory_entries([]) == ""


def test_render_entries_includes_sequence_and_content():
    entries = [
        MemoryEntry(owner_id="u1", sequence=2, content="second memory"),
        MemoryEntry(owner_id="u1", sequence=1, content="first memory"),
    ]
    rendered = render_memory_entries(entries)
    assert "## Memory #2" in rendered
    assert "second memory" in rendered
    assert "## Memory #1" in rendered
    assert "first memory" in rendered


def test_render_entries_respects_custom_header_template():
    entries = [MemoryEntry(owner_id="u1", sequence=5, content="content")]
    rendered = render_memory_entries(entries, header_template="Chapter {sequence}")
    assert rendered.startswith("Chapter 5")
