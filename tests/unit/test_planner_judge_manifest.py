"""Regression for the C-class section of the PlannerJudge file manifest.

Pre-merge, C-class files have conflict_count=0 (no markers exist yet).
The manifest now exposes both-side line deltas and fork-side hunk
regions so Judge has semantic evidence beyond the path name when
applying Rule 6 ("flag C-class auto_risky in auth/permission dirs").
"""

from __future__ import annotations

from datetime import datetime

from src.models.diff import (
    DiffHunk,
    FileChangeCategory,
    FileDiff,
    FileStatus,
    RiskLevel,
)
from src.models.plan import MergePlan, RiskSummary
from src.llm.prompts.planner_judge_prompts import _build_file_manifest


def _empty_plan() -> MergePlan:
    return MergePlan(
        created_at=datetime.now(),
        upstream_ref="up",
        fork_ref="fork",
        merge_base_commit="abc",
        phases=[],
        risk_summary=RiskSummary(
            total_files=0,
            auto_safe_count=0,
            auto_risky_count=0,
            human_required_count=0,
            deleted_only_count=0,
            binary_count=0,
            excluded_count=0,
            estimated_auto_merge_rate=0.0,
        ),
        project_context_summary="",
    )


def _hunk(start: int, end: int) -> DiffHunk:
    return DiffHunk(
        hunk_id=f"h-{start}-{end}",
        start_line_current=start,
        end_line_current=end,
        start_line_target=start,
        end_line_target=end,
        content_current="",
        content_target="",
        content_base=None,
        has_conflict=False,
    )


def _fd(
    file_path: str,
    *,
    change_category: FileChangeCategory | None,
    risk_level: RiskLevel = RiskLevel.AUTO_RISKY,
    lines_added: int = 12,
    lines_deleted: int = 3,
    upstream_lines_added: int = 8,
    upstream_lines_deleted: int = 2,
    hunks: list[DiffHunk] | None = None,
) -> FileDiff:
    return FileDiff(
        file_path=file_path,
        file_status=FileStatus.MODIFIED,
        risk_level=risk_level,
        risk_score=0.5,
        lines_added=lines_added,
        lines_deleted=lines_deleted,
        upstream_lines_added=upstream_lines_added,
        upstream_lines_deleted=upstream_lines_deleted,
        change_category=change_category,
        hunks=hunks or [],
    )


class TestManifestCClassHunkOverlap:
    def test_c_class_line_emits_fork_and_upstream_deltas(self):
        fd = _fd(
            "models/user/user.go",
            change_category=FileChangeCategory.C,
            lines_added=12,
            lines_deleted=3,
            upstream_lines_added=8,
            upstream_lines_deleted=2,
            hunks=[_hunk(10, 20)],
        )
        text = _build_file_manifest([fd])
        assert "fork=+12/-3" in text
        assert "upstream=+8/-2" in text
        assert "[C," in text or ", C," in text or "[C]" in text

    def test_c_class_emits_fork_regions(self):
        fd = _fd(
            "models/auth/auth_token.go",
            change_category=FileChangeCategory.C,
            hunks=[_hunk(10, 20), _hunk(45, 60)],
        )
        text = _build_file_manifest([fd])
        assert "regions=10-20;45-60" in text

    def test_c_class_truncates_to_three_regions(self):
        fd = _fd(
            "routers/web/auth/oauth.go",
            change_category=FileChangeCategory.C,
            hunks=[
                _hunk(10, 12),
                _hunk(30, 35),
                _hunk(50, 60),
                _hunk(80, 88),
                _hunk(100, 110),
            ],
        )
        text = _build_file_manifest([fd])
        assert "regions=10-12;30-35;50-60;+2" in text

    def test_b_class_does_not_emit_c_class_flags(self):
        fd = _fd(
            "templates/user/profile.tmpl",
            change_category=FileChangeCategory.B,
            lines_added=5,
            lines_deleted=2,
            hunks=[_hunk(1, 5)],
        )
        text = _build_file_manifest([fd])
        assert "fork=" not in text
        assert "upstream=" not in text
        assert "regions=" not in text

    def test_c_class_with_no_hunks_omits_regions(self):
        fd = _fd(
            "models/user/user.go",
            change_category=FileChangeCategory.C,
            hunks=[],
        )
        text = _build_file_manifest([fd])
        assert "fork=+12/-3" in text
        assert "regions=" not in text


class TestRule6PathVocabulary:
    """Rule 6 in the PlannerJudge review prompt must list enough
    security-adjacent path tokens to actually trigger on real-world
    fork dirs (auth/user/oauth/session/permission/...). The pre-expansion
    text only mentioned "auth/crypto/permission" which left dirs like
    ``models/user/`` and ``services/session/`` invisible to the rule.
    """

    def _expected_tokens(self) -> list[str]:
        return [
            "auth",
            "token",
            "user",
            "permission",
            "session",
            "oauth",
            "credential",
            "password",
            "signin",
            "signup",
            "login",
            "otp",
            "secret",
            "signature",
        ]

    def test_full_prompt_lists_expected_tokens(self):
        from src.llm.prompts.planner_judge_prompts import build_plan_review_prompt

        plan = _empty_plan()
        prompt = build_plan_review_prompt(plan, [], lang="en", revision_round=0)
        for token in self._expected_tokens():
            assert token in prompt, f"Rule 6 must list {token!r}"

    def test_segment_prompt_lists_expected_tokens(self):
        from src.llm.prompts.planner_judge_prompts import (
            build_segment_plan_review_prompt,
        )

        plan = _empty_plan()
        prompt = build_segment_plan_review_prompt(
            plan,
            [],
            segment_idx=0,
            total_segments=1,
            total_files=0,
            lang="en",
        )
        for token in self._expected_tokens():
            assert token in prompt, f"Segment Rule 6 must list {token!r}"

    def test_rule_requires_hunk_evidence_not_path_alone(self):
        from src.llm.prompts.planner_judge_prompts import build_plan_review_prompt

        plan = _empty_plan()
        prompt = build_plan_review_prompt(plan, [], lang="en", revision_round=0)
        assert (
            "Pure path-name match without supporting hunk evidence is NOT sufficient"
            in prompt
        )


class TestPerFileSafelistFilter:
    """`filter_obviously_safe_files` partitions a segment per-file so a
    future split-send pass can keep safelist-clean files out of the LLM
    payload. Verifies the partition preserves order, recognizes
    lockfiles / SAFELIST patterns / extra patterns, and rejects files
    with conflicts, security flags, or risk keywords in the path.
    """

    def _batch_risk_map_for(self, *paths: str) -> dict[str, str]:
        return {p: "auto_safe" for p in paths}

    def _make_fd(
        self,
        path: str,
        *,
        lines_added: int = 5,
        lines_deleted: int = 2,
        conflict_count: int = 0,
        is_security_sensitive: bool = False,
        risk_level: RiskLevel = RiskLevel.AUTO_SAFE,
    ) -> FileDiff:
        return FileDiff(
            file_path=path,
            file_status=FileStatus.MODIFIED,
            risk_level=risk_level,
            risk_score=0.2,
            lines_added=lines_added,
            lines_deleted=lines_deleted,
            conflict_count=conflict_count,
            is_security_sensitive=is_security_sensitive,
        )

    def test_partitions_lockfile_and_source_file(self):
        from src.llm.prompts.planner_judge_prompts import (
            filter_obviously_safe_files,
        )

        files = [
            self._make_fd("poetry.lock", lines_added=20, lines_deleted=5),
            self._make_fd("src/auth/login.py", lines_added=200, lines_deleted=100),
        ]
        batch = self._batch_risk_map_for("poetry.lock", "src/auth/login.py")

        safe, needs_llm = filter_obviously_safe_files(files, batch)

        assert [fd.file_path for fd in safe] == ["poetry.lock"]
        assert [fd.file_path for fd in needs_llm] == ["src/auth/login.py"]

    def test_file_with_conflict_goes_to_llm(self):
        from src.llm.prompts.planner_judge_prompts import (
            filter_obviously_safe_files,
        )

        files = [
            self._make_fd(
                "package.json", lines_added=5, lines_deleted=2, conflict_count=1
            ),
        ]
        batch = self._batch_risk_map_for("package.json")

        safe, needs_llm = filter_obviously_safe_files(files, batch)

        assert safe == []
        assert [fd.file_path for fd in needs_llm] == ["package.json"]

    def test_security_sensitive_file_goes_to_llm(self):
        from src.llm.prompts.planner_judge_prompts import (
            filter_obviously_safe_files,
        )

        files = [
            self._make_fd(
                "config/app.yaml",
                lines_added=2,
                lines_deleted=1,
                is_security_sensitive=True,
            ),
        ]
        batch = self._batch_risk_map_for("config/app.yaml")

        safe, needs_llm = filter_obviously_safe_files(files, batch)

        assert safe == []
        assert [fd.file_path for fd in needs_llm] == ["config/app.yaml"]

    def test_extra_safelist_pattern_honored(self):
        from src.llm.prompts.planner_judge_prompts import (
            filter_obviously_safe_files,
        )

        files = [
            self._make_fd("docs/notes.adoc", lines_added=300, lines_deleted=100),
        ]
        batch = self._batch_risk_map_for("docs/notes.adoc")

        safe, needs_llm = filter_obviously_safe_files(
            files, batch, extra_safelist_patterns=["docs/**/*.adoc"]
        )

        assert [fd.file_path for fd in safe] == ["docs/notes.adoc"]
        assert needs_llm == []

    def test_not_batched_file_goes_to_llm(self):
        from src.llm.prompts.planner_judge_prompts import (
            filter_obviously_safe_files,
        )

        files = [self._make_fd("ghost.md")]
        # ghost.md is intentionally not in the batch_risk_map → must
        # NOT be considered safe (NOT-BATCHED signal must reach Judge).
        safe, needs_llm = filter_obviously_safe_files(files, batch_risk_map={})
        assert safe == []
        assert [fd.file_path for fd in needs_llm] == ["ghost.md"]

    def test_preserves_input_order_within_partitions(self):
        from src.llm.prompts.planner_judge_prompts import (
            filter_obviously_safe_files,
        )

        files = [
            self._make_fd("docs/a.md"),
            self._make_fd("src/auth/login.py"),
            self._make_fd("CHANGELOG.md"),
            self._make_fd("src/parser.go"),
        ]
        batch = self._batch_risk_map_for(
            "docs/a.md", "src/auth/login.py", "CHANGELOG.md", "src/parser.go"
        )

        safe, needs_llm = filter_obviously_safe_files(files, batch)

        assert [fd.file_path for fd in safe] == ["docs/a.md", "CHANGELOG.md"]
        assert [fd.file_path for fd in needs_llm] == [
            "src/auth/login.py",
            "src/parser.go",
        ]

    def test_is_segment_safe_still_consistent_with_per_file(self):
        # Whole-segment short-circuit must continue to agree with the
        # per-file predicate: True iff filter returns empty needs_llm.
        from src.llm.prompts.planner_judge_prompts import (
            filter_obviously_safe_files,
            is_segment_obviously_safe,
        )

        files = [self._make_fd("docs/a.md"), self._make_fd("CHANGELOG.md")]
        batch = self._batch_risk_map_for("docs/a.md", "CHANGELOG.md")

        _, needs_llm = filter_obviously_safe_files(files, batch)
        assert needs_llm == []
        assert is_segment_obviously_safe(files, batch) is True
