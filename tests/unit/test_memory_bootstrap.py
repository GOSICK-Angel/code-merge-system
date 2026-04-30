"""M3: cold-start memory bootstrap from CLAUDE.md."""

from __future__ import annotations

from pathlib import Path

from src.memory.bootstrap import bootstrap_from_claude_md
from src.memory.sqlite_store import SQLiteMemoryStore


def _write_claude_md(repo: Path, body: str) -> None:
    (repo / "CLAUDE.md").write_text(body, encoding="utf-8")


def _open_store(repo: Path) -> SQLiteMemoryStore:
    db = repo / ".merge" / "memory.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    return SQLiteMemoryStore.open(db)


def test_no_claude_md_means_zero_entries(tmp_path: Path) -> None:
    store = _open_store(tmp_path)
    added = bootstrap_from_claude_md(store, tmp_path)
    assert added == 0
    assert store.entry_count == 0


def test_imports_each_level2_section(tmp_path: Path) -> None:
    _write_claude_md(
        tmp_path,
        """# Title

## Commands

`pip install -e ".[dev]"` install with dev deps.
Run `pytest tests/unit/` to execute unit tests only.

## Architecture Constraints

- No `TIMEOUT_DEFAULT` enum value
- Judge agents are read-only
- Executor must snapshot before writing

## Code Style

Python 3.11+, async/await throughout. Pydantic v2.
""",
    )
    store = _open_store(tmp_path)
    added = bootstrap_from_claude_md(store, tmp_path)
    assert added == 3
    assert store.entry_count == 3

    contents = [e.content for e in store.query_by_tags(["bootstrap"], limit=10)]
    assert any("Commands" in c for c in contents)
    assert any("Architecture Constraints" in c for c in contents)
    assert any("Code Style" in c for c in contents)


def test_idempotent_across_invocations(tmp_path: Path) -> None:
    _write_claude_md(
        tmp_path,
        """## Section A

content A here. additional words to clear the min length filter.

## Section B

content B here. additional words to clear the min length filter.
""",
    )
    store = _open_store(tmp_path)
    first = bootstrap_from_claude_md(store, tmp_path)
    second = bootstrap_from_claude_md(store, tmp_path)
    assert first == 2
    assert second == 0
    assert store.entry_count == 2


def test_skips_empty_sections(tmp_path: Path) -> None:
    _write_claude_md(
        tmp_path,
        """## Empty section

## Real section

real content lives here.
""",
    )
    store = _open_store(tmp_path)
    added = bootstrap_from_claude_md(store, tmp_path)
    assert added == 1


def test_no_level2_headings_means_zero(tmp_path: Path) -> None:
    _write_claude_md(tmp_path, "Just a free-form paragraph without any headings.\n")
    store = _open_store(tmp_path)
    assert bootstrap_from_claude_md(store, tmp_path) == 0


def test_truncates_long_section(tmp_path: Path) -> None:
    long_body = "x" * 5000
    _write_claude_md(tmp_path, f"## Big\n\n{long_body}\n")
    store = _open_store(tmp_path)
    added = bootstrap_from_claude_md(store, tmp_path)
    assert added == 1
    entry = store.query_by_tags(["bootstrap"], limit=1)[0]
    assert len(entry.content) < 1000
    assert entry.content.endswith("...")


def test_entry_has_bootstrap_tag_and_codebase_insight_type(tmp_path: Path) -> None:
    from src.memory.models import ConfidenceLevel, MemoryEntryType

    _write_claude_md(tmp_path, "## Stuff\n\nuseful conventions here.\n")
    store = _open_store(tmp_path)
    bootstrap_from_claude_md(store, tmp_path)
    entry = store.query_by_tags(["bootstrap"], limit=1)[0]
    assert entry.entry_type == MemoryEntryType.CODEBASE_INSIGHT
    assert entry.confidence_level == ConfidenceLevel.HEURISTIC
    assert "bootstrap" in entry.tags
    assert entry.phase == "planning"
