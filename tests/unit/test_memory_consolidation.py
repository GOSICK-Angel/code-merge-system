"""OPP-8: consolidation must not collapse location-distinct patterns.

``_consolidate_entries`` grouped only by ``(phase, entry_type, tags[0])``.
Since the primary tag is often a generic class shared across directories
(``c_class``, ``conflict_decision``), 3+ entries covering different directories
merged into one ``"; "``-joined blob, destroying the directory specificity that
L2 path-scoring depends on. The grouping key now includes a directory bucket
derived from ``file_paths`` so same-directory entries still merge but
cross-directory ones survive.
"""

from __future__ import annotations

from src.memory.models import MemoryEntry, MemoryEntryType
from src.memory.store import _consolidate_entries


def _pattern(content: str, dir_path: str, tag: str = "c_class") -> MemoryEntry:
    return MemoryEntry(
        entry_type=MemoryEntryType.PATTERN,
        phase="planning",
        content=content,
        file_paths=[dir_path],
        tags=[tag, dir_path],
    )


def test_cross_directory_same_tag_patterns_survive():
    entries = [
        _pattern("3 C-class files in src/auth/", "src/auth"),
        _pattern("3 C-class files in src/api/", "src/api"),
        _pattern("3 C-class files in src/db/", "src/db"),
    ]
    result = _consolidate_entries(entries)
    # distinct directories must NOT collapse into one lossy blob
    assert len(result) == 3
    contents = {e.content for e in result}
    assert contents == {e.content for e in entries}


def test_same_directory_patterns_still_merge():
    entries = [
        _pattern(f"pattern variant {i} in src/auth/", "src/auth") for i in range(3)
    ]
    result = _consolidate_entries(entries)
    # same directory + same tag -> still consolidated into one entry
    assert len(result) == 1


def test_decision_entries_split_by_directory():
    entries = [
        MemoryEntry(
            entry_type=MemoryEntryType.DECISION,
            phase="conflict_analysis",
            content=f"{path}: take_target",
            file_paths=[path, dir_prefix],
            tags=["conflict_decision", "take_target", dir_prefix],
        )
        for path, dir_prefix in [
            ("src/auth/login.py", "src/auth"),
            ("src/auth/token.py", "src/auth"),
            ("src/auth/session.py", "src/auth"),
            ("pkg/api/routes.go", "pkg/api"),
            ("pkg/api/handler.go", "pkg/api"),
            ("pkg/api/server.go", "pkg/api"),
        ]
    ]
    result = _consolidate_entries(entries)
    # two directories, each with 3 entries -> two merged entries, not one
    assert len(result) == 2
