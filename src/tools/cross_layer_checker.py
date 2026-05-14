"""P0-4: CrossLayerIntegrityChecker.

Enforces declarative cross-layer key consistency. Given a list of
``CrossLayerAssertion``:

    keys_from = "<source_file>::<regex with group(1) as key>"
    keys_in   = [target_file_1, target_file_2, ...]
    allow_missing = ["KeyA", ...]  # exempted

For every captured key that's NOT in ``allow_missing``, verify the literal
string appears at least once in each target file. Missing keys surface as
``CrossLayerAssertionResult.missing_keys`` for the Judge deterministic
pipeline.

Purely config-driven — no hardcoded patterns.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from pydantic import BaseModel, Field

from src.models.config import CrossLayerAssertion
from src.tools.conflict_markers import safe_read_text


class CrossLayerAssertionResult(BaseModel):
    assertion_name: str
    source_file: str = ""
    target_files: list[str] = Field(default_factory=list)
    captured_keys: list[str] = Field(default_factory=list)
    missing_keys: list[str] = Field(default_factory=list)
    error: str = ""


class CrossLayerChecker:
    def __init__(self, repo_path: Path | str):
        self.repo_path = Path(repo_path)

    def check(
        self, assertions: Iterable[CrossLayerAssertion]
    ) -> list[CrossLayerAssertionResult]:
        return [self._check_one(a) for a in assertions]

    def _check_one(self, assertion: CrossLayerAssertion) -> CrossLayerAssertionResult:
        src_file, sep, pattern = assertion.keys_from.partition("::")
        if not sep or not pattern:
            return CrossLayerAssertionResult(
                assertion_name=assertion.name,
                error=(
                    f"Invalid keys_from spec: '{assertion.keys_from}' "
                    "(expected '<file>::<regex>')"
                ),
            )

        source_path = self.repo_path / src_file
        if not source_path.exists():
            return CrossLayerAssertionResult(
                assertion_name=assertion.name,
                source_file=src_file,
                target_files=list(assertion.keys_in),
                error=f"Source file not found: {src_file}",
            )

        try:
            compiled = re.compile(pattern, re.MULTILINE)
        except re.error as e:
            return CrossLayerAssertionResult(
                assertion_name=assertion.name,
                source_file=src_file,
                error=f"Invalid regex: {e}",
            )

        source_content = safe_read_text(source_path)
        if source_content is None:
            return CrossLayerAssertionResult(
                assertion_name=assertion.name,
                source_file=src_file,
                target_files=list(assertion.keys_in),
                error=(f"Source file is binary or unreadable: {src_file}"),
            )
        captured: set[str] = set()
        for m in compiled.finditer(source_content):
            if not m.groups():
                continue
            key = m.group(1)
            if key:
                captured.add(key)

        allow_missing = set(assertion.allow_missing)
        keys_to_check = sorted(captured - allow_missing)

        missing: set[str] = set()
        checked_targets: list[str] = []
        for target in assertion.keys_in:
            tgt_path = self.repo_path / target
            checked_targets.append(target)
            if not tgt_path.exists():
                missing.update(keys_to_check)
                continue
            tgt_content = safe_read_text(tgt_path)
            if tgt_content is None:
                missing.update(keys_to_check)
                continue
            for key in keys_to_check:
                if key not in tgt_content:
                    missing.add(key)

        return CrossLayerAssertionResult(
            assertion_name=assertion.name,
            source_file=src_file,
            target_files=checked_targets,
            captured_keys=sorted(captured),
            missing_keys=sorted(missing),
        )
