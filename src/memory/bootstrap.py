"""M3: Cold-start memory bootstrap from <repo>/CLAUDE.md.

When the project's memory store is empty (first run on a new repo), the
layered loader returns nothing on every call and the LLMs operate without
any project-specific context. CLAUDE.md is the canonical place for
project-level conventions, architecture constraints, and required env
vars — wiring it into L0/L2 gives a useful baseline at zero LLM cost.

Idempotent: each section is hashed via ``MemoryEntry.content_hash`` and
the SQLite UNIQUE INDEX on ``content_hash`` rejects duplicates, so calling
``bootstrap_from_claude_md`` on every run is safe.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from src.memory.models import (
    ConfidenceLevel,
    MemoryEntry,
    MemoryEntryType,
)
from src.memory.sqlite_store import SQLiteMemoryStore

logger = logging.getLogger(__name__)

_MAX_FILE_BYTES = 20 * 1024
_MIN_SECTION_CHARS = 40
_MAX_SECTION_CHARS = 800
_BOOTSTRAP_TAG = "bootstrap"
_BOOTSTRAP_PHASE = "planning"


def bootstrap_from_claude_md(
    store: SQLiteMemoryStore,
    repo_path: str | Path,
) -> int:
    """Seed ``store`` with sections from ``<repo>/CLAUDE.md``.

    Returns the number of entries added (0 when the file is missing,
    empty, or already imported).
    """
    claude_md = Path(repo_path) / "CLAUDE.md"
    if not claude_md.is_file():
        return 0

    try:
        raw = claude_md.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.debug("bootstrap: failed to read %s: %s", claude_md, exc)
        return 0

    if len(raw) > _MAX_FILE_BYTES:
        raw = raw[:_MAX_FILE_BYTES]

    sections = _split_sections(raw)
    if not sections:
        return 0

    added = 0
    for heading, body in sections:
        content = _format_section(heading, body)
        if content is None:
            continue
        entry = MemoryEntry(
            entry_type=MemoryEntryType.CODEBASE_INSIGHT,
            phase=_BOOTSTRAP_PHASE,
            content=content,
            file_paths=[],
            tags=[_BOOTSTRAP_TAG],
            confidence=0.5,
            confidence_level=ConfidenceLevel.HEURISTIC,
        )
        before = store.entry_count
        store.add_entry(entry)
        if store.entry_count > before:
            added += 1

    if added:
        logger.info("memory bootstrap: imported %d sections from CLAUDE.md", added)
    return added


_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


def _split_sections(text: str) -> list[tuple[str, str]]:
    """Split markdown by level-2 headings into ``(heading, body)`` pairs."""
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return []

    sections: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        heading = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        sections.append((heading, body))
    return sections


def _format_section(heading: str, body: str) -> str | None:
    body = body.strip()
    if not body:
        return None

    if len(body) > _MAX_SECTION_CHARS:
        body = body[:_MAX_SECTION_CHARS].rstrip() + "..."

    composed = f"[CLAUDE.md / {heading}] {body}"
    if len(composed) < _MIN_SECTION_CHARS:
        return None
    return composed
