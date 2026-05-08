"""Migration numbering-space collision detection (§9 P1).

Two layers:

1. Pure helpers — ``extract_migration_number`` / ``find_migration_collision``
2. End-to-end — ``InitializePhase._apply_forks_profile_routing`` writes
   the right decision when ``migration_policy`` matches.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from src.core.phases.base import PhaseContext
from src.core.phases.initialize import InitializePhase
from src.models.config import (
    FileClassifierConfig,
    MergeConfig,
    OutputConfig,
)
from src.models.decision import MergeDecision
from src.models.diff import FileChangeCategory
from src.models.forks_profile import (
    ForksProfile,
    MigrationCollisionAction,
    MigrationCollisionRule,
    MigrationPolicy,
)
from src.models.state import MergeState
from src.tools.forks_profile_loader import (
    extract_migration_number,
    find_migration_collision,
)


def _stub_policy(
    *,
    path_globs: list[str],
    upstream_max: int = 25,
    action: MigrationCollisionAction = MigrationCollisionAction.ESCALATE_HUMAN,
    note: str = "",
) -> MigrationPolicy:
    """Build the MigrationPolicy ``compute_auto_overlay`` would synthesize."""
    return MigrationPolicy(
        path_globs=path_globs,
        fork_owns_numbers_above=upstream_max,
        upstream_take_target_max=upstream_max,
        on_collision=MigrationCollisionRule(action=action, note=note),
    )


def _patch_overlay(
    monkeypatch, *, policy: MigrationPolicy | None = None, features=None
) -> None:
    """Replace ``compute_auto_overlay`` with a deterministic stub.

    The real implementation needs a git tree to compute fork divergence;
    routing tests don't care about the inputs, only that the overlay
    returns a known policy.
    """
    monkeypatch.setattr(
        "src.core.phases.initialize.compute_auto_overlay",
        lambda *args, **kwargs: (features or [], policy),
    )


def _write_profile(repo_root: Path, body: str) -> None:
    merge_dir = repo_root / ".merge"
    merge_dir.mkdir(parents=True, exist_ok=True)
    (merge_dir / "forks-profile.yaml").write_text(body, encoding="utf-8")


def _make_config(tmp_path: Path) -> MergeConfig:
    return MergeConfig(
        upstream_ref="upstream/main",
        fork_ref="feature/fork",
        repo_path=str(tmp_path),
        output=OutputConfig(directory=str(tmp_path / "outputs")),
        file_classifier=FileClassifierConfig(),
    )


def _make_ctx(config: MergeConfig, git_tool=None) -> PhaseContext:
    from src.core.state_machine import StateMachine
    from src.core.message_bus import MessageBus
    from src.core.phase_runner import PhaseRunner
    from src.memory.store import MemoryStore
    from src.memory.summarizer import PhaseSummarizer

    return PhaseContext(
        config=config,
        git_tool=git_tool or MagicMock(),
        gate_runner=MagicMock(),
        state_machine=StateMachine(),
        message_bus=MessageBus(),
        checkpoint=MagicMock(),
        phase_runner=PhaseRunner(),
        memory_store=MemoryStore(),
        summarizer=PhaseSummarizer(),
        trace_logger=None,
        emit=None,
        agents={},
    )


class TestExtractMigrationNumber:
    def test_simple_sequence(self):
        assert extract_migration_number("db/migrations/026_payments.sql") == 26

    def test_zero_padded(self):
        assert extract_migration_number("0001_initial.py") == 1

    def test_timestamp_style(self):
        assert extract_migration_number("20240101120000_widgets.sql") == 20240101120000

    def test_flyway_style(self):
        assert extract_migration_number("V1__init.sql") == 1

    def test_no_digits_returns_none(self):
        assert extract_migration_number("schema.sql") is None

    def test_only_first_run_used(self):
        assert extract_migration_number("002_v3_users.sql") == 2

    def test_directory_digits_ignored(self):
        assert extract_migration_number("v999/migrations/007_x.sql") == 7


class TestFindMigrationCollision:
    def _profile(
        self,
        path_globs: list[str],
        upstream_max: int | None = 25,
        fork_above: int | None = None,
        action: str = "escalate_human",
    ) -> ForksProfile:
        policy: dict = {"path_globs": path_globs}
        if upstream_max is not None:
            policy["upstream_take_target_max"] = upstream_max
        if fork_above is not None:
            policy["fork_owns_numbers_above"] = fork_above
        policy["on_collision"] = {"action": action, "note": "n/a"}
        return ForksProfile.model_validate({"migration_policy": policy})

    def test_collision_above_threshold(self):
        profile = self._profile(["db/migrations/*.sql"])
        result = find_migration_collision(profile, "db/migrations/030_payments.sql")
        assert result is not None
        number, rule = result
        assert number == 30
        assert rule.action == MigrationCollisionAction.ESCALATE_HUMAN

    def test_at_threshold_is_safe(self):
        profile = self._profile(["db/migrations/*.sql"])
        assert find_migration_collision(profile, "db/migrations/025_safe.sql") is None

    def test_below_threshold_is_safe(self):
        profile = self._profile(["db/migrations/*.sql"])
        assert find_migration_collision(profile, "db/migrations/010_early.sql") is None

    def test_path_outside_glob_ignored(self):
        profile = self._profile(["db/migrations/*.sql"])
        assert find_migration_collision(profile, "src/views/030_widget.py") is None

    def test_no_path_globs_disables_check(self):
        profile = self._profile(path_globs=[], upstream_max=25)
        assert find_migration_collision(profile, "db/migrations/030_x.sql") is None

    def test_no_bounds_disables_check(self):
        profile = self._profile(
            ["db/migrations/*.sql"], upstream_max=None, fork_above=None
        )
        assert find_migration_collision(profile, "db/migrations/030_x.sql") is None

    def test_fork_above_is_used_when_upstream_max_unset(self):
        profile = self._profile(
            ["db/migrations/*.sql"],
            upstream_max=None,
            fork_above=25,
        )
        result = find_migration_collision(
            profile, "db/migrations/026_first_fork_owned.sql"
        )
        assert result is not None
        assert result[0] == 26

    def test_default_rule_when_on_collision_omitted(self):
        profile = ForksProfile.model_validate(
            {
                "migration_policy": {
                    "path_globs": ["db/migrations/*.sql"],
                    "upstream_take_target_max": 25,
                }
            }
        )
        result = find_migration_collision(profile, "db/migrations/030_x.sql")
        assert result is not None
        _, rule = result
        assert rule.action == MigrationCollisionAction.ESCALATE_HUMAN

    def test_no_migration_policy_returns_none(self):
        profile = ForksProfile.model_validate({})
        assert find_migration_collision(profile, "db/migrations/030_x.sql") is None

    def test_filename_without_digits_skipped(self):
        profile = self._profile(["db/migrations/*.sql"])
        assert find_migration_collision(profile, "db/migrations/schema.sql") is None


class TestPlanStageMigrationRouting:
    """End-to-end routing now sources ``migration_policy`` from the auto
    overlay (``compute_auto_overlay``) — yaml can no longer declare it.
    Tests stub the overlay to keep the routing logic isolated from git.
    """

    def test_d_missing_collision_routes_to_escalate_human(
        self, tmp_path: Path, monkeypatch
    ):
        _patch_overlay(
            monkeypatch,
            policy=_stub_policy(
                path_globs=["db/migrations/*.sql"],
                upstream_max=25,
                action=MigrationCollisionAction.ESCALATE_HUMAN,
                note="manual reconcile",
            ),
        )
        config = _make_config(tmp_path)
        ctx = _make_ctx(config)
        state = MergeState(config=config)
        state.merge_base_commit = "deadbeef"
        phase = InitializePhase()

        consumed = phase._apply_forks_profile_routing(
            state,
            ctx,
            {"db/migrations/030_payments.sql": FileChangeCategory.D_MISSING},
        )
        assert consumed == {"db/migrations/030_payments.sql"}
        rec = state.file_decision_records["db/migrations/030_payments.sql"]
        assert rec.decision == MergeDecision.ESCALATE_HUMAN
        assert "30" in rec.rationale
        assert "escalate_human" in rec.rationale
        assert "manual reconcile" in rec.rationale

    def test_d_missing_collision_routes_to_take_current(
        self, tmp_path: Path, monkeypatch
    ):
        _patch_overlay(
            monkeypatch,
            policy=_stub_policy(
                path_globs=["db/migrations/*.sql"],
                upstream_max=25,
                action=MigrationCollisionAction.TAKE_CURRENT,
            ),
        )
        config = _make_config(tmp_path)
        ctx = _make_ctx(config)
        state = MergeState(config=config)
        state.merge_base_commit = "deadbeef"
        phase = InitializePhase()

        consumed = phase._apply_forks_profile_routing(
            state,
            ctx,
            {"db/migrations/030_payments.sql": FileChangeCategory.D_MISSING},
        )
        assert consumed == {"db/migrations/030_payments.sql"}
        rec = state.file_decision_records["db/migrations/030_payments.sql"]
        assert rec.decision == MergeDecision.TAKE_CURRENT
        assert "take_current" in rec.rationale

    def test_safe_upstream_migration_not_consumed(self, tmp_path: Path, monkeypatch):
        _patch_overlay(
            monkeypatch,
            policy=_stub_policy(path_globs=["db/migrations/*.sql"], upstream_max=25),
        )
        config = _make_config(tmp_path)
        ctx = _make_ctx(config)
        state = MergeState(config=config)
        state.merge_base_commit = "deadbeef"
        phase = InitializePhase()

        consumed = phase._apply_forks_profile_routing(
            state,
            ctx,
            {"db/migrations/010_safe.sql": FileChangeCategory.D_MISSING},
        )
        assert consumed == set()
        assert state.file_decision_records == {}

    def test_b_class_migration_not_consumed(self, tmp_path: Path, monkeypatch):
        _patch_overlay(
            monkeypatch,
            policy=_stub_policy(path_globs=["db/migrations/*.sql"], upstream_max=25),
        )
        config = _make_config(tmp_path)
        ctx = _make_ctx(config)
        state = MergeState(config=config)
        state.merge_base_commit = "deadbeef"
        phase = InitializePhase()

        consumed = phase._apply_forks_profile_routing(
            state,
            ctx,
            {"db/migrations/030_payments.sql": FileChangeCategory.B},
        )
        assert consumed == set()
        assert state.file_decision_records == {}

    def test_rewritten_module_takes_priority_over_migration_policy(
        self, tmp_path: Path, monkeypatch
    ):
        _write_profile(
            tmp_path,
            (
                "rewritten_modules:\n"
                '  - path: "db/migrations/030_payments.sql"\n'
                "    policy: take_current_with_diff_note\n"
            ),
        )
        _patch_overlay(
            monkeypatch,
            policy=_stub_policy(path_globs=["db/migrations/*.sql"], upstream_max=25),
        )
        config = _make_config(tmp_path)
        ctx = _make_ctx(config)
        state = MergeState(config=config)
        state.merge_base_commit = "deadbeef"
        phase = InitializePhase()

        consumed = phase._apply_forks_profile_routing(
            state,
            ctx,
            {"db/migrations/030_payments.sql": FileChangeCategory.D_MISSING},
        )
        assert consumed == {"db/migrations/030_payments.sql"}
        rec = state.file_decision_records["db/migrations/030_payments.sql"]
        assert rec.decision == MergeDecision.TAKE_CURRENT
        assert "take_current_with_diff_note" in rec.rationale
        assert "migration_policy" not in rec.rationale
