import pytest
from src.models.config import MergeConfig
from src.models.state import MergeState
from src.models.plan import MergePlan, MergePhase, RiskSummary, PhaseFileBatch
from src.core.read_only_state_view import ReadOnlyStateView
from datetime import datetime
from uuid import uuid4


def _make_state() -> MergeState:
    config = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/fork")
    return MergeState(config=config)


def _make_plan() -> MergePlan:
    return MergePlan(
        created_at=datetime.now(),
        upstream_ref="upstream/main",
        fork_ref="feature/fork",
        merge_base_commit="abc123",
        phases=[
            PhaseFileBatch(
                batch_id=str(uuid4()),
                phase=MergePhase.AUTO_MERGE,
                file_paths=["src/main.py"],
                risk_level="auto_safe",
            )
        ],
        risk_summary=RiskSummary(
            total_files=1,
            auto_safe_count=1,
            auto_risky_count=0,
            human_required_count=0,
            deleted_only_count=0,
            binary_count=0,
            excluded_count=0,
            estimated_auto_merge_rate=1.0,
        ),
        project_context_summary="Test project",
    )


def test_readonly_view_can_read():
    state = _make_state()
    state.merge_plan = _make_plan()
    view = ReadOnlyStateView(state)
    assert view.merge_plan is not None
    assert view.status == state.status


def test_readonly_view_blocks_write():
    state = _make_state()
    view = ReadOnlyStateView(state)
    with pytest.raises(PermissionError, match="Read-only view"):
        view.merge_plan = None


def test_readonly_view_blocks_write_to_lists():
    state = _make_state()
    view = ReadOnlyStateView(state)
    with pytest.raises(PermissionError):
        view.plan_disputes = []


def test_readonly_view_returns_deep_copy():
    state = _make_state()
    state.file_classifications["test.py"] = "auto_safe"
    view = ReadOnlyStateView(state)

    copy = view.file_classifications
    copy["test.py"] = "human_required"

    assert state.file_classifications["test.py"] == "auto_safe", (
        "Modifying the copy must not affect the original state"
    )


def test_readonly_view_status_is_readable():
    state = _make_state()
    view = ReadOnlyStateView(state)
    assert view.status is not None


def test_readonly_view_config_is_deep_copy():
    state = _make_state()
    view = ReadOnlyStateView(state)
    config_copy = view.config
    assert config_copy.upstream_ref == state.config.upstream_ref
