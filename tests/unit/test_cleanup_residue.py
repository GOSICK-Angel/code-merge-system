"""Phase 5 cleanup-residue tests (P5-3 / P5-4 / P5-5 / P5-6).

These tests assert that no stale references to the deleted React Ink TUI
remain in the source tree, the tests tree, or top-level documentation.
They are intentionally cheap grep-style checks so a future regression
(accidental re-import, half-finished revert) trips CI immediately.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Exclude this test file itself from grep-style scans — its needles
# appear inside docstrings / string literals by design.
_SELF_PATH = Path(__file__).resolve()


def _python_files(root: Path) -> list[Path]:
    return [
        p
        for p in root.rglob("*.py")
        if "__pycache__" not in p.parts
        and "node_modules" not in p.parts
        and p.resolve() != _SELF_PATH
    ]


def test_no_tui_module_imports_remain() -> None:
    """P5-3: no `from src.cli.commands.tui` / `import src.cli.commands.tui`
    in src/ or tests/ — the module is deleted."""
    needles = (
        "from src.cli.commands.tui",
        "import src.cli.commands.tui",
    )
    offenders: list[str] = []
    for root in (REPO_ROOT / "src", REPO_ROOT / "tests"):
        for path in _python_files(root):
            text = path.read_text(encoding="utf-8", errors="replace")
            for needle in needles:
                if needle in text:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}: {needle!r}")
    assert not offenders, "Stale TUI module imports remain:\n  " + "\n  ".join(
        offenders
    )


def test_no_tui_command_impl_refs_remain() -> None:
    """P5-4: no `tui_command_impl` / `tui_resume_impl` references in
    src/ or tests/ — both functions are deleted."""
    needles = ("tui_command_impl", "tui_resume_impl")
    offenders: list[str] = []
    for root in (REPO_ROOT / "src", REPO_ROOT / "tests"):
        for path in _python_files(root):
            text = path.read_text(encoding="utf-8", errors="replace")
            for needle in needles:
                if needle in text:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}: {needle!r}")
    assert not offenders, (
        "Stale tui_command_impl/tui_resume_impl refs remain:\n  "
        + "\n  ".join(offenders)
    )


def test_tui_directory_deleted() -> None:
    """P5-5: the top-level `tui/` React Ink directory is fully removed."""
    assert not (REPO_ROOT / "tui").exists(), (
        "tui/ directory still exists — Phase 5a deletion incomplete"
    )


def test_docs_no_no_tui_except_deprecation_note() -> None:
    """P5-6: `--no-tui` may appear in user-facing docs only inside
    deprecation context (alias note, deprecation table). Any other mention
    fails the test.

    Scanned: CLAUDE.md, README*.md, doc/*.md (recursive).
    Excluded:
      - doc/web-ui-redesign-handoff.md — frozen historical record
      - doc/test-report/** — historical test reports
      - docs/web-ui-redesign-tests.md — test matrix references --no-tui for
        historical context (location pointer for the cleanup)
    """
    candidates: list[Path] = []
    for name in ("README.md", "README_zh.md", "CLAUDE.md"):
        path = REPO_ROOT / name
        if path.exists():
            candidates.append(path)

    excluded_paths = {
        REPO_ROOT / "doc" / "web-ui-redesign-handoff.md",
        REPO_ROOT / "docs" / "web-ui-redesign-tests.md",
    }
    for doc_root in (REPO_ROOT / "doc", REPO_ROOT / "docs"):
        if not doc_root.exists():
            continue
        for path in doc_root.rglob("*.md"):
            if path.resolve() in {p.resolve() for p in excluded_paths}:
                continue
            if "test-report" in path.parts:
                continue
            candidates.append(path)

    pattern = re.compile(r"--no-tui")
    deprecation_keywords = ("deprecat",)
    offenders: list[str] = []
    for path in candidates:
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        for lineno, line in enumerate(lines, start=1):
            if not pattern.search(line):
                continue
            window_start = max(0, lineno - 4)
            window = "\n".join(lines[window_start : lineno + 1]).lower()
            if any(kw in window for kw in deprecation_keywords):
                continue
            offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")
    assert not offenders, (
        "`--no-tui` referenced outside deprecation context:\n  "
        + "\n  ".join(offenders)
    )
