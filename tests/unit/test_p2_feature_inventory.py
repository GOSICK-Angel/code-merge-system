"""P2-2 tests: merge_report.md Feature Inventory section.

After P2-1 auto-learns customizations from fork-only feature commits, the
human-facing report needs to surface what was audited and whether each
audited customization survived. The new "Feature Inventory" section lists
every ``state.config.customizations`` entry alongside its verification
status (PASS / FAIL / NOT_CHECKED), grouped implicitly by ``source``
(manual vs scar_learned).

Contract:
- Section header is ``Feature Inventory`` (en) / ``特性清单`` (zh).
- Empty ``customizations`` list => section is omitted entirely.
- A customization whose name appears in
  ``state.judge_verdict.customization_violations`` => ``FAIL`` row.
- Otherwise, when ``judge_verdict`` is non-None => ``PASS``.
- ``judge_verdict is None`` => ``NOT_CHECKED``.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from src.models.config import (
    CustomizationEntry,
    CustomizationVerification,
    MergeConfig,
)
from src.models.judge import (
    CustomizationViolation,
    JudgeVerdict,
    VerdictType,
)
from src.models.state import MergeState
from src.tools.report_writer import write_markdown_report


def _make_verdict(
    violations: list[CustomizationViolation] | None = None,
) -> JudgeVerdict:
    return JudgeVerdict(
        verdict=VerdictType.PASS,
        reviewed_files_count=1,
        passed_files=["x.py"],
        failed_files=[],
        conditional_files=[],
        issues=[],
        critical_issues_count=0,
        high_issues_count=0,
        overall_confidence=0.9,
        summary="ok",
        blocking_issues=[],
        timestamp=datetime.now(),
        judge_model="test",
        customization_violations=violations or [],
    )


def _make_state(
    customizations: list[CustomizationEntry],
    verdict: JudgeVerdict | None = None,
) -> MergeState:
    config = MergeConfig(
        upstream_ref="upstream/main",
        fork_ref="feature/fork",
        customizations=customizations,
    )
    state = MergeState(config=config)
    state.judge_verdict = verdict
    return state


def _entry(
    name: str,
    files: list[str],
    *,
    source: str = "manual",
) -> CustomizationEntry:
    return CustomizationEntry(
        name=name,
        files=files,
        verification=[CustomizationVerification(type="file_exists", files=files)],
        source=source,  # type: ignore[arg-type]
    )


def _read_report(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestSectionPresence:
    def test_no_customizations_no_section(self, tmp_path: Path) -> None:
        state = _make_state([], _make_verdict())
        path = write_markdown_report(state, str(tmp_path))
        body = _read_report(path)
        assert "Feature Inventory" not in body
        assert "特性清单" not in body

    def test_section_appears_when_customizations_present(self, tmp_path: Path) -> None:
        state = _make_state([_entry("Cvte SSO", ["src/auth/cvte.py"])], _make_verdict())
        path = write_markdown_report(state, str(tmp_path))
        body = _read_report(path)
        assert ("Feature Inventory" in body) or ("特性清单" in body)


class TestStatusRendering:
    def test_pass_when_judge_verdict_no_violation(self, tmp_path: Path) -> None:
        state = _make_state([_entry("Cvte SSO", ["src/auth/cvte.py"])], _make_verdict())
        path = write_markdown_report(state, str(tmp_path))
        body = _read_report(path)
        assert "Cvte SSO" in body
        assert "PASS" in body

    def test_fail_when_violation_matches(self, tmp_path: Path) -> None:
        violation = CustomizationViolation(
            customization_name="Cvte SSO",
            verification_type="file_exists",
            expected_pattern="src/auth/cvte.py exists",
            checked_files=["src/auth/cvte.py"],
            match_count=0,
        )
        state = _make_state(
            [_entry("Cvte SSO", ["src/auth/cvte.py"])],
            _make_verdict([violation]),
        )
        path = write_markdown_report(state, str(tmp_path))
        body = _read_report(path)
        assert "FAIL" in body

    def test_not_checked_when_no_judge_verdict(self, tmp_path: Path) -> None:
        state = _make_state([_entry("Cvte SSO", ["src/auth/cvte.py"])], None)
        path = write_markdown_report(state, str(tmp_path))
        body = _read_report(path)
        assert "NOT_CHECKED" in body

    def test_mixed_pass_and_fail(self, tmp_path: Path) -> None:
        violation = CustomizationViolation(
            customization_name="Lost Feature",
            verification_type="file_exists",
            expected_pattern="src/lost.py exists",
            checked_files=["src/lost.py"],
            match_count=0,
        )
        state = _make_state(
            [
                _entry("Surviving Feature", ["src/keep.py"]),
                _entry("Lost Feature", ["src/lost.py"]),
            ],
            _make_verdict([violation]),
        )
        path = write_markdown_report(state, str(tmp_path))
        body = _read_report(path)
        assert "Surviving Feature" in body
        assert "Lost Feature" in body
        assert body.count("PASS") >= 1
        assert body.count("FAIL") >= 1


class TestSourceColumn:
    def test_manual_and_scar_learned_distinguishable(self, tmp_path: Path) -> None:
        state = _make_state(
            [
                _entry("Manual Feature", ["src/m.py"], source="manual"),
                _entry(
                    "Auto-learned Feature",
                    ["src/a.py"],
                    source="scar_learned",
                ),
            ],
            _make_verdict(),
        )
        path = write_markdown_report(state, str(tmp_path))
        body = _read_report(path)
        assert "manual" in body.lower()
        assert "scar_learned" in body.lower()


class TestZhI18n:
    def test_zh_section_title(self, tmp_path: Path) -> None:
        config = MergeConfig(
            upstream_ref="upstream/main",
            fork_ref="feature/fork",
            customizations=[_entry("功能 A", ["src/a.py"])],
        )
        config.output.language = "zh"
        state = MergeState(config=config)
        state.judge_verdict = _make_verdict()
        path = write_markdown_report(state, str(tmp_path))
        body = _read_report(path)
        assert "特性清单" in body


class TestSectionOrder:
    def test_section_after_judge_verdict(self, tmp_path: Path) -> None:
        """Inventory must come after judge verdict so the reader sees the
        verdict context first, then the customization-level breakdown."""
        state = _make_state([_entry("Cvte SSO", ["src/auth/cvte.py"])], _make_verdict())
        path = write_markdown_report(state, str(tmp_path))
        body = _read_report(path)
        verdict_idx = body.find("Judge Verdict")
        inventory_idx = body.find("Feature Inventory")
        assert verdict_idx >= 0
        assert inventory_idx > verdict_idx
