"""P0-2 tests: detect elision placeholders before writing to disk.

The chunker emits ``# ... (N sections omitted)`` markers when staging large
files for the LLM. If the LLM echoes these markers back into its merged-output
reply (instead of producing the full file), the patch_applier currently writes
them to disk verbatim — silently truncating the file.

These tests pin the contract:
- A reusable detector in ``src/tools/elision_detector.py`` that matches the
  marker family (multiple comment prefixes).
- ``parse_merge_result`` raises ``ParseError`` when detected, so the executor
  routes to ``SEMANTIC_MERGE_FAILED → escalate_human`` instead of writing.
- ``apply_with_snapshot`` refuses to write when detected, returns
  ``ESCALATE_HUMAN``, and leaves the original file untouched.
"""

from __future__ import annotations

from pathlib import Path

import pytest


class TestElisionDetector:
    def test_python_style_marker_is_detected(self) -> None:
        from src.tools.elision_detector import has_elision

        content = "def foo():\n    pass\n\n# ... (3 sections omitted)\n\ndef bar():\n    pass\n"
        hit, sample = has_elision(content)
        assert hit is True
        assert sample is not None and "3 sections omitted" in sample

    def test_c_style_marker_is_detected(self) -> None:
        from src.tools.elision_detector import has_elision

        content = "func A() {}\n// ... (5 sections omitted)\nfunc B() {}\n"
        hit, _ = has_elision(content)
        assert hit is True

    def test_angle_bracket_marker_is_detected(self) -> None:
        from src.tools.elision_detector import has_elision

        content = "line1\n<... omitted ...>\nline2\n"
        hit, _ = has_elision(content)
        assert hit is True

    def test_elided_singular_is_detected(self) -> None:
        from src.tools.elision_detector import has_elision

        content = "x = 1\n# ... (elided)\ny = 2\n"
        hit, _ = has_elision(content)
        assert hit is True

    def test_normal_comment_is_not_flagged(self) -> None:
        from src.tools.elision_detector import has_elision

        content = "# This is a regular comment\n# ... and more text\nx = 1\n"
        hit, _ = has_elision(content)
        assert hit is False

    def test_ellipsis_alone_is_not_flagged(self) -> None:
        from src.tools.elision_detector import has_elision

        content = "items = [\n    1,\n    2,\n    # ...\n]\n"
        hit, _ = has_elision(content)
        assert hit is False

    def test_empty_content_is_not_flagged(self) -> None:
        from src.tools.elision_detector import has_elision

        hit, sample = has_elision("")
        assert hit is False
        assert sample is None


class TestParseMergeResultRejectsElision:
    def test_parse_merge_result_raises_on_elision(self) -> None:
        from src.llm.client import ParseError
        from src.llm.response_parser import parse_merge_result

        merged = (
            "package main\n\nfunc A() {}\n\n# ... (3 sections omitted)\n\nfunc B() {}\n"
        )
        with pytest.raises(ParseError, match="elision|omitted"):
            parse_merge_result(merged)

    def test_parse_merge_result_passes_clean_content(self) -> None:
        from src.llm.response_parser import parse_merge_result

        result = parse_merge_result("def f():\n    return 1\n")
        assert "def f()" in result

    def test_parse_merge_result_strips_fences_then_checks_elision(self) -> None:
        from src.llm.client import ParseError
        from src.llm.response_parser import parse_merge_result

        fenced = "```python\ndef x():\n    pass\n# ... (2 sections omitted)\n```\n"
        with pytest.raises(ParseError):
            parse_merge_result(fenced)


class TestPatchApplierRejectsElision:
    @pytest.mark.asyncio
    async def test_apply_with_snapshot_does_not_write_elision(
        self, tmp_path: Path
    ) -> None:
        from unittest.mock import MagicMock

        from src.models.decision import MergeDecision
        from src.models.state import MergeState
        from src.tools.patch_applier import apply_with_snapshot

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        target = repo_dir / "service.go"
        original = "package main\n\nfunc Original() {}\n"
        target.write_text(original)

        git_tool = MagicMock()
        git_tool.repo_path = repo_dir
        git_tool.get_worktree_blob_sha.return_value = "irrelevant"

        state = MagicMock(spec=MergeState)

        elided_content = (
            "package main\n\nfunc A() {}\n\n# ... (4 sections omitted)\n\nfunc Z() {}\n"
        )
        record = await apply_with_snapshot(
            "service.go",
            elided_content,
            git_tool,
            state,
            phase="auto_merge",
            agent="executor",
            decision=MergeDecision.SEMANTIC_MERGE,
            rationale="test",
        )

        assert record.decision == MergeDecision.ESCALATE_HUMAN
        assert (
            "elision" in record.rationale.lower()
            or "omitted" in record.rationale.lower()
        )
        assert target.read_text() == original
