import pytest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.models.diff import FileChangeCategory
from src.models.plan import MergePlanLive, RiskSummary
from src.tools.config_drift_detector import ConfigDrift


class TestConfigDriftDetector:
    def test_no_drift_when_all_match(self):
        from src.tools.config_drift_detector import ConfigDriftDetector

        detector = ConfigDriftDetector(Path("/tmp"))
        drifts = detector.detect_drift(
            code_defaults={"DB_HOST": "localhost"},
            env_defaults={"DB_HOST": "localhost"},
            docker_env_defaults={"DB_HOST": "localhost"},
        )
        assert len(drifts) == 0

    def test_drift_detected_when_values_differ(self):
        from src.tools.config_drift_detector import ConfigDriftDetector

        detector = ConfigDriftDetector(Path("/tmp"))
        drifts = detector.detect_drift(
            code_defaults={"ENABLED": "True"},
            env_defaults={"ENABLED": "false"},
            docker_env_defaults={"ENABLED": "false"},
        )
        assert len(drifts) == 1
        assert drifts[0].key == "ENABLED"
        assert drifts[0].code_default == "True"
        assert drifts[0].env_default == "false"
        assert "ENABLED" in drifts[0].impact

    def test_drift_skips_single_source_keys(self):
        from src.tools.config_drift_detector import ConfigDriftDetector

        detector = ConfigDriftDetector(Path("/tmp"))
        drifts = detector.detect_drift(
            code_defaults={"ONLY_CODE": "val"},
            env_defaults={},
            docker_env_defaults={},
        )
        assert len(drifts) == 0

    def test_drift_multiple_keys(self):
        from src.tools.config_drift_detector import ConfigDriftDetector

        detector = ConfigDriftDetector(Path("/tmp"))
        drifts = detector.detect_drift(
            code_defaults={"A": "1", "B": "x"},
            env_defaults={"A": "2", "B": "x"},
            docker_env_defaults={"A": "1", "B": "y"},
        )
        assert len(drifts) == 2
        keys = {d.key for d in drifts}
        assert keys == {"A", "B"}

    def test_drift_report_from_env_files(self, tmp_path):
        from src.tools.config_drift_detector import ConfigDriftDetector

        (tmp_path / ".env.example").write_text("DB_HOST=localhost\nDEBUG=true\n")
        docker_dir = tmp_path / "docker"
        docker_dir.mkdir()
        (docker_dir / ".env").write_text("DB_HOST=db\nDEBUG=true\n")

        detector = ConfigDriftDetector(tmp_path)
        report = detector.detect_drift_from_files(
            env_files=[".env.example"],
            docker_env_files=["docker/.env"],
        )
        assert report.total_keys_checked == 2
        assert report.drift_count == 1
        assert report.has_drifts
        assert report.drifts[0].key == "DB_HOST"

    def test_no_drift_report_when_files_missing(self, tmp_path):
        from src.tools.config_drift_detector import ConfigDriftDetector

        detector = ConfigDriftDetector(tmp_path)
        report = detector.detect_drift_from_files(
            env_files=[".env.example"],
            docker_env_files=["docker/.env"],
        )
        assert report.drift_count == 0
        assert not report.has_drifts

    def test_parse_env_file_ignores_comments(self, tmp_path):
        from src.tools.config_drift_detector import ConfigDriftDetector

        (tmp_path / ".env").write_text("# comment\nKEY=value\n# another\nKEY2=val2\n")

        detector = ConfigDriftDetector(tmp_path)
        defaults = detector._parse_env_files([".env"])
        assert defaults == {"KEY": "value", "KEY2": "val2"}

    def test_parse_env_file_strips_quotes(self, tmp_path):
        from src.tools.config_drift_detector import ConfigDriftDetector

        (tmp_path / ".env").write_text("KEY1=\"quoted\"\nKEY2='single'\nKEY3=plain\n")

        detector = ConfigDriftDetector(tmp_path)
        defaults = detector._parse_env_files([".env"])
        assert defaults["KEY1"] == "quoted"
        assert defaults["KEY2"] == "single"
        assert defaults["KEY3"] == "plain"

    def test_parse_code_defaults_os_getenv(self, tmp_path):
        from src.tools.config_drift_detector import ConfigDriftDetector

        code = 'host = os.getenv("DB_HOST", "localhost")\nport = os.environ.get("DB_PORT", "5432")\n'
        (tmp_path / "settings.py").write_text(code)

        detector = ConfigDriftDetector(tmp_path)
        defaults = detector._parse_code_defaults(["settings.py"])
        assert defaults["DB_HOST"] == "localhost"
        assert defaults["DB_PORT"] == "5432"

    def test_find_env_files(self, tmp_path):
        from src.tools.config_drift_detector import ConfigDriftDetector

        (tmp_path / ".env").write_text("X=1\n")
        (tmp_path / ".env.example").write_text("X=1\n")
        docker_dir = tmp_path / "docker"
        docker_dir.mkdir()
        (docker_dir / ".env").write_text("X=1\n")

        detector = ConfigDriftDetector(tmp_path)
        env_files, docker_files = detector.find_env_files()
        assert ".env" in env_files
        assert ".env.example" in env_files
        assert "docker/.env" in docker_files

    def test_config_drift_model(self):
        from src.tools.config_drift_detector import ConfigDrift

        drift = ConfigDrift(
            key="FEATURE_FLAG",
            code_default="True",
            env_default="false",
            docker_default=None,
            impact="Divergent behavior",
            suggestion="Align to false",
        )
        assert drift.key == "FEATURE_FLAG"
        assert drift.docker_default is None


class TestPollutionAuditor:
    def _mock_git(self, file_hashes=None, prior_commits=None, commit_files=None):
        git_tool = MagicMock()
        git_tool.get_file_hash.side_effect = lambda ref, fp: (file_hashes or {}).get(
            (ref, fp)
        )

        log_output = ""
        if prior_commits:
            log_output = "\n".join(
                f"{sha} merge upstream changes" for sha in prior_commits
            )
        git_tool.repo.git.log.return_value = log_output

        def diff_tree_side_effect(*args, **kwargs):
            sha = args[-1] if args else ""
            files = (commit_files or {}).get(sha, [])
            return "\n".join(files)

        git_tool.repo.git.diff_tree.side_effect = diff_tree_side_effect

        return git_tool

    def test_clean_audit_no_prior_merges(self):
        from src.tools.pollution_auditor import PollutionAuditor

        git_tool = self._mock_git()
        git_tool.repo.git.log.return_value = ""

        auditor = PollutionAuditor(git_tool)
        report = auditor.audit(
            "base", "head", "upstream", {"a.py": FileChangeCategory.A}
        )
        assert report.clean
        assert report.reclassified_count == 0
        assert not report.has_pollution

    def test_audit_detects_a_overwritten(self):
        from src.tools.pollution_auditor import PollutionAuditor

        file_hashes = {
            ("base", "a.py"): "hash_base",
            ("head", "a.py"): "hash_head",
            ("upstream", "a.py"): "hash_upstream",
        }
        git_tool = self._mock_git(
            file_hashes=file_hashes,
            prior_commits=["abc123"],
            commit_files={"abc123": ["a.py"]},
        )

        auditor = PollutionAuditor(git_tool)
        categories = {"a.py": FileChangeCategory.A}
        report = auditor.audit("base", "head", "upstream", categories)

        assert report.has_pollution
        assert report.reclassified_count == 1
        assert report.polluted_files[0].original_category == FileChangeCategory.A
        assert report.polluted_files[0].corrected_category == FileChangeCategory.C

    def test_audit_detects_a_to_b(self):
        from src.tools.pollution_auditor import PollutionAuditor

        file_hashes = {
            ("base", "a.py"): "hash_base",
            ("head", "a.py"): "hash_base",
            ("upstream", "a.py"): "hash_upstream",
        }
        git_tool = self._mock_git(
            file_hashes=file_hashes,
            prior_commits=["abc123"],
            commit_files={"abc123": ["a.py"]},
        )

        auditor = PollutionAuditor(git_tool)
        report = auditor.audit(
            "base", "head", "upstream", {"a.py": FileChangeCategory.A}
        )
        assert report.polluted_files[0].corrected_category == FileChangeCategory.B

    def test_audit_detects_e_residue(self):
        from src.tools.pollution_auditor import PollutionAuditor

        file_hashes = {
            ("base", "e.py"): "hash_base",
            ("head", "e.py"): "hash_head",
            ("upstream", "e.py"): "hash_upstream",
        }
        git_tool = self._mock_git(
            file_hashes=file_hashes,
            prior_commits=["def456"],
            commit_files={"def456": ["e.py"]},
        )

        auditor = PollutionAuditor(git_tool)
        report = auditor.audit(
            "base", "head", "upstream", {"e.py": FileChangeCategory.E}
        )
        assert report.has_pollution
        pf = report.polluted_files[0]
        assert pf.original_category == FileChangeCategory.E
        assert pf.corrected_category == FileChangeCategory.C

    def test_audit_detects_e_to_a(self):
        from src.tools.pollution_auditor import PollutionAuditor

        file_hashes = {
            ("base", "e.py"): "hash_base",
            ("head", "e.py"): "same_hash",
            ("upstream", "e.py"): "same_hash",
        }
        git_tool = self._mock_git(
            file_hashes=file_hashes,
            prior_commits=["abc"],
            commit_files={"abc": ["e.py"]},
        )

        auditor = PollutionAuditor(git_tool)
        report = auditor.audit(
            "base", "head", "upstream", {"e.py": FileChangeCategory.E}
        )
        assert report.polluted_files[0].corrected_category == FileChangeCategory.A

    def test_audit_detects_b_partial(self):
        from src.tools.pollution_auditor import PollutionAuditor

        file_hashes = {
            ("base", "b.py"): "hash_base",
            ("head", "b.py"): "hash_partial",
            ("upstream", "b.py"): "hash_upstream",
        }
        git_tool = self._mock_git(
            file_hashes=file_hashes,
            prior_commits=["xyz"],
            commit_files={"xyz": ["b.py"]},
        )

        auditor = PollutionAuditor(git_tool)
        report = auditor.audit(
            "base", "head", "upstream", {"b.py": FileChangeCategory.B}
        )
        assert report.has_pollution
        assert report.polluted_files[0].corrected_category == FileChangeCategory.C

    def test_audit_b_to_a_when_already_merged(self):
        from src.tools.pollution_auditor import PollutionAuditor

        file_hashes = {
            ("base", "b.py"): "hash_base",
            ("head", "b.py"): "hash_up",
            ("upstream", "b.py"): "hash_up",
        }
        git_tool = self._mock_git(
            file_hashes=file_hashes,
            prior_commits=["xyz"],
            commit_files={"xyz": ["b.py"]},
        )

        auditor = PollutionAuditor(git_tool)
        report = auditor.audit(
            "base", "head", "upstream", {"b.py": FileChangeCategory.B}
        )
        assert report.polluted_files[0].corrected_category == FileChangeCategory.A

    def test_no_pollution_for_unaffected_files(self):
        from src.tools.pollution_auditor import PollutionAuditor

        file_hashes = {
            ("base", "clean.py"): "h1",
            ("head", "clean.py"): "h1",
            ("upstream", "clean.py"): "h1",
        }
        git_tool = self._mock_git(
            file_hashes=file_hashes,
            prior_commits=["abc"],
            commit_files={"abc": ["other.py"]},
        )

        auditor = PollutionAuditor(git_tool)
        report = auditor.audit(
            "base", "head", "upstream", {"clean.py": FileChangeCategory.A}
        )
        assert report.clean
        assert report.reclassified_count == 0

    def test_apply_corrections(self):
        from src.tools.pollution_auditor import (
            PollutionAuditor,
            PollutionAuditReport,
            PollutedFile,
        )

        git_tool = MagicMock()
        auditor = PollutionAuditor(git_tool)

        original = {
            "a.py": FileChangeCategory.A,
            "b.py": FileChangeCategory.B,
        }
        report = PollutionAuditReport(
            polluted_files=[
                PollutedFile(
                    file_path="a.py",
                    original_category=FileChangeCategory.A,
                    corrected_category=FileChangeCategory.C,
                    reason="test",
                ),
            ],
            total_files_audited=2,
            reclassified_count=1,
            clean=False,
        )

        corrected = auditor.apply_corrections(original, report)
        assert corrected["a.py"] == FileChangeCategory.C
        assert corrected["b.py"] == FileChangeCategory.B
        assert original["a.py"] == FileChangeCategory.A

    def test_pollution_report_model(self):
        from src.tools.pollution_auditor import PollutionAuditReport

        report = PollutionAuditReport(
            prior_merge_commits=["abc", "def"],
            files_from_prior_merges=["a.py", "b.py"],
            total_files_audited=100,
            reclassified_count=0,
            clean=True,
        )
        assert not report.has_pollution
        assert report.total_files_audited == 100

    def test_polluted_file_model(self):
        from src.tools.pollution_auditor import PollutedFile

        pf = PollutedFile(
            file_path="x.py",
            original_category=FileChangeCategory.A,
            corrected_category=FileChangeCategory.C,
            reason="A-overwritten",
            source_commit="abc123",
        )
        assert pf.source_commit == "abc123"

    def test_describe_reason_known_pair(self):
        from src.tools.pollution_auditor import PollutionAuditor

        auditor = PollutionAuditor(MagicMock())
        reason = auditor._describe_reason(FileChangeCategory.A, FileChangeCategory.C)
        assert "A-overwritten" in reason

    def test_describe_reason_unknown_pair(self):
        from src.tools.pollution_auditor import PollutionAuditor

        auditor = PollutionAuditor(MagicMock())
        reason = auditor._describe_reason(
            FileChangeCategory.D_MISSING, FileChangeCategory.A
        )
        assert "Reclassified" in reason


class TestMergePlanLiveP3Fields:
    def test_config_drifts_field(self):
        rs = RiskSummary(
            total_files=1,
            auto_safe_count=1,
            auto_risky_count=0,
            human_required_count=0,
            deleted_only_count=0,
            binary_count=0,
            excluded_count=0,
            estimated_auto_merge_rate=1.0,
        )
        live = MergePlanLive(
            created_at=datetime.now(),
            upstream_ref="u",
            fork_ref="f",
            merge_base_commit="base",
            phases=[],
            risk_summary=rs,
            project_context_summary="",
        )
        assert live.config_drifts == []
        assert live.pollution_summary == {}

        live.config_drifts.append(
            ConfigDrift(key="DEBUG", code_default="True", env_default="false")
        )
        assert len(live.config_drifts) == 1

    def test_pollution_summary_field(self):
        rs = RiskSummary(
            total_files=1,
            auto_safe_count=1,
            auto_risky_count=0,
            human_required_count=0,
            deleted_only_count=0,
            binary_count=0,
            excluded_count=0,
            estimated_auto_merge_rate=1.0,
        )
        live = MergePlanLive(
            created_at=datetime.now(),
            upstream_ref="u",
            fork_ref="f",
            merge_base_commit="base",
            phases=[],
            risk_summary=rs,
            project_context_summary="",
            pollution_summary={
                "reclassified_count": 3,
                "prior_commits": ["abc", "def"],
            },
        )
        assert live.pollution_summary["reclassified_count"] == 3


class TestMergeStateP3Fields:
    def test_pollution_audit_field(self):
        from src.models.state import MergeState
        from src.models.config import MergeConfig

        config = MergeConfig(upstream_ref="u", fork_ref="f")
        state = MergeState(config=config)
        assert state.pollution_audit is None
        assert state.config_drifts is None

    def test_pollution_audit_stores_report(self):
        from src.models.state import MergeState
        from src.models.config import MergeConfig
        from src.tools.pollution_auditor import PollutionAuditReport

        config = MergeConfig(upstream_ref="u", fork_ref="f")
        state = MergeState(config=config)

        report = PollutionAuditReport(
            prior_merge_commits=["abc"],
            total_files_audited=50,
            reclassified_count=2,
            clean=False,
        )
        state.pollution_audit = report
        assert state.pollution_audit.reclassified_count == 2

    def test_config_drifts_stores_report(self):
        from src.models.state import MergeState
        from src.models.config import MergeConfig
        from src.tools.config_drift_detector import ConfigDriftReport

        config = MergeConfig(upstream_ref="u", fork_ref="f")
        state = MergeState(config=config)

        report = ConfigDriftReport(
            total_keys_checked=10,
            drift_count=2,
        )
        state.config_drifts = report
        assert state.config_drifts.drift_count == 2


class TestOrchestratorP3Integration:
    def _make_orchestrator(self):
        from src.core.orchestrator import Orchestrator
        from src.models.config import MergeConfig

        config = MergeConfig(upstream_ref="upstream/main", fork_ref="fork/main")
        mock_agents = {
            name: MagicMock()
            for name in [
                "planner",
                "planner_judge",
                "conflict_analyst",
                "executor",
                "judge",
                "human_interface",
            ]
        }
        for a in mock_agents.values():
            a.set_trace_logger = MagicMock()
            a.set_memory_store = MagicMock()
        with (
            patch("src.core.orchestrator.GitTool"),
            patch("src.core.orchestrator.GateRunner"),
        ):
            return Orchestrator(config, agents=mock_agents)

    def test_orchestrator_imports_p3_tools(self):
        from src.tools.pollution_auditor import PollutionAuditor
        from src.tools.config_drift_detector import ConfigDriftDetector

        assert PollutionAuditor is not None
        assert ConfigDriftDetector is not None

    def test_orchestrator_creates_successfully(self):
        orch = self._make_orchestrator()
        assert orch is not None
