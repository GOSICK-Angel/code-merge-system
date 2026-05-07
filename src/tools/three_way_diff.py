from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from src.tools.git_tool import GitTool


def _safe_read_text(path: Path) -> str | None:
    """Read a file as UTF-8 text, returning None on binary / decode / IO errors.

    Defensive wrapper for merge-time file reads: binary assets (icons, lock
    files with BOMs, compiled bundles) coexist with source in the diff set.
    Returning None lets callers treat binary files as "no textual content"
    rather than crash the whole phase.
    """
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


@dataclass
class ThreeWayResult:
    file_path: str
    base_content: str | None
    upstream_content: str | None
    merged_content: str | None


class ThreeWayDiff:
    def __init__(self, git_tool: GitTool):
        self.git_tool = git_tool

    def compare(
        self, file_path: str, merge_base: str, upstream_ref: str
    ) -> ThreeWayResult:
        base_content = self.git_tool.get_file_content(merge_base, file_path)
        upstream_content = self.git_tool.get_file_content(upstream_ref, file_path)

        abs_path = self.git_tool.repo_path / file_path
        merged_content: str | None = None
        if abs_path.exists():
            merged_content = _safe_read_text(abs_path)

        return ThreeWayResult(
            file_path=file_path,
            base_content=base_content,
            upstream_content=upstream_content,
            merged_content=merged_content,
        )

    def verify_b_class(self, file_path: str, upstream_ref: str) -> bool:
        """Strict equality check: HEAD blob == upstream blob.

        Retained for callers / tests that want byte-exact verification.
        Most B-class checks should prefer ``verify_b_class_diff_applied``
        which tolerates fork-side customizations layered on top of
        upstream changes.
        """
        upstream_content = self.git_tool.get_file_content(upstream_ref, file_path)
        abs_path = self.git_tool.repo_path / file_path

        if upstream_content is None:
            return not abs_path.exists()

        if not abs_path.exists():
            return False

        merged_content = _safe_read_text(abs_path)
        if merged_content is None:
            return False
        return merged_content == upstream_content

    def verify_b_class_diff_applied(
        self, file_path: str, merge_base: str, upstream_ref: str
    ) -> bool:
        """Check that the upstream-vs-base diff is reflected in HEAD.

        For B-class files (modified on both sides), strict equality
        ``HEAD == upstream`` is too aggressive — a fork may legitimately
        layer additional changes on top of upstream's. The relaxed check
        verifies that every symbol upstream *added* (relative to
        merge-base) is present in the merged HEAD content, reusing the
        same signal C-class checks already use.

        Returns True when:
        * upstream did not change anything since merge-base (no symbols
          added) — nothing to verify;
        * all upstream-added symbols are present in HEAD.

        Returns False when upstream-added symbols are missing in HEAD,
        i.e. the upstream change was lost during the merge.
        """
        base_content = self.git_tool.get_file_content(merge_base, file_path)
        upstream_content = self.git_tool.get_file_content(upstream_ref, file_path)

        if upstream_content is None:
            abs_path = self.git_tool.repo_path / file_path
            return not abs_path.exists()

        base_symbols = _extract_symbols(base_content or "")
        upstream_symbols = _extract_symbols(upstream_content)
        added = sorted(upstream_symbols - base_symbols)

        if not added:
            return True

        missing = self.verify_additions_present(file_path, added)
        return not missing

    def verify_d_missing_present(self, file_path: str) -> bool:
        abs_path = self.git_tool.repo_path / file_path
        return abs_path.exists()

    def extract_upstream_additions(
        self, file_path: str, merge_base: str, upstream_ref: str
    ) -> list[str]:
        base_content = self.git_tool.get_file_content(merge_base, file_path)
        upstream_content = self.git_tool.get_file_content(upstream_ref, file_path)

        if upstream_content is None:
            return []

        base_symbols = _extract_symbols(base_content or "")
        upstream_symbols = _extract_symbols(upstream_content)

        return sorted(upstream_symbols - base_symbols)

    def verify_additions_present(
        self, file_path: str, additions: list[str]
    ) -> list[str]:
        abs_path = self.git_tool.repo_path / file_path
        if not abs_path.exists():
            return list(additions)

        merged_content = _safe_read_text(abs_path)
        if merged_content is None:
            return list(additions)
        merged_symbols = _extract_symbols(merged_content)

        return [name for name in additions if name not in merged_symbols]

    def count_todo_merge(self, file_path: str) -> int:
        abs_path = self.git_tool.repo_path / file_path
        if not abs_path.exists():
            return 0
        content = _safe_read_text(abs_path)
        if content is None:
            return 0
        return len(re.findall(r"TODO\s*\[merge\]", content))

    def find_todo_check(self, file_path: str) -> list[int]:
        abs_path = self.git_tool.repo_path / file_path
        if not abs_path.exists():
            return []
        raw = _safe_read_text(abs_path)
        if raw is None:
            return []
        lines = raw.splitlines()
        return [
            i + 1
            for i, line in enumerate(lines)
            if re.search(r"TODO\s*\[check\]", line)
        ]

    def extract_missing_top_level_invocations(
        self, file_path: str, merge_base: str, upstream_ref: str
    ) -> list[str]:
        """Return top-level invocations / decorators present in base∪upstream but
        absent from HEAD merged content.

        Detection targets (language-agnostic regex fallback):
          - Top-level call expressions: ``foo.bar(...)``
          - Decorators / annotations:    ``@app.route(...)``
        """
        base = self.git_tool.get_file_content(merge_base, file_path) or ""
        upstream = self.git_tool.get_file_content(upstream_ref, file_path) or ""
        abs_path = self.git_tool.repo_path / file_path
        merged = (_safe_read_text(abs_path) or "") if abs_path.exists() else ""

        expected = _extract_top_level_invocations(
            base
        ) | _extract_top_level_invocations(upstream)
        actual = _extract_top_level_invocations(merged)

        return sorted(expected - actual)


_SYMBOL_PATTERNS = [
    re.compile(r"^(?:async\s+)?def\s+(\w+)\s*\(", re.MULTILINE),
    re.compile(r"^class\s+(\w+)[\s(:]", re.MULTILINE),
    re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*[\(<]", re.MULTILINE),
    re.compile(
        r"^(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\(",
        re.MULTILINE,
    ),
]


def _extract_symbols(content: str) -> set[str]:
    symbols: set[str] = set()
    for pattern in _SYMBOL_PATTERNS:
        for match in pattern.finditer(content):
            symbols.add(match.group(1))
    return symbols


_TOP_LEVEL_CALL = re.compile(
    r"^(?P<call>\w+(?:\.\w+)*)\s*\([^)\n]*\)\s*;?\s*$",
    re.MULTILINE,
)
_TOP_LEVEL_DECORATOR = re.compile(
    r"^\s*@(?P<name>\w+(?:\.\w+)*)\s*(?:\([^)\n]*\))?\s*$",
    re.MULTILINE,
)

_NON_INVOCATION_HEADS = frozenset(
    {
        "if",
        "elif",
        "while",
        "for",
        "switch",
        "return",
        "yield",
        "await",
        "raise",
        "throw",
        "print",
        "assert",
        "not",
        "and",
        "or",
        "in",
        "is",
    }
)


def _extract_top_level_invocations(content: str) -> set[str]:
    """Extract top-level (column-0) call expressions and decorators.

    Heuristic regex fallback — intentionally conservative. Prefer AST-level
    extraction for P1; this layer only needs to catch the common bug where
    ``api.add_resource(...)`` / ``@blueprint.route(...)`` vanish after merge.
    """
    invocations: set[str] = set()

    for m in _TOP_LEVEL_CALL.finditer(content):
        name = m.group("call")
        head = name.split(".", 1)[0]
        if head in _NON_INVOCATION_HEADS:
            continue
        invocations.add(name)

    for m in _TOP_LEVEL_DECORATOR.finditer(content):
        invocations.add("@" + m.group("name"))

    return invocations
