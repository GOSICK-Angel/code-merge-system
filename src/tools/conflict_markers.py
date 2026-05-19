"""Detect unresolved git merge conflict markers in file content.

O-M1: cherry-pick fall-back can leave ``<<<<<<<`` / ``=======`` / ``>>>>>>>``
markers in the working tree. Send those straight to human review instead of
feeding them into the AUTO_MERGE / Judge pipeline.
"""

from __future__ import annotations

from pathlib import Path

CONFLICT_MARKERS: tuple[str, ...] = ("<<<<<<<", "=======", ">>>>>>>")


def has_conflict_markers(content: str) -> bool:
    if not content:
        return False
    return any(marker in content for marker in CONFLICT_MARKERS)


def safe_read_text(abs_path: Path) -> str | None:
    try:
        return abs_path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, FileNotFoundError, PermissionError, IsADirectoryError):
        return None


def file_has_conflict_markers(repo_path: Path, file_path: str) -> bool:
    abs_path = repo_path / file_path
    if not abs_path.is_file():
        return False
    content = safe_read_text(abs_path)
    if content is None:
        return False
    return has_conflict_markers(content)


def extract_conflict_info(
    repo_path: Path,
    file_path: str,
    max_preview_chars: int = 1200,
) -> tuple[int, str]:
    """Return (conflict_block_count, preview_snippet) for a file with markers.

    Counts the number of ``<<<<<<<`` markers (each marks one conflict block).
    The preview contains up to *max_preview_chars* characters of the first
    conflict block so the user can make an informed take/keep/resolve decision.
    Returns (0, "") if the file cannot be read or contains no markers.
    """
    abs_path = repo_path / file_path
    content = safe_read_text(abs_path)
    if not content or not has_conflict_markers(content):
        return 0, ""

    lines = content.splitlines()
    count = sum(1 for ln in lines if ln.startswith("<<<<<<<"))

    # Extract the first conflict block for the preview.
    preview_lines: list[str] = []
    in_block = False
    for ln in lines:
        if ln.startswith("<<<<<<<"):
            in_block = True
        if in_block:
            preview_lines.append(ln)
            if ln.startswith(">>>>>>>"):
                break

    preview = "\n".join(preview_lines)
    if len(preview) > max_preview_chars:
        preview = preview[:max_preview_chars] + "\n... (truncated)"
    return count, preview
