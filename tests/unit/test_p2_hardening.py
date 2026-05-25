"""Unit tests for P2 hardening
(multi-agent-optimization doc §4 P2-1..P2-3).

Covers:
- P2-1 ScarListBuilder + materialize_as_customizations
- P2-2 SentinelScanner (scan / scan_file / from_config_extras)
        + Executor sentinel-gating in run()
- P2-3 ConfigLineRetentionChecker
        + JudgeAgent._check_sentinel_hits / _check_config_retention
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.config import (
    AgentLLMConfig,
    ConfigRetentionConfig,
    ConfigRetentionRule,
    CustomizationEntry,
    MergeConfig,
    ScarLearningConfig,
)
from src.models.judge import ISSUE_TYPES_NEW, VETO_CONDITIONS
from src.tools.config_line_retention_checker import (
    ConfigLineRetentionChecker,
    ConfigRetentionViolation,
)
from src.tools.scar_list_builder import (
    DEFAULT_GREP_PATTERNS,
    Scar,
    ScarListBuilder,
    _classify_kind,
)
from src.tools.sentinel_scanner import DEFAULT_SENTINELS, SentinelHit, SentinelScanner


# ---------------------------------------------------------------------------
# P2-1  ScarListBuilder
# ---------------------------------------------------------------------------


class TestScarClassifyKind:
    def test_restore(self):
        assert _classify_kind("fix: restore missing route") == "restore"

    def test_fix_compat(self):
        assert _classify_kind("fix compat issue in auth") == "fix_compat"

    def test_revert(self):
        assert _classify_kind("Revert PR #123") == "revert"

    def test_restore_beats_revert(self):
        assert _classify_kind("restore reverted logic") == "restore"

    def test_fallback_to_restore(self):
        assert _classify_kind("chore: bump version") == "restore"


class TestScarListBuilderBuild:
    def test_returns_empty_for_invalid_repo(self, tmp_path: Path):
        builder = ScarListBuilder()
        result = builder.build(tmp_path / "no_such_repo")
        assert result == []

    def test_returns_empty_for_valid_repo_no_matches(self, tmp_path: Path):
        repo = _init_bare_git_repo(tmp_path)
        builder = ScarListBuilder()
        result = builder.build(repo, grep_patterns=["XYZZY_NO_MATCH"])
        assert result == []

    def test_finds_restore_commit(self, tmp_path: Path):
        repo_path = _init_git_repo_with_commits(
            tmp_path,
            commits=[
                ("add feature", {"src/foo.py": "x = 1\n"}),
                ("fix: restore bar logic", {"src/bar.py": "y = 2\n"}),
            ],
        )
        builder = ScarListBuilder()
        scars = builder.build(repo_path, since="1 year ago")
        subjects = [s.commit_subject for s in scars]
        assert any("restore" in s.lower() for s in subjects)

    def test_scar_files_populated(self, tmp_path: Path):
        repo_path = _init_git_repo_with_commits(
            tmp_path,
            commits=[
                ("add base", {"src/a.py": "pass\n"}),
                ("fix: restore deleted handler", {"src/handler.py": "def h(): pass\n"}),
            ],
        )
        builder = ScarListBuilder()
        scars = builder.build(repo_path)
        restore_scars = [s for s in scars if "restore" in s.commit_subject.lower()]
        assert restore_scars
        assert any("src/handler.py" in s.files for s in restore_scars)

    def test_deduplication_by_sha(self, tmp_path: Path):
        repo_path = _init_git_repo_with_commits(
            tmp_path,
            commits=[
                ("add base", {"a.py": "1\n"}),
                ("fix: restore x", {"b.py": "2\n"}),
            ],
        )
        builder = ScarListBuilder()
        scars = builder.build(repo_path)
        shas = [s.commit_sha for s in scars]
        assert len(shas) == len(set(shas))


class TestScarListBuilderMaterialize:
    def _make_scars(self) -> list[Scar]:
        return [
            Scar(
                commit_sha="aaa",
                commit_subject="fix: restore auth.py",
                files=["src/auth.py", "src/utils.py"],
                pattern_kind="restore",
            ),
            Scar(
                commit_sha="bbb",
                commit_subject="fix: restore auth.py again",
                files=["src/auth.py"],
                pattern_kind="restore",
            ),
        ]

    def test_returns_customization_entries(self):
        builder = ScarListBuilder()
        entries = builder.materialize_as_customizations(self._make_scars(), [])
        assert all(isinstance(e, CustomizationEntry) for e in entries)

    def test_source_tagged_scar_learned(self):
        builder = ScarListBuilder()
        entries = builder.materialize_as_customizations(self._make_scars(), [])
        assert all(e.source == "scar_learned" for e in entries)

    def test_high_frequency_file_gets_higher_confidence(self):
        builder = ScarListBuilder()
        entries = builder.materialize_as_customizations(self._make_scars(), [])
        auth_entry = next(e for e in entries if "src/auth.py" in e.files)
        utils_entry = next(e for e in entries if "src/utils.py" in e.files)
        assert auth_entry.confidence > utils_entry.confidence

    def test_skips_already_covered_files(self):
        existing = [CustomizationEntry(name="existing", files=["src/auth.py"])]
        builder = ScarListBuilder()
        entries = builder.materialize_as_customizations(self._make_scars(), existing)
        covered = {fp for e in entries for fp in e.files}
        assert "src/auth.py" not in covered

    def test_empty_scars_returns_empty(self):
        builder = ScarListBuilder()
        assert builder.materialize_as_customizations([], []) == []

    def test_verification_uses_file_exists(self):
        builder = ScarListBuilder()
        entries = builder.materialize_as_customizations(self._make_scars(), [])
        for e in entries:
            assert any(v.type == "file_exists" for v in e.verification)


# ---------------------------------------------------------------------------
# P2-2  SentinelScanner
# ---------------------------------------------------------------------------


class TestSentinelScannerDefaults:
    def test_default_sentinels_non_empty(self):
        assert len(DEFAULT_SENTINELS) > 0

    def test_scanner_created_with_defaults(self):
        s = SentinelScanner()
        assert len(s._compiled) == len(DEFAULT_SENTINELS)

    def test_extra_extends_defaults(self):
        s = SentinelScanner(extra=[r"MY_CUSTOM"])
        assert len(s._compiled) == len(DEFAULT_SENTINELS) + 1


class TestSentinelScannerScan:
    def test_no_hits_clean_file(self):
        scanner = SentinelScanner()
        hits = scanner.scan("x = 1\ny = 2\n", "clean.py")
        assert hits == []

    def test_fork_only_marker_detected(self):
        scanner = SentinelScanner()
        content = "def foo():\n    pass  # @fork-only: custom\n"
        hits = scanner.scan(content, "foo.py")
        assert len(hits) == 1
        assert hits[0].line_number == 2
        assert hits[0].file_path == "foo.py"

    def test_todo_merge_detected(self):
        scanner = SentinelScanner()
        content = "# TODO [merge]: check this\npass\n"
        hits = scanner.scan(content, "x.py")
        assert hits

    def test_conflict_marker_detected(self):
        scanner = SentinelScanner()
        content = "<<<<<<< HEAD\nx = 1\n=======\nx = 2\n>>>>>>> upstream\n"
        hits = scanner.scan(content, "conflict.py")
        assert len(hits) >= 1

    def test_current_branch_enhancement_detected(self):
        scanner = SentinelScanner()
        content = "# Current branch enhancement\ndef extra(): pass\n"
        hits = scanner.scan(content, "feat.py")
        assert hits

    def test_extra_pattern_matches(self):
        scanner = SentinelScanner(extra=[r"CVTE"])
        content = "# CVTE custom logic\n"
        hits = scanner.scan(content, "f.py")
        assert hits
        assert hits[0].pattern == r"CVTE"

    def test_hit_contains_matched_text(self):
        scanner = SentinelScanner()
        content = "# @do-not-remove: important\n"
        hits = scanner.scan(content, "g.py")
        assert hits[0].matched_text == "# @do-not-remove: important"

    def test_only_first_matching_pattern_per_line(self):
        scanner = SentinelScanner()
        content = "# @fork-only @do-not-remove\n"
        hits = scanner.scan(content, "h.py")
        assert len(hits) == 1


class TestSentinelScannerScanFile:
    def test_reads_file_from_disk(self, tmp_path: Path):
        f = tmp_path / "myfile.py"
        f.write_text("# @fork-only\n", encoding="utf-8")
        scanner = SentinelScanner()
        hits = scanner.scan_file(f)
        assert hits
        assert hits[0].line_number == 1

    def test_missing_file_returns_empty(self, tmp_path: Path):
        scanner = SentinelScanner()
        hits = scanner.scan_file(tmp_path / "nonexistent.py")
        assert hits == []

    def test_from_config_extras(self):
        scanner = SentinelScanner.from_config_extras(["MYMARKER"])
        content = "# MYMARKER\n"
        hits = scanner.scan(content, "x.py")
        assert hits


# ---------------------------------------------------------------------------
# P2-2  Executor sentinel gating
# ---------------------------------------------------------------------------


class TestExecutorSentinelGating:
    def _make_state(self) -> MagicMock:
        from src.models.diff import FileChangeCategory, RiskLevel

        config = MagicMock()
        config.fork_ref = "fork/main"
        config.upstream_ref = "upstream/main"
        config.project_context = ""
        config.sentinels_extra = []

        plan_phase = MagicMock()
        plan_phase.risk_level = RiskLevel.AUTO_SAFE
        plan_phase.file_paths = ["src/service.py"]
        plan_phase.change_category = FileChangeCategory.C

        plan = MagicMock()
        plan.phases = [plan_phase]

        state = MagicMock()
        state.config = config
        state.merge_plan = plan
        state.file_diffs = []
        state.file_decision_records = {}
        state.plan_disputes = []
        state.sentinel_hits = {}
        state.current_phase = MagicMock()
        state.current_phase.value = "auto_merge"
        return state

    @pytest.mark.asyncio
    async def test_sentinel_hit_raises_dispute(self):
        from src.agents.executor_agent import ExecutorAgent
        from src.models.diff import RiskLevel

        git_tool = MagicMock()
        git_tool.get_file_content.return_value = "# @fork-only: custom logic\n"

        with patch("src.llm.client.LLMClientFactory.create", return_value=MagicMock()):
            agent = ExecutorAgent(llm_config=AgentLLMConfig(), git_tool=git_tool)
        state = self._make_state()

        await agent.run(state)

        assert "src/service.py" in state.sentinel_hits
        assert len(state.plan_disputes) == 1
        dispute = state.plan_disputes[0]
        assert (
            dispute.suggested_reclassification["src/service.py"]
            == RiskLevel.HUMAN_REQUIRED
        )

    @pytest.mark.asyncio
    async def test_clean_file_no_dispute(self):
        from src.agents.executor_agent import ExecutorAgent

        git_tool = MagicMock()
        git_tool.get_file_content.return_value = "x = 1\n"

        with patch("src.llm.client.LLMClientFactory.create", return_value=MagicMock()):
            agent = ExecutorAgent(llm_config=AgentLLMConfig(), git_tool=git_tool)
        state = self._make_state()

        with patch.object(agent, "execute_auto_merge", new_callable=AsyncMock) as m:
            m.return_value = MagicMock()
            await agent.run(state)

        assert "src/service.py" not in state.sentinel_hits
        assert len(state.plan_disputes) == 0


# ---------------------------------------------------------------------------
# P2-3  ConfigLineRetentionChecker
# ---------------------------------------------------------------------------


class TestConfigLineRetentionChecker:
    def _make_rule(self, glob: str, patterns: list[str]) -> ConfigRetentionRule:
        return ConfigRetentionRule(file_glob=glob, required_lines=patterns)

    def test_no_violations_when_line_present(self, tmp_path: Path):
        ci = tmp_path / ".github" / "workflows"
        ci.mkdir(parents=True)
        (ci / "ci.yml").write_text("  my-job:\n    runs-on: ubuntu\n", encoding="utf-8")

        checker = ConfigLineRetentionChecker(tmp_path)
        violations = checker.check(
            [self._make_rule(".github/workflows/*.yml", [r"^\s*my-job:"])]
        )
        assert violations == []

    def test_violation_when_line_missing(self, tmp_path: Path):
        ci = tmp_path / ".github" / "workflows"
        ci.mkdir(parents=True)
        (ci / "ci.yml").write_text(
            "  other-job:\n    runs-on: ubuntu\n", encoding="utf-8"
        )

        checker = ConfigLineRetentionChecker(tmp_path)
        violations = checker.check(
            [self._make_rule(".github/workflows/*.yml", [r"^\s*my-job:"])]
        )
        assert len(violations) == 1
        assert violations[0].file_path.endswith("ci.yml")
        assert r"^\s*my-job:" in violations[0].missing_patterns

    def test_multiple_required_lines_partial_match(self, tmp_path: Path):
        env = tmp_path / "docker"
        env.mkdir()
        (env / ".env.example").write_text("FOO=bar\n", encoding="utf-8")

        checker = ConfigLineRetentionChecker(tmp_path)
        violations = checker.check(
            [self._make_rule("docker/.env.example", [r"^FOO=", r"^MISSING_VAR="])]
        )
        assert len(violations) == 1
        assert r"^MISSING_VAR=" in violations[0].missing_patterns
        assert r"^FOO=" not in violations[0].missing_patterns

    def test_empty_rules_returns_empty(self, tmp_path: Path):
        checker = ConfigLineRetentionChecker(tmp_path)
        assert checker.check([]) == []

    def test_glob_matches_multiple_files(self, tmp_path: Path):
        wf = tmp_path / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "a.yml").write_text("steps:\n", encoding="utf-8")
        (wf / "b.yml").write_text("steps:\n", encoding="utf-8")

        checker = ConfigLineRetentionChecker(tmp_path)
        violations = checker.check(
            [self._make_rule(".github/workflows/*.yml", [r"^REQUIRED_LINE"])]
        )
        assert len(violations) == 2

    def test_invalid_regex_treated_as_literal(self, tmp_path: Path):
        f = tmp_path / "Dockerfile"
        f.write_text("RUN apt-get install [foo\n", encoding="utf-8")

        checker = ConfigLineRetentionChecker(tmp_path)
        violations = checker.check([self._make_rule("Dockerfile", [r"[foo"])])
        assert violations == []

    def test_violation_is_frozen(self):
        v = ConfigRetentionViolation(
            rule_file_glob="*.yml",
            file_path="ci.yml",
            missing_patterns=["x"],
        )
        with pytest.raises(Exception):
            v.file_path = "other.yml"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# P2-2 + P2-3  JudgeAgent deterministic pipeline integration
# ---------------------------------------------------------------------------


class TestJudgeAgentP2Checks:
    def _make_judge(self, repo_path: Path):
        from src.agents.judge_agent import JudgeAgent

        git_tool = MagicMock()
        git_tool.repo_path = repo_path
        with patch("src.llm.client.LLMClientFactory.create", return_value=MagicMock()):
            return JudgeAgent(llm_config=AgentLLMConfig(), git_tool=git_tool)

    def _make_state(
        self,
        sentinel_hits: dict | None = None,
        retention_rules: list[ConfigRetentionRule] | None = None,
    ) -> MagicMock:
        config = MagicMock()
        config.upstream_ref = "upstream/main"
        config.project_context = ""
        config.cross_layer_assertions = []
        config.sentinels_extra = []
        config.config_retention = ConfigRetentionConfig(
            enabled=True,
            rules=retention_rules or [],
        )

        state = MagicMock()
        state.config = config
        state.merge_base_commit = "abc123"
        state.file_categories = {}
        state.file_diffs = []
        state.file_decision_records = {}
        state.shadow_conflicts = []
        state.interface_changes = []
        state.reverse_impacts = {}
        state.sentinel_hits = sentinel_hits or {}
        return state

    def test_sentinel_hits_emits_veto_issue(self, tmp_path: Path):
        from src.models.judge import IssueSeverity

        judge = self._make_judge(tmp_path)
        hits = [
            SentinelHit(
                file_path="src/api.py",
                line_number=5,
                pattern=r"@fork-only",
                matched_text="# @fork-only",
            )
        ]
        state = self._make_state(sentinel_hits={"src/api.py": hits})

        issues = judge._check_sentinel_hits(state)
        assert len(issues) == 1
        assert issues[0].issue_type == "sentinel_hit_unacknowledged"
        assert issues[0].issue_level == IssueSeverity.CRITICAL
        assert issues[0].veto_condition is not None

    def test_no_sentinel_hits_returns_empty(self, tmp_path: Path):
        judge = self._make_judge(tmp_path)
        state = self._make_state(sentinel_hits={})
        assert judge._check_sentinel_hits(state) == []

    def test_dead_checks_revived_under_judge_contract(self, tmp_path: Path):
        """Regression: sentinel_hits / shadow_conflicts were absent from
        judge.yaml inputs, so getattr on the contract-restricted view swallowed
        FieldNotInContract (it subclasses AttributeError) and both checks never
        fired in production. The earlier tests pass a bare MagicMock, bypassing
        the contract entirely — this one drives the real restricted view."""
        from src.models.config import MergeConfig
        from src.models.state import MergeState
        from src.tools.shadow_conflict_detector import ShadowConflict

        judge = self._make_judge(tmp_path)
        state = MergeState(
            config=MergeConfig(upstream_ref="upstream/main", fork_ref="fork")
        )
        state.merge_base_commit = "base"
        state.sentinel_hits = {
            "src/api.py": [
                SentinelHit(
                    file_path="src/api.py",
                    line_number=5,
                    pattern=r"@fork-only",
                    matched_text="# @fork-only",
                )
            ]
        }
        state.shadow_conflicts = [
            ShadowConflict(
                logical_name="cfg",
                path_a="a.yaml",
                path_b="b.yaml",
                rule_description="dup",
            )
        ]

        restricted = judge.restricted_view(state)
        # Contract now grants read access — these would raise
        # FieldNotInContract (swallowed to empty) before the fix.
        assert restricted.sentinel_hits
        assert restricted.shadow_conflicts

        issues = judge._run_deterministic_pipeline(restricted, {})
        types = {i.issue_type for i in issues}
        assert "sentinel_hit_unacknowledged" in types
        assert "shadow_conflict_unresolved" in types

    def test_config_retention_violation_emits_issue(self, tmp_path: Path):
        from src.models.judge import IssueSeverity

        ci = tmp_path / ".github" / "workflows"
        ci.mkdir(parents=True)
        (ci / "ci.yml").write_text("jobs:\n  other:\n", encoding="utf-8")

        rule = ConfigRetentionRule(
            file_glob=".github/workflows/*.yml",
            required_lines=[r"^\s*my-required-job:"],
        )
        judge = self._make_judge(tmp_path)
        state = self._make_state(retention_rules=[rule])

        issues = judge._check_config_retention(state)
        assert len(issues) == 1
        assert issues[0].issue_type == "config_retention_violation"
        assert issues[0].issue_level == IssueSeverity.CRITICAL

    def test_config_retention_no_rules_returns_empty(self, tmp_path: Path):
        judge = self._make_judge(tmp_path)
        state = self._make_state(retention_rules=[])
        assert judge._check_config_retention(state) == []

    def test_config_retention_disabled_returns_empty(self, tmp_path: Path):
        from src.agents.judge_agent import JudgeAgent

        git_tool = MagicMock()
        git_tool.repo_path = tmp_path
        with patch("src.llm.client.LLMClientFactory.create", return_value=MagicMock()):
            judge = JudgeAgent(llm_config=AgentLLMConfig(), git_tool=git_tool)

        config = MagicMock()
        config.config_retention = ConfigRetentionConfig(
            enabled=False,
            rules=[ConfigRetentionRule(file_glob="*.yml", required_lines=["x"])],
        )
        state = MagicMock()
        state.config = config
        state.sentinel_hits = {}

        assert judge._check_config_retention(state) == []


# ---------------------------------------------------------------------------
# P2 model / config smoke tests
# ---------------------------------------------------------------------------


class TestP2ModelConfig:
    def test_customization_entry_default_source_manual(self):
        e = CustomizationEntry(name="test")
        assert e.source == "manual"
        assert e.confidence == 1.0

    def test_customization_entry_scar_learned(self):
        e = CustomizationEntry(name="scar:x.py", source="scar_learned", confidence=0.7)
        assert e.source == "scar_learned"
        assert e.confidence == pytest.approx(0.7)

    def test_scar_learning_config_defaults(self):
        c = ScarLearningConfig()
        assert c.enabled is True  # P2-3: zero-config repos protected by default
        assert c.since == "1 year ago"
        assert c.auto_append_to_customizations is True

    def test_config_retention_rule(self):
        r = ConfigRetentionRule(
            file_glob=".github/workflows/*.yml",
            required_lines=[r"^\s*my-job:"],
        )
        assert r.min_line_count == 1

    def test_merge_config_has_p2_fields(self):
        cfg = MergeConfig(upstream_ref="up/main", fork_ref="fork/main")
        assert hasattr(cfg, "sentinels_extra")
        assert hasattr(cfg, "config_retention")
        assert hasattr(cfg, "scar_learning")
        assert cfg.sentinels_extra == []

    def test_veto_conditions_contain_p2(self):
        assert "Sentinel hit in AUTO_SAFE file unacknowledged" in VETO_CONDITIONS
        assert "Config retention required line missing" in VETO_CONDITIONS

    def test_issue_types_new_contain_p2(self):
        assert "sentinel_hit_unacknowledged" in ISSUE_TYPES_NEW
        assert "config_retention_violation" in ISSUE_TYPES_NEW


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_bare_git_repo(tmp_path: Path) -> Path:
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    return repo


def _init_git_repo_with_commits(
    tmp_path: Path,
    commits: list[tuple[str, dict[str, str]]],
) -> Path:
    import subprocess

    repo = _init_bare_git_repo(tmp_path)

    for message, files in commits:
        for fname, content in files.items():
            fpath = repo / fname
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(content, encoding="utf-8")
        subprocess.run(
            ["git", "add", "-A"], cwd=str(repo), check=True, capture_output=True
        )
        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=str(repo),
            check=True,
            capture_output=True,
        )

    return repo
