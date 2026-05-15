"""Fork-name purity check.

Scans the supplied paths for fork-specific identifiers that the project
forbids in production code (``CLAUDE.md`` § "Project Generality"). Used as
a Phase 0 fixture safeguard and a CI step (Phase 9) so calibration data
from a specific downstream fork never leaks into the generic harness.

Usage::

    python -m scripts.eval._fork_name_check scripts/eval tests/eval

Exit codes:
    0  no forbidden token found
    1  at least one match (printed to stderr)
    2  CLI argument error

The scan uses a word-boundary regex so substrings like ``cvtemp`` /
``modify`` are NOT flagged. Matches inside any of the whitelisted fixture
sub-paths (``tests/eval/datasets/`` / ``tests/eval/fixtures/``) are
ignored — fixtures may use any string as opaque test data, while
production code under ``scripts/eval/`` may not.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

FORBIDDEN_TOKENS: tuple[str, ...] = ("cvte", "dify", "insforge")
"""Concrete fork identifiers that must not appear in eval source code."""

_PATTERN = re.compile(r"\b(" + "|".join(FORBIDDEN_TOKENS) + r")\b", re.IGNORECASE)

WHITELIST_RELATIVE_PARTS: tuple[tuple[str, ...], ...] = (
    ("tests", "eval", "datasets"),
    ("tests", "eval", "fixtures"),
)
"""Relative path prefixes (POSIX-style components) exempt from scanning."""

SELF_BASENAMES: frozenset[str] = frozenset(
    {"_fork_name_check.py", "test_fork_name_check.py"}
)
"""Files that must include the forbidden tokens by definition.

The checker module declares the tokens; its unit-test module exercises
matching against them. Skipping by basename (rather than absolute path)
keeps the rule robust to test-time copies of the file under tmp paths.
"""

SUPPORTED_SUFFIXES: frozenset[str] = frozenset(
    {
        ".py",
        ".pyi",
        ".md",
        ".yaml",
        ".yml",
        ".json",
        ".toml",
        ".cfg",
        ".ini",
        ".txt",
        ".j2",
    }
)


class _Hit:
    __slots__ = ("path", "line_no", "snippet")

    def __init__(self, path: Path, line_no: int, snippet: str) -> None:
        self.path = path
        self.line_no = line_no
        self.snippet = snippet

    def render(self, root: Path) -> str:
        try:
            rel = self.path.relative_to(root)
        except ValueError:
            rel = self.path
        return f"{rel}:{self.line_no}: {self.snippet.strip()}"


def _is_whitelisted(path: Path, project_root: Path) -> bool:
    try:
        rel_parts = path.resolve().relative_to(project_root).parts
    except ValueError:
        return False
    for prefix in WHITELIST_RELATIVE_PARTS:
        if len(rel_parts) >= len(prefix) and rel_parts[: len(prefix)] == prefix:
            return True
    return False


def _iter_files(path: Path) -> Iterable[Path]:
    if path.is_file():
        if path.name not in SELF_BASENAMES:
            yield path
        return
    if not path.is_dir():
        return
    for child in sorted(path.rglob("*")):
        if not child.is_file():
            continue
        if child.suffix not in SUPPORTED_SUFFIXES:
            continue
        if child.name in SELF_BASENAMES:
            continue
        yield child


def scan_paths(
    paths: Sequence[str | Path],
    *,
    project_root: Path | None = None,
) -> list[_Hit]:
    """Scan the given paths and return a list of hits.

    Pure function — no side effects. ``project_root`` defaults to ``Path.cwd()``.
    """
    root = (project_root or Path.cwd()).resolve()
    hits: list[_Hit] = []
    for raw in paths:
        target = Path(raw).resolve()
        for file_path in _iter_files(target):
            if _is_whitelisted(file_path, root):
                continue
            try:
                lines = file_path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for idx, line in enumerate(lines, start=1):
                if _PATTERN.search(line):
                    hits.append(_Hit(file_path, idx, line))
    return hits


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scripts.eval._fork_name_check",
        description="Fail the build if a forbidden fork name appears in eval source.",
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help="Files or directories to scan recursively.",
    )
    parser.add_argument(
        "--project-root",
        default=None,
        help="Project root used to resolve the fixture whitelist (default: cwd).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    project_root = (
        Path(args.project_root).resolve()
        if args.project_root is not None
        else Path.cwd().resolve()
    )
    hits = scan_paths(args.paths, project_root=project_root)
    if not hits:
        return 0
    print(
        f"fork-name purity check: {len(hits)} forbidden match(es)",
        file=sys.stderr,
    )
    for hit in hits:
        print(hit.render(project_root), file=sys.stderr)
    return 1


if __name__ == "__main__":  # pragma: no cover - direct CLI entry
    raise SystemExit(main())
