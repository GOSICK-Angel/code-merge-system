"""P2-3: ConfigLineRetentionChecker — enforce required lines in CI/env/docker files.

After the merge, verifies that files matching user-supplied globs still contain
all required line patterns (regex).  Violations are surfaced as Judge VETO
``config_retention_violation`` and block progression to later phases.

Design principles:
- No default rules — every rule is user-supplied via ``MergeConfig.config_retention``.
- Rules reference files by glob; checker walks the working tree at check time.
- Zero repo-specific knowledge in this file.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

from pydantic import BaseModel

from src.models.config import ConfigRetentionRule


class ConfigRetentionViolation(BaseModel):
    """Describes one required-line pattern that is absent from a matched file."""

    rule_file_glob: str
    file_path: str
    missing_patterns: list[str]

    model_config = {"frozen": True}


class ConfigLineRetentionChecker:
    """Check that files matching each rule's glob contain all required_lines patterns.

    Args:
        repo_path: Absolute path to the repository working tree root.
    """

    def __init__(self, repo_path: Path) -> None:
        self._repo_path = repo_path

    def check(
        self,
        rules: list[ConfigRetentionRule],
    ) -> list[ConfigRetentionViolation]:
        """Run all retention rules against the current working tree.

        Args:
            rules: List of ``ConfigRetentionRule`` from
                   ``MergeConfig.config_retention.rules``.

        Returns:
            List of violations; empty if all rules pass.
        """
        violations: list[ConfigRetentionViolation] = []

        all_files = [
            str(p.relative_to(self._repo_path))
            for p in self._repo_path.rglob("*")
            if p.is_file()
        ]

        for rule in rules:
            matched_files = [
                fp for fp in all_files if fnmatch.fnmatch(fp, rule.file_glob)
            ]

            for fp in matched_files:
                abs_path = self._repo_path / fp
                try:
                    content = abs_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    content = ""

                missing = self._find_missing_patterns(content, rule.required_lines)
                if missing:
                    violations.append(
                        ConfigRetentionViolation(
                            rule_file_glob=rule.file_glob,
                            file_path=fp,
                            missing_patterns=missing,
                        )
                    )

        return violations

    @staticmethod
    def _find_missing_patterns(content: str, patterns: list[str]) -> list[str]:
        """Return patterns from *patterns* that do not match any line in *content*."""
        missing: list[str] = []
        lines = content.splitlines()
        for pattern in patterns:
            try:
                compiled = re.compile(pattern)
            except re.error:
                compiled = re.compile(re.escape(pattern))
            if not any(compiled.search(line) for line in lines):
                missing.append(pattern)
        return missing
