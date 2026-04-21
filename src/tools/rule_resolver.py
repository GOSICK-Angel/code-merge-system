"""Rule-based pre-resolution for common conflict patterns.

Attempts deterministic resolution before invoking LLM agents.
Inspired by merge-engine's pattern DSL approach (ICSE 2021).

Supported patterns:
  1. WHITESPACE_ONLY — normalize and compare; if identical after strip, take target.
  2. IDENTICAL_CHANGE — both sides made the same change; take either.
  3. IMPORT_UNION — both sides added different imports; merge import lists.
  4. ADJACENT_EDIT — non-overlapping changes in distinct line ranges; combine.
"""

from __future__ import annotations

import logging
from enum import Enum

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class RulePattern(str, Enum):
    WHITESPACE_ONLY = "whitespace_only"
    IDENTICAL_CHANGE = "identical_change"
    IMPORT_UNION = "import_union"
    ADJACENT_EDIT = "adjacent_edit"
    LINE_ADDITION_UNION = "line_addition_union"


class RuleResolution(BaseModel):
    resolved: bool = False
    pattern: RulePattern | None = None
    merged_content: str = ""
    confidence: float = 0.0
    description: str = ""


_IMPORT_KEYWORDS = frozenset(
    {
        "import ",
        "from ",
        "#include ",
        "require(",
        "require ",
        "using ",
        "use ",
        "@import ",
    }
)


class RuleBasedResolver:
    """Try deterministic resolution for trivially-resolvable conflicts."""

    def try_resolve(
        self,
        base_content: str | None,
        current_content: str | None,
        target_content: str | None,
    ) -> RuleResolution:
        if base_content is None or current_content is None or target_content is None:
            return RuleResolution()

        if current_content == target_content:
            return RuleResolution(
                resolved=True,
                pattern=RulePattern.IDENTICAL_CHANGE,
                merged_content=target_content,
                confidence=1.0,
                description="Both sides made the identical change",
            )

        result = self._try_whitespace_only(
            base_content, current_content, target_content
        )
        if result.resolved:
            return result

        result = self._try_import_union(base_content, current_content, target_content)
        if result.resolved:
            return result

        result = self._try_adjacent_edit(base_content, current_content, target_content)
        if result.resolved:
            return result

        result = self._try_line_addition_union(
            base_content, current_content, target_content
        )
        if result.resolved:
            return result

        return RuleResolution()

    @staticmethod
    def _try_whitespace_only(base: str, current: str, target: str) -> RuleResolution:
        def _normalize(text: str) -> str:
            return "\n".join(line.rstrip() for line in text.splitlines())

        norm_base = _normalize(base)
        norm_current = _normalize(current)
        norm_target = _normalize(target)

        if norm_current == norm_target and current != target:
            return RuleResolution(
                resolved=True,
                pattern=RulePattern.WHITESPACE_ONLY,
                merged_content=target,
                confidence=0.95,
                description="Changes differ only in trailing whitespace",
            )

        if norm_current == norm_base and norm_target != norm_base:
            return RuleResolution(
                resolved=True,
                pattern=RulePattern.WHITESPACE_ONLY,
                merged_content=target,
                confidence=0.95,
                description="Current only has whitespace changes; take target",
            )

        if norm_target == norm_base and norm_current != norm_base:
            return RuleResolution(
                resolved=True,
                pattern=RulePattern.WHITESPACE_ONLY,
                merged_content=current,
                confidence=0.95,
                description="Target only has whitespace changes; keep current",
            )

        return RuleResolution()

    @staticmethod
    def _try_import_union(base: str, current: str, target: str) -> RuleResolution:
        base_lines = base.splitlines()
        current_lines = current.splitlines()
        target_lines = target.splitlines()

        def _is_import(line: str) -> bool:
            stripped = line.lstrip()
            return any(stripped.startswith(kw) for kw in _IMPORT_KEYWORDS)

        def _extract_import_block(
            lines: list[str],
        ) -> tuple[int, int, list[str], list[str]]:
            start = -1
            end = -1
            imports: list[str] = []
            rest: list[str] = []
            for i, line in enumerate(lines):
                if _is_import(line):
                    if start < 0:
                        start = i
                    end = i
                    imports.append(line)
                else:
                    rest.append(line)
            return start, end, imports, rest

        b_start, b_end, b_imports, b_rest = _extract_import_block(base_lines)
        c_start, c_end, c_imports, c_rest = _extract_import_block(current_lines)
        t_start, t_end, t_imports, t_rest = _extract_import_block(target_lines)

        if not b_imports and not c_imports and not t_imports:
            return RuleResolution()

        if c_rest == b_rest and t_rest == b_rest:
            pass
        elif c_rest != b_rest or t_rest != b_rest:
            return RuleResolution()

        c_added = set(c_imports) - set(b_imports)
        t_added = set(t_imports) - set(b_imports)
        c_removed = set(b_imports) - set(c_imports)
        t_removed = set(b_imports) - set(t_imports)

        if not c_added and not t_added and not c_removed and not t_removed:
            return RuleResolution()

        if c_removed or t_removed:
            return RuleResolution()

        merged_imports = sorted(
            set(c_imports) | set(t_imports),
            key=lambda x: x.lstrip(),
        )

        merged_lines: list[str] = []
        import_inserted = False

        for i, line in enumerate(base_lines):
            if _is_import(line):
                if not import_inserted:
                    merged_lines.extend(merged_imports)
                    import_inserted = True
                continue
            merged_lines.append(line)

        if not import_inserted:
            merged_lines = merged_imports + merged_lines

        return RuleResolution(
            resolved=True,
            pattern=RulePattern.IMPORT_UNION,
            merged_content="\n".join(merged_lines),
            confidence=0.90,
            description=f"Merged import blocks: +{len(c_added | t_added)} imports",
        )

    @staticmethod
    def _try_adjacent_edit(base: str, current: str, target: str) -> RuleResolution:
        base_lines = base.splitlines()
        current_lines = current.splitlines()
        target_lines = target.splitlines()

        if len(base_lines) != len(current_lines) or len(base_lines) != len(
            target_lines
        ):
            return RuleResolution()

        current_changed: set[int] = set()
        target_changed: set[int] = set()

        for i in range(len(base_lines)):
            if base_lines[i] != current_lines[i]:
                current_changed.add(i)
            if base_lines[i] != target_lines[i]:
                target_changed.add(i)

        if not current_changed or not target_changed:
            return RuleResolution()

        if current_changed & target_changed:
            return RuleResolution()

        merged = list(base_lines)
        for i in current_changed:
            merged[i] = current_lines[i]
        for i in target_changed:
            merged[i] = target_lines[i]

        return RuleResolution(
            resolved=True,
            pattern=RulePattern.ADJACENT_EDIT,
            merged_content="\n".join(merged),
            confidence=0.85,
            description=(
                f"Non-overlapping edits: {len(current_changed)} current, "
                f"{len(target_changed)} target lines"
            ),
        )

    @staticmethod
    def _try_line_addition_union(
        base: str, current: str, target: str
    ) -> RuleResolution:
        """Both sides only added lines to base (no removals); merge as union.

        Handles requirements.txt, plain text lists, and similar additive files
        where both the fork and upstream appended distinct entries.
        """
        base_lines = base.splitlines()
        current_lines = current.splitlines()
        target_lines = target.splitlines()

        base_set = set(base_lines)
        current_set = set(current_lines)
        target_set = set(target_lines)

        if not base_set.issubset(current_set):
            return RuleResolution()

        if not base_set.issubset(target_set):
            return RuleResolution()

        current_added = [ln for ln in current_lines if ln not in base_set]
        target_added = [ln for ln in target_lines if ln not in base_set]

        if not current_added and not target_added:
            return RuleResolution()

        if set(current_added) & set(target_added):
            return RuleResolution()

        seen: set[str] = set()
        merged: list[str] = []
        for ln in base_lines:
            if ln not in seen:
                merged.append(ln)
                seen.add(ln)
        for ln in current_lines:
            if ln not in seen:
                merged.append(ln)
                seen.add(ln)
        for ln in target_lines:
            if ln not in seen:
                merged.append(ln)
                seen.add(ln)

        return RuleResolution(
            resolved=True,
            pattern=RulePattern.LINE_ADDITION_UNION,
            merged_content="\n".join(merged),
            confidence=0.88,
            description=(
                f"Both sides only added lines: "
                f"+{len(current_added)} current, +{len(target_added)} target"
            ),
        )
