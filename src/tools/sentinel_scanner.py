"""P2-2: SentinelScanner — detect fork-customization markers in file content.

When the Executor is about to process an AUTO_SAFE file, this scanner checks
whether the fork's version contains annotation-style markers that signal
"this file has fork-only customizations — do not auto-overwrite".

Design principles:
- DEFAULT_SENTINELS contains only language/project-agnostic annotation markers.
- Project-specific sentinels (business terms, SSO names, etc.) are injected via
  ``MergeConfig.sentinels_extra``.
- Zero repository-specific knowledge in this file.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from src.models.diff import FileDiff


FORK_DELTA_PATTERN: str = "__fork_delta_threshold__"
DEFAULT_FORK_DELTA_MIN_LINES: int = 50


DEFAULT_SENTINELS: list[str] = [
    r"#\s*Current branch enhancement",
    r"#\s*TODO\s*\[merge\]",
    r"#\s*Merged from upstream",
    r"<<<<<<<",
    r"=======",
    r">>>>>>>",
    r"@fork-only",
    r"@do-not-remove",
]


class SentinelHit(BaseModel):
    """A single sentinel pattern match within a file."""

    file_path: str
    line_number: int
    pattern: str
    matched_text: str

    model_config = {"frozen": True}


class SentinelScanner:
    """Scan file content (or files on disk) for sentinel markers.

    Args:
        sentinels: Base sentinel patterns (defaults to ``DEFAULT_SENTINELS``).
        extra: Additional patterns from ``MergeConfig.sentinels_extra``.
    """

    def __init__(
        self,
        sentinels: list[str] | None = None,
        extra: list[str] | None = None,
    ) -> None:
        base = sentinels if sentinels is not None else DEFAULT_SENTINELS
        all_patterns = list(base) + list(extra or [])
        self._compiled: list[tuple[str, re.Pattern[str]]] = [
            (p, re.compile(p)) for p in all_patterns
        ]

    @classmethod
    def from_config_extras(cls, extras: list[str]) -> "SentinelScanner":
        """Build a scanner with DEFAULT_SENTINELS plus project-specific extras."""
        return cls(extra=extras)

    def scan(self, content: str, file_path: str = "") -> list[SentinelHit]:
        """Scan *content* string for sentinel patterns.

        Args:
            content: Text content to scan (fork version of a file).
            file_path: Used only as metadata in SentinelHit; not read from disk.

        Returns:
            List of SentinelHit objects; empty if no sentinels found.
        """
        hits: list[SentinelHit] = []
        for lineno, line in enumerate(content.splitlines(), start=1):
            for pattern_str, compiled in self._compiled:
                if compiled.search(line):
                    hits.append(
                        SentinelHit(
                            file_path=file_path,
                            line_number=lineno,
                            pattern=pattern_str,
                            matched_text=line.rstrip(),
                        )
                    )
                    break
        return hits

    def check_fork_delta(
        self,
        file_diff: "FileDiff | None",
        *,
        min_lines: int = DEFAULT_FORK_DELTA_MIN_LINES,
    ) -> list[SentinelHit]:
        """P1-2: emit a synthetic hit when fork-side line delta crosses
        ``min_lines``. Complements text-marker scanning: a fork file that
        diverged 50+ lines from merge_base is customized regardless of
        whether anyone wrote ``@fork-only``.

        Returns at most one hit so downstream plan-dispute logic stays
        symmetric with text-marker hits.
        """
        if file_diff is None:
            return []
        delta = file_diff.lines_added + file_diff.lines_deleted
        if delta < min_lines:
            return []
        return [
            SentinelHit(
                file_path=file_diff.file_path,
                line_number=0,
                pattern=FORK_DELTA_PATTERN,
                matched_text=(
                    f"fork delta {delta} lines >= {min_lines} (customization signal)"
                ),
            )
        ]

    def scan_file(self, file_path: Path) -> list[SentinelHit]:
        """Read *file_path* from disk and scan its content.

        Returns an empty list if the file cannot be read.
        """
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        return self.scan(content, str(file_path))
