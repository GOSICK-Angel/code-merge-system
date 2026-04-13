import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.checkpoint import Checkpoint
from src.core.message_bus import MessageBus
from src.core.phase_runner import PhaseRunner
from src.core.state_machine import StateMachine, VALID_TRANSITIONS
from src.models.config import MergeConfig, ThresholdConfig
from src.models.conflict import ConflictAnalysis, ConflictType
from src.models.decision import MergeDecision
from src.models.diff import FileDiff, FileChangeCategory, FileStatus, RiskLevel
from src.models.judge import JudgeVerdict, VerdictType
from src.models.message import AgentMessage, AgentType, MessageType
from src.models.plan import MergePlan, MergePhase, PhaseFileBatch, RiskSummary
from src.models.plan_judge import PlanJudgeResult, PlanJudgeVerdict
from src.models.state import MergeState, SystemStatus


def _make_config(output_dir: str = "./outputs") -> MergeConfig:
    from src.models.config import OutputConfig

    return MergeConfig(
        upstream_ref="upstream/main",
        fork_ref="feature/fork",
        output=OutputConfig(directory=output_dir),
    )


def _make_state(config: MergeConfig | None = None) -> MergeState:
    if config is None:
        config = _make_config()
    return MergeState(config=config)


def _make_merge_plan(
    auto_safe_files: list[str] | None = None,
    risky_files: list[str] | None = None,
) -> MergePlan:
    phases: list[PhaseFileBatch] = []
    if auto_safe_files:
        phases.append(
            PhaseFileBatch(
                batch_id="b1",
                phase=MergePhase.AUTO_MERGE,
                file_paths=auto_safe_files,
                risk_level=RiskLevel.AUTO_SAFE,
            )
        )
    if risky_files:
        phases.append(
            PhaseFileBatch(
                batch_id="b2",
                phase=MergePhase.CONFLICT_ANALYSIS,
                file_paths=risky_files,
                risk_level=RiskLevel.AUTO_RISKY,
            )
        )
    return MergePlan(
        created_at=datetime.now(),
        upstream_ref="upstream/main",
        fork_ref="feature/fork",
        merge_base_commit="abc123",
        phases=phases,
        risk_summary=RiskSummary(
            total_files=len(auto_safe_files or []) + len(risky_files or []),
            auto_safe_count=len(auto_safe_files or []),
            auto_risky_count=len(risky_files or []),
            human_required_count=0,
            deleted_only_count=0,
            binary_count=0,
            excluded_count=0,
            estimated_auto_merge_rate=0.5,
        ),
        project_context_summary="test",
    )


def _make_file_diff(
    file_path: str = "src/foo.py",
    risk_level: RiskLevel = RiskLevel.AUTO_SAFE,
    is_security_sensitive: bool = False,
) -> FileDiff:
    return FileDiff(
        file_path=file_path,
        file_status=FileStatus.MODIFIED,
        risk_level=risk_level,
        risk_score=0.1,
        lines_added=5,
        lines_deleted=3,
        is_security_sensitive=is_security_sensitive,
    )


def _make_agent_message(
    sender: AgentType = AgentType.PLANNER,
    receiver: AgentType = AgentType.ORCHESTRATOR,
    phase: MergePhase = MergePhase.ANALYSIS,
    message_type: MessageType = MessageType.INFO,
    subject: str = "test",
    payload: dict | None = None,
) -> AgentMessage:
    return AgentMessage(
        sender=sender,
        receiver=receiver,
        phase=phase,
        message_type=message_type,
        subject=subject,
        payload=payload or {},
    )


def _make_plan_judge_verdict(result: PlanJudgeResult) -> PlanJudgeVerdict:
    return PlanJudgeVerdict(
        result=result,
        summary="test verdict",
        judge_model="gpt-4o",
        timestamp=datetime.now(),
    )


def _make_conflict_analysis(
    file_path: str = "src/foo.py",
    confidence: float = 0.9,
    conflict_type: ConflictType = ConflictType.CONCURRENT_MODIFICATION,
    can_coexist: bool = True,
    is_security_sensitive: bool = False,
    recommended_strategy: MergeDecision = MergeDecision.SEMANTIC_MERGE,
) -> ConflictAnalysis:
    return ConflictAnalysis(
        file_path=file_path,
        conflict_points=[],
        overall_confidence=confidence,
        recommended_strategy=recommended_strategy,
        conflict_type=conflict_type,
        can_coexist=can_coexist,
        is_security_sensitive=is_security_sensitive,
        confidence=confidence,
    )


class TestStateMachine:
    def test_valid_transition_initialized_to_planning(self):
        sm = StateMachine()
        state = _make_state()
        sm.transition(state, SystemStatus.PLANNING, "start")
        assert state.status == SystemStatus.PLANNING

    def test_valid_transition_planning_to_plan_reviewing(self):
        sm = StateMachine()
        state = _make_state()
        state.status = SystemStatus.PLANNING
        sm.transition(state, SystemStatus.PLAN_REVIEWING, "plan done")
        assert state.status == SystemStatus.PLAN_REVIEWING

    def test_valid_transition_plan_reviewing_to_auto_merging(self):
        sm = StateMachine()
        state = _make_state()
        state.status = SystemStatus.PLAN_REVIEWING
        sm.transition(state, SystemStatus.AUTO_MERGING, "approved")
        assert state.status == SystemStatus.AUTO_MERGING

    def test_valid_transition_auto_merging_to_analyzing_conflicts(self):
        sm = StateMachine()
        state = _make_state()
        state.status = SystemStatus.AUTO_MERGING
        sm.transition(state, SystemStatus.ANALYZING_CONFLICTS, "has risky files")
        assert state.status == SystemStatus.ANALYZING_CONFLICTS

    def test_valid_transition_analyzing_conflicts_to_judge_reviewing(self):
        sm = StateMachine()
        state = _make_state()
        state.status = SystemStatus.ANALYZING_CONFLICTS
        sm.transition(state, SystemStatus.JUDGE_REVIEWING, "done")
        assert state.status == SystemStatus.JUDGE_REVIEWING

    def test_valid_transition_judge_reviewing_to_generating_report(self):
        sm = StateMachine()
        state = _make_state()
        state.status = SystemStatus.JUDGE_REVIEWING
        sm.transition(state, SystemStatus.GENERATING_REPORT, "pass")
        assert state.status == SystemStatus.GENERATING_REPORT

    def test_valid_transition_generating_report_to_completed(self):
        sm = StateMachine()
        state = _make_state()
        state.status = SystemStatus.GENERATING_REPORT
        sm.transition(state, SystemStatus.COMPLETED, "done")
        assert state.status == SystemStatus.COMPLETED

    def test_invalid_transition_raises_value_error(self):
        sm = StateMachine()
        state = _make_state()
        with pytest.raises(ValueError, match="Invalid state transition"):
            sm.transition(state, SystemStatus.COMPLETED, "skip ahead")

    def test_invalid_transition_completed_to_anything(self):
        sm = StateMachine()
        state = _make_state()
        state.status = SystemStatus.COMPLETED
        with pytest.raises(ValueError):
            sm.transition(state, SystemStatus.PLANNING, "retry")

    def test_invalid_transition_failed_to_anything(self):
        sm = StateMachine()
        state = _make_state()
        state.status = SystemStatus.FAILED
        with pytest.raises(ValueError):
            sm.transition(state, SystemStatus.PLANNING, "retry")

    def test_can_transition_returns_true_for_valid(self):
        sm = StateMachine()
        assert (
            sm.can_transition(SystemStatus.INITIALIZED, SystemStatus.PLANNING) is True
        )

    def test_can_transition_returns_false_for_invalid(self):
        sm = StateMachine()
        assert (
            sm.can_transition(SystemStatus.INITIALIZED, SystemStatus.COMPLETED) is False
        )

    def test_can_transition_from_failed_always_false(self):
        sm = StateMachine()
        for target in SystemStatus:
            assert sm.can_transition(SystemStatus.FAILED, target) is False

    def test_get_valid_transitions_initialized(self):
        sm = StateMachine()
        valid = sm.get_valid_transitions(SystemStatus.INITIALIZED)
        assert SystemStatus.PLANNING in valid
        assert SystemStatus.FAILED in valid

    def test_get_valid_transitions_completed_empty(self):
        sm = StateMachine()
        valid = sm.get_valid_transitions(SystemStatus.COMPLETED)
        assert valid == []

    def test_transition_appends_message_to_state(self):
        sm = StateMachine()
        state = _make_state()
        sm.transition(state, SystemStatus.PLANNING, "test reason")
        assert len(state.messages) == 1
        msg = state.messages[0]
        assert msg["type"] == "state_transition"
        assert msg["from"] == "initialized"
        assert msg["to"] == "planning"
        assert msg["reason"] == "test reason"

    def test_transition_updates_updated_at(self):
        sm = StateMachine()
        state = _make_state()
        before = state.updated_at
        sm.transition(state, SystemStatus.PLANNING, "update")
        assert state.updated_at >= before

    def test_plan_reviewing_can_transition_to_planning_for_replan(self):
        sm = StateMachine()
        assert (
            sm.can_transition(SystemStatus.PLAN_REVIEWING, SystemStatus.PLANNING)
            is True
        )

    def test_plan_reviewing_can_transition_to_awaiting_human(self):
        sm = StateMachine()
        assert (
            sm.can_transition(SystemStatus.PLAN_REVIEWING, SystemStatus.AWAITING_HUMAN)
            is True
        )

    def test_paused_can_transition_to_most_states(self):
        sm = StateMachine()
        valid = sm.get_valid_transitions(SystemStatus.PAUSED)
        assert SystemStatus.PLANNING in valid
        assert SystemStatus.AUTO_MERGING in valid
        assert SystemStatus.FAILED in valid

    def test_all_statuses_have_entry_in_valid_transitions(self):
        for status in SystemStatus:
            assert status in VALID_TRANSITIONS


class TestCheckpoint:
    def test_save_creates_file(self, tmp_path):
        cp = Checkpoint(str(tmp_path))
        state = _make_state(_make_config(str(tmp_path)))
        path = cp.save(state, "test_tag")
        assert path.exists()
        assert f"run_{state.run_id}_test_tag.json" == path.name

    def test_save_writes_valid_json(self, tmp_path):
        cp = Checkpoint(str(tmp_path))
        state = _make_state(_make_config(str(tmp_path)))
        path = cp.save(state, "json_check")
        data = json.loads(path.read_text())
        assert data["run_id"] == state.run_id

    def test_save_updates_checkpoint_path_on_state(self, tmp_path):
        cp = Checkpoint(str(tmp_path))
        state = _make_state(_make_config(str(tmp_path)))
        path = cp.save(state, "path_update")
        assert state.checkpoint_path == str(path)

    def test_save_creates_latest_link(self, tmp_path):
        cp = Checkpoint(str(tmp_path))
        state = _make_state(_make_config(str(tmp_path)))
        cp.save(state, "first")
        latest = cp.checkpoint_dir / f"run_{state.run_id}_latest.json"
        assert latest.exists()

    def test_save_multiple_tags_updates_latest(self, tmp_path):
        cp = Checkpoint(str(tmp_path))
        state = _make_state(_make_config(str(tmp_path)))
        cp.save(state, "first")
        cp.save(state, "second")
        latest = cp.checkpoint_dir / f"run_{state.run_id}_latest.json"
        assert latest.exists()

    def test_load_restores_state(self, tmp_path):
        cp = Checkpoint(str(tmp_path))
        state = _make_state(_make_config(str(tmp_path)))
        state.status = SystemStatus.PLANNING
        path = cp.save(state, "load_test")
        restored = cp.load(path)
        assert restored.run_id == state.run_id
        assert restored.status == SystemStatus.PLANNING

    def test_load_nonexistent_raises_file_not_found(self, tmp_path):
        cp = Checkpoint(str(tmp_path))
        with pytest.raises(FileNotFoundError):
            cp.load(tmp_path / "does_not_exist.json")

    def test_list_checkpoints_returns_sorted_paths(self, tmp_path):
        cp = Checkpoint(str(tmp_path))
        state = _make_state(_make_config(str(tmp_path)))
        cp.save(state, "alpha")
        cp.save(state, "beta")
        paths = cp.list_checkpoints(state.run_id)
        assert len(paths) == 2
        names = [p.name for p in paths]
        assert any("alpha" in n for n in names)
        assert any("beta" in n for n in names)

    def test_list_checkpoints_excludes_latest_link(self, tmp_path):
        cp = Checkpoint(str(tmp_path))
        state = _make_state(_make_config(str(tmp_path)))
        cp.save(state, "one")
        paths = cp.list_checkpoints(state.run_id)
        for p in paths:
            assert "_latest" not in p.name

    def test_list_checkpoints_empty_for_unknown_run_id(self, tmp_path):
        cp = Checkpoint(str(tmp_path))
        paths = cp.list_checkpoints("nonexistent-run-id")
        assert paths == []

    def test_get_latest_returns_most_recent(self, tmp_path):
        cp = Checkpoint(str(tmp_path))
        state = _make_state(_make_config(str(tmp_path)))
        cp.save(state, "first")
        cp.save(state, "second")
        result = cp.get_latest(state.run_id)
        assert result is not None
        assert result.exists()

    def test_get_latest_returns_none_for_unknown_run(self, tmp_path):
        cp = Checkpoint(str(tmp_path))
        result = cp.get_latest("no-such-run")
        assert result is None

    def test_checkpoint_dir_created_on_init(self, tmp_path):
        subdir = tmp_path / "deep" / "nested"
        cp = Checkpoint(str(subdir))
        assert cp.checkpoint_dir.exists()

    def test_register_signal_handler_does_not_raise(self, tmp_path):
        cp = Checkpoint(str(tmp_path))
        state = _make_state(_make_config(str(tmp_path)))
        cp.register_signal_handler(state)


class TestMessageBus:
    def test_publish_stores_message(self):
        bus = MessageBus()
        msg = _make_agent_message()
        bus.publish(msg)
        assert msg in bus._messages

    def test_publish_puts_message_in_queue(self):
        bus = MessageBus()
        msg = _make_agent_message()
        bus.publish(msg)
        assert not bus._queue.empty()

    def test_subscribe_and_callback_called_on_publish(self):
        bus = MessageBus()
        received = []
        bus.subscribe(AgentType.ORCHESTRATOR, lambda m: received.append(m))
        msg = _make_agent_message(receiver=AgentType.ORCHESTRATOR)
        bus.publish(msg)
        assert len(received) == 1
        assert received[0] is msg

    def test_subscribe_callback_not_called_for_different_receiver(self):
        bus = MessageBus()
        received = []
        bus.subscribe(AgentType.PLANNER, lambda m: received.append(m))
        msg = _make_agent_message(receiver=AgentType.ORCHESTRATOR)
        bus.publish(msg)
        assert len(received) == 0

    def test_publish_broadcast_calls_all_subscribers_except_sender(self):
        bus = MessageBus()
        planner_received = []
        executor_received = []
        bus.subscribe(AgentType.PLANNER, lambda m: planner_received.append(m))
        bus.subscribe(AgentType.EXECUTOR, lambda m: executor_received.append(m))
        msg = _make_agent_message(
            sender=AgentType.ORCHESTRATOR,
            receiver=AgentType.BROADCAST,
        )
        bus.publish(msg)
        assert len(planner_received) == 1
        assert len(executor_received) == 1

    def test_publish_broadcast_does_not_call_sender_subscriber(self):
        bus = MessageBus()
        sender_received = []
        bus.subscribe(AgentType.ORCHESTRATOR, lambda m: sender_received.append(m))
        msg = _make_agent_message(
            sender=AgentType.ORCHESTRATOR,
            receiver=AgentType.BROADCAST,
        )
        bus.publish(msg)
        assert len(sender_received) == 0

    def test_get_messages_returns_all_without_filter(self):
        bus = MessageBus()
        m1 = _make_agent_message(receiver=AgentType.PLANNER)
        m2 = _make_agent_message(receiver=AgentType.EXECUTOR)
        bus.publish(m1)
        bus.publish(m2)
        results = bus.get_messages()
        assert len(results) == 2

    def test_get_messages_filters_by_receiver(self):
        bus = MessageBus()
        m1 = _make_agent_message(receiver=AgentType.PLANNER)
        m2 = _make_agent_message(receiver=AgentType.EXECUTOR)
        bus.publish(m1)
        bus.publish(m2)
        results = bus.get_messages(receiver=AgentType.PLANNER)
        assert len(results) == 1
        assert results[0] is m1

    def test_get_messages_includes_broadcast_for_any_receiver(self):
        bus = MessageBus()
        broadcast_msg = _make_agent_message(receiver=AgentType.BROADCAST)
        bus.publish(broadcast_msg)
        results = bus.get_messages(receiver=AgentType.PLANNER)
        assert broadcast_msg in results

    def test_get_messages_unprocessed_only(self):
        bus = MessageBus()
        m1 = _make_agent_message()
        m2 = _make_agent_message()
        bus.publish(m1)
        bus.publish(m2)
        bus.mark_processed(m1.message_id)
        results = bus.get_messages(unprocessed_only=True)
        assert m1 not in results
        assert m2 in results

    def test_mark_processed_sets_flag(self):
        bus = MessageBus()
        msg = _make_agent_message()
        bus.publish(msg)
        bus.mark_processed(msg.message_id)
        assert bus._messages[0].is_processed is True

    def test_mark_processed_unknown_id_does_nothing(self):
        bus = MessageBus()
        msg = _make_agent_message()
        bus.publish(msg)
        bus.mark_processed("unknown-id")
        assert bus._messages[0].is_processed is False

    def test_clear_empties_messages_and_queue(self):
        bus = MessageBus()
        bus.publish(_make_agent_message())
        bus.clear()
        assert len(bus._messages) == 0
        assert bus._queue.empty()

    async def test_wait_for_message_returns_published_message(self):
        bus = MessageBus()
        msg = _make_agent_message()
        bus.publish(msg)
        result = await bus.wait_for_message(timeout=1.0)
        assert result is msg

    async def test_wait_for_message_times_out(self):
        bus = MessageBus()
        result = await bus.wait_for_message(timeout=0.05)
        assert result is None

    def test_subscriber_exception_does_not_propagate(self):
        bus = MessageBus()

        def bad_callback(m):
            raise RuntimeError("oops")

        bus.subscribe(AgentType.ORCHESTRATOR, bad_callback)
        msg = _make_agent_message(receiver=AgentType.ORCHESTRATOR)
        bus.publish(msg)

    def test_multiple_subscribers_for_same_agent_type(self):
        bus = MessageBus()
        calls_a = []
        calls_b = []
        bus.subscribe(AgentType.JUDGE, lambda m: calls_a.append(m))
        bus.subscribe(AgentType.JUDGE, lambda m: calls_b.append(m))
        msg = _make_agent_message(receiver=AgentType.JUDGE)
        bus.publish(msg)
        assert len(calls_a) == 1
        assert len(calls_b) == 1


class TestPhaseRunner:
    async def test_run_sequential_processes_all_items(self):
        runner = PhaseRunner(batch_size=10, max_concurrency=5)

        async def handler(x):
            return x * 2

        results = await runner.run_sequential([1, 2, 3], handler)
        assert sorted(results) == [2, 4, 6]

    async def test_run_sequential_returns_results_in_order(self):
        runner = PhaseRunner(batch_size=10, max_concurrency=5)
        order = []

        async def handler(x):
            order.append(x)
            return x

        results = await runner.run_sequential([10, 20, 30], handler)
        assert results == [10, 20, 30]
        assert order == [10, 20, 30]

    async def test_run_sequential_empty_list(self):
        runner = PhaseRunner()
        results = await runner.run_sequential([], lambda x: None)
        assert results == []

    async def test_run_parallel_processes_all_items(self):
        runner = PhaseRunner(batch_size=10, max_concurrency=5)

        async def handler(x):
            return x * 2

        results = await runner.run_parallel([1, 2, 3, 4, 5], handler)
        assert sorted(results) == [2, 4, 6, 8, 10]

    async def test_run_parallel_empty_list(self):
        runner = PhaseRunner()

        async def handler(x):
            return x

        results = await runner.run_parallel([], handler)
        assert results == []

    async def test_run_parallel_respects_max_concurrency(self):
        runner = PhaseRunner(max_concurrency=2)
        active = []
        peak = [0]

        async def handler(x):
            active.append(x)
            peak[0] = max(peak[0], len(active))
            await asyncio.sleep(0.01)
            active.pop()
            return x

        await runner.run_parallel(list(range(10)), handler)
        assert peak[0] <= 2

    async def test_run_parallel_captures_exceptions(self):
        runner = PhaseRunner()

        async def handler(x):
            if x == 2:
                raise ValueError("bad item")
            return x

        results = await runner.run_parallel([1, 2, 3], handler)
        errors = [r for r in results if isinstance(r, Exception)]
        assert len(errors) == 1
        assert isinstance(errors[0], ValueError)

    async def test_run_batched_parallel_processes_all(self):
        runner = PhaseRunner(batch_size=3, max_concurrency=2)

        async def handler(x):
            return x + 1

        results = await runner.run_batched(list(range(9)), handler, parallel=True)
        assert sorted(results) == list(range(1, 10))

    async def test_run_batched_sequential_processes_all(self):
        runner = PhaseRunner(batch_size=3, max_concurrency=5)

        async def handler(x):
            return x * 2

        results = await runner.run_batched(list(range(6)), handler, parallel=False)
        assert sorted(results) == [0, 2, 4, 6, 8, 10]

    async def test_run_batched_calls_on_batch_complete(self):
        runner = PhaseRunner(batch_size=2, max_concurrency=2)
        batch_callbacks = []

        async def on_batch(batch_results):
            batch_callbacks.append(batch_results)

        async def handler(x):
            return x

        await runner.run_batched([1, 2, 3, 4], handler, on_batch_complete=on_batch)
        assert len(batch_callbacks) == 2

    async def test_run_batched_empty_list(self):
        runner = PhaseRunner()

        async def handler(x):
            return x

        results = await runner.run_batched([], handler)
        assert results == []


class TestOrchestratorSelectMergeStrategy:
    def test_low_confidence_escalates_human(self):
        from src.core.orchestrator import _select_merge_strategy

        thresholds = ThresholdConfig()
        analysis = _make_conflict_analysis(confidence=0.3)
        result = _select_merge_strategy(analysis, thresholds)
        assert result == MergeDecision.ESCALATE_HUMAN

    def test_logic_contradiction_below_90_escalates(self):
        from src.core.orchestrator import _select_merge_strategy

        thresholds = ThresholdConfig()
        analysis = _make_conflict_analysis(
            confidence=0.85,
            conflict_type=ConflictType.LOGIC_CONTRADICTION,
        )
        result = _select_merge_strategy(analysis, thresholds)
        assert result == MergeDecision.ESCALATE_HUMAN

    def test_semantic_equivalent_high_confidence_takes_target(self):
        from src.core.orchestrator import _select_merge_strategy

        thresholds = ThresholdConfig(auto_merge_confidence=0.85)
        analysis = _make_conflict_analysis(
            confidence=0.95,
            conflict_type=ConflictType.SEMANTIC_EQUIVALENT,
        )
        result = _select_merge_strategy(analysis, thresholds)
        assert result == MergeDecision.TAKE_TARGET

    def test_can_coexist_high_confidence_semantic_merge(self):
        from src.core.orchestrator import _select_merge_strategy

        thresholds = ThresholdConfig(auto_merge_confidence=0.85)
        analysis = _make_conflict_analysis(
            confidence=0.92,
            can_coexist=True,
            conflict_type=ConflictType.CONCURRENT_MODIFICATION,
            recommended_strategy=MergeDecision.SEMANTIC_MERGE,
        )
        result = _select_merge_strategy(analysis, thresholds)
        assert result == MergeDecision.SEMANTIC_MERGE

    def test_security_sensitive_escalates_human(self):
        from src.core.orchestrator import _select_merge_strategy

        thresholds = ThresholdConfig(auto_merge_confidence=0.85, human_escalation=0.60)
        analysis = _make_conflict_analysis(
            confidence=0.75,
            can_coexist=False,
            is_security_sensitive=True,
        )
        result = _select_merge_strategy(analysis, thresholds)
        assert result == MergeDecision.ESCALATE_HUMAN

    def test_high_confidence_uses_recommended_strategy(self):
        from src.core.orchestrator import _select_merge_strategy

        thresholds = ThresholdConfig(auto_merge_confidence=0.85)
        analysis = _make_conflict_analysis(
            confidence=0.95,
            can_coexist=False,
            is_security_sensitive=False,
            recommended_strategy=MergeDecision.TAKE_CURRENT,
        )
        result = _select_merge_strategy(analysis, thresholds)
        assert result == MergeDecision.TAKE_CURRENT

    def test_insufficient_confidence_escalates_human_fallback(self):
        from src.core.orchestrator import _select_merge_strategy

        thresholds = ThresholdConfig(auto_merge_confidence=0.85, human_escalation=0.60)
        analysis = _make_conflict_analysis(
            confidence=0.70,
            can_coexist=False,
            is_security_sensitive=False,
        )
        result = _select_merge_strategy(analysis, thresholds)
        assert result == MergeDecision.ESCALATE_HUMAN


class TestBuildHumanDecisionRequest:
    def test_returns_human_decision_request(self):
        from src.core.orchestrator import _build_human_decision_request

        fd = _make_file_diff(is_security_sensitive=True)
        analysis = _make_conflict_analysis()
        req = _build_human_decision_request(fd, analysis)
        assert req.file_path == fd.file_path

    def test_priority_1_for_security_sensitive(self):
        from src.core.orchestrator import _build_human_decision_request

        fd = _make_file_diff(is_security_sensitive=True)
        analysis = _make_conflict_analysis()
        req = _build_human_decision_request(fd, analysis)
        assert req.priority == 1

    def test_priority_5_for_non_security(self):
        from src.core.orchestrator import _build_human_decision_request

        fd = _make_file_diff(is_security_sensitive=False)
        analysis = _make_conflict_analysis()
        req = _build_human_decision_request(fd, analysis)
        assert req.priority == 5

    def test_has_four_options(self):
        from src.core.orchestrator import _build_human_decision_request

        fd = _make_file_diff()
        analysis = _make_conflict_analysis()
        req = _build_human_decision_request(fd, analysis)
        assert len(req.options) == 4

    def test_options_cover_all_decisions(self):
        from src.core.orchestrator import _build_human_decision_request

        fd = _make_file_diff()
        analysis = _make_conflict_analysis()
        req = _build_human_decision_request(fd, analysis)
        decisions = {opt.decision for opt in req.options}
        assert MergeDecision.TAKE_CURRENT in decisions
        assert MergeDecision.TAKE_TARGET in decisions
        assert MergeDecision.SEMANTIC_MERGE in decisions
        assert MergeDecision.MANUAL_PATCH in decisions

    def test_analyst_confidence_set(self):
        from src.core.orchestrator import _build_human_decision_request

        fd = _make_file_diff()
        analysis = _make_conflict_analysis(confidence=0.77)
        req = _build_human_decision_request(fd, analysis)
        assert req.analyst_confidence == 0.77


class TestParseFileStatus:
    def test_added(self):
        from src.core.orchestrator import _parse_file_status
        from src.models.diff import FileStatus

        assert _parse_file_status("A") == FileStatus.ADDED

    def test_modified(self):
        from src.core.orchestrator import _parse_file_status
        from src.models.diff import FileStatus

        assert _parse_file_status("M") == FileStatus.MODIFIED

    def test_deleted(self):
        from src.core.orchestrator import _parse_file_status
        from src.models.diff import FileStatus

        assert _parse_file_status("D") == FileStatus.DELETED

    def test_renamed(self):
        from src.core.orchestrator import _parse_file_status
        from src.models.diff import FileStatus

        assert _parse_file_status("R") == FileStatus.RENAMED

    def test_unknown_defaults_to_modified(self):
        from src.core.orchestrator import _parse_file_status
        from src.models.diff import FileStatus

        assert _parse_file_status("X") == FileStatus.MODIFIED

    def test_lowercase_handled(self):
        from src.core.orchestrator import _parse_file_status
        from src.models.diff import FileStatus

        assert _parse_file_status("a") == FileStatus.ADDED


class TestPhaseClasses:
    """Tests for the extracted Phase classes.

    After the A3 refactor the Orchestrator delegates to Phase classes.
    Each test creates a lightweight PhaseContext with mocked agents and
    invokes the Phase directly — no need to construct a full Orchestrator.
    """

    @staticmethod
    def _make_ctx(config, **overrides):
        from src.core.phases.base import PhaseContext
        from src.core.state_machine import StateMachine
        from src.core.message_bus import MessageBus
        from src.core.checkpoint import Checkpoint
        from src.core.phase_runner import PhaseRunner
        from src.memory.store import MemoryStore
        from src.memory.summarizer import PhaseSummarizer

        defaults = dict(
            config=config,
            git_tool=MagicMock(),
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
        defaults.update(overrides)
        return PhaseContext(**defaults)

    async def test_run_initialize_then_planning(self, tmp_path):
        from src.core.orchestrator import Orchestrator

        config = _make_config(str(tmp_path))
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
        mock_agents["planner"].run = AsyncMock()
        mock_agents["planner_judge"].review_plan = AsyncMock(
            return_value=_make_plan_judge_verdict(PlanJudgeResult.APPROVED)
        )
        mock_agents["conflict_analyst"].run = AsyncMock()
        judge_msg = MagicMock()
        judge_msg.payload = {}
        mock_agents["judge"].run = AsyncMock(return_value=judge_msg)

        with patch("src.core.orchestrator.GitTool") as MockGit:
            mock_git = MockGit.return_value
            mock_git.get_merge_base.return_value = "abc123"
            mock_git.get_changed_files.return_value = []

            orch = Orchestrator(config, agents=mock_agents)

            with (
                patch("src.core.phases.report_generation.write_json_report"),
                patch("src.core.phases.report_generation.write_markdown_report"),
            ):
                state = _make_state(config)
                state.merge_plan = _make_merge_plan()
                result = await orch.run(state)

        assert result.status in (
            SystemStatus.COMPLETED,
            SystemStatus.AWAITING_HUMAN,
            SystemStatus.FAILED,
            SystemStatus.GENERATING_REPORT,
        )

    async def test_initialize_sets_file_diffs(self, tmp_path):
        from src.core.phases.initialize import InitializePhase

        config = _make_config(str(tmp_path))
        mock_git = MagicMock()
        mock_git.get_merge_base.return_value = "abc123"
        mock_git.get_changed_files.return_value = [("M", "src/foo.py")]
        mock_git.get_unified_diff.return_value = "+line\n"

        ctx = self._make_ctx(config, git_tool=mock_git)

        with (
            patch("src.core.phases.initialize.build_file_diff") as mock_build,
            patch("src.core.phases.initialize.detect_language", return_value="python"),
            patch(
                "src.core.phases.initialize.is_security_sensitive", return_value=False
            ),
            patch("src.core.phases.initialize.compute_risk_score", return_value=0.1),
            patch(
                "src.core.phases.initialize.classify_file",
                return_value=RiskLevel.AUTO_SAFE,
            ),
            patch(
                "src.core.phases.initialize.classify_all_files",
                return_value={"src/foo.py": FileChangeCategory.C},
            ),
            patch(
                "src.core.phases.initialize.category_summary",
                return_value={
                    "unchanged": 0,
                    "upstream_only": 0,
                    "both_changed": 1,
                    "upstream_new": 0,
                    "current_only_file": 0,
                    "current_only_change": 0,
                },
            ),
        ):
            mock_fd = _make_file_diff()
            mock_build.return_value = mock_fd

            state = _make_state(config)
            phase = InitializePhase()
            await phase.execute(state, ctx)

        assert state.status == SystemStatus.PLANNING
        assert len(getattr(state, "_file_diffs", [])) == 1
        assert state.file_categories == {"src/foo.py": FileChangeCategory.C}

    async def test_planning_transitions_to_plan_reviewing(self, tmp_path):
        from src.core.phases.planning import PlanningPhase

        config = _make_config(str(tmp_path))
        mock_planner = MagicMock()
        mock_planner.run = AsyncMock()
        ctx = self._make_ctx(config, agents={"planner": mock_planner})

        state = _make_state(config)
        state.status = SystemStatus.PLANNING
        phase = PlanningPhase()
        await phase.execute(state, ctx)

        assert state.status == SystemStatus.PLAN_REVIEWING

    async def test_planning_failure_marks_failed_phase(self, tmp_path):
        from src.core.phases.planning import PlanningPhase

        config = _make_config(str(tmp_path))
        mock_planner = MagicMock()
        mock_planner.run = AsyncMock(side_effect=RuntimeError("planner failed"))
        ctx = self._make_ctx(config, agents={"planner": mock_planner})

        state = _make_state(config)
        state.status = SystemStatus.PLANNING
        phase = PlanningPhase()
        with pytest.raises(RuntimeError):
            await phase.execute(state, ctx)

        assert state.phase_results[MergePhase.ANALYSIS.value].status == "failed"

    async def test_plan_review_approved_transitions_to_awaiting_human(self, tmp_path):
        from src.core.phases.plan_review import PlanReviewPhase

        config = _make_config(str(tmp_path))
        mock_pj = MagicMock()
        mock_pj.review_plan = AsyncMock(
            return_value=_make_plan_judge_verdict(PlanJudgeResult.APPROVED)
        )
        ctx = self._make_ctx(
            config, agents={"planner": MagicMock(), "planner_judge": mock_pj}
        )

        state = _make_state(config)
        state.status = SystemStatus.PLAN_REVIEWING
        state.merge_plan = _make_merge_plan()
        phase = PlanReviewPhase()
        await phase.execute(state, ctx)

        assert state.status == SystemStatus.AWAITING_HUMAN
        assert len(state.plan_review_log) == 1
        assert state.plan_review_log[0].verdict_result == PlanJudgeResult.APPROVED

    async def test_plan_review_exceeds_max_rounds_proceeds(self, tmp_path):
        from src.core.phases.plan_review import PlanReviewPhase
        from src.models.config import OutputConfig

        config = MergeConfig(
            upstream_ref="upstream/main",
            fork_ref="feature/fork",
            max_plan_revision_rounds=1,
            output=OutputConfig(directory=str(tmp_path)),
        )
        mock_pj = MagicMock()
        mock_pj.review_plan = AsyncMock(
            return_value=_make_plan_judge_verdict(PlanJudgeResult.REVISION_NEEDED)
        )
        mock_planner = MagicMock()
        mock_planner.revise_plan = AsyncMock(return_value=_make_merge_plan())
        ctx = self._make_ctx(
            config, agents={"planner": mock_planner, "planner_judge": mock_pj}
        )

        state = _make_state(config)
        state.status = SystemStatus.PLAN_REVIEWING
        state.merge_plan = _make_merge_plan()
        phase = PlanReviewPhase()
        await phase.execute(state, ctx)

        assert state.status == SystemStatus.AWAITING_HUMAN

    async def test_auto_merge_no_risky_skips_to_judge(self, tmp_path):
        from src.core.phases.auto_merge import AutoMergePhase

        config = _make_config(str(tmp_path))
        mock_executor = MagicMock()
        mock_executor.execute_auto_merge = AsyncMock(
            return_value=MagicMock(file_path="src/foo.py")
        )
        ctx = self._make_ctx(config, agents={"executor": mock_executor})

        state = _make_state(config)
        state.status = SystemStatus.AUTO_MERGING
        state.merge_plan = _make_merge_plan(auto_safe_files=["src/foo.py"])
        object.__setattr__(state, "_file_diffs", [_make_file_diff("src/foo.py")])
        phase = AutoMergePhase()
        await phase.execute(state, ctx)

        assert state.status == SystemStatus.JUDGE_REVIEWING

    async def test_auto_merge_no_plan_raises(self, tmp_path):
        from src.core.phases.auto_merge import AutoMergePhase

        config = _make_config(str(tmp_path))
        ctx = self._make_ctx(config, agents={"executor": MagicMock()})

        state = _make_state(config)
        state.status = SystemStatus.AUTO_MERGING
        phase = AutoMergePhase()
        with pytest.raises(ValueError, match="No merge plan"):
            await phase.execute(state, ctx)

    async def test_conflict_analysis_no_human_transitions_to_judge(self, tmp_path):
        from src.core.phases.conflict_analysis import ConflictAnalysisPhase

        config = _make_config(str(tmp_path))
        mock_analyst = MagicMock()
        mock_analyst.run = AsyncMock()
        mock_executor = MagicMock()
        mock_executor.execute_semantic_merge = AsyncMock(
            return_value=MagicMock(file_path="src/foo.py")
        )
        ctx = self._make_ctx(
            config,
            agents={"conflict_analyst": mock_analyst, "executor": mock_executor},
        )

        state = _make_state(config)
        state.status = SystemStatus.ANALYZING_CONFLICTS
        fd = _make_file_diff("src/foo.py")
        object.__setattr__(state, "_file_diffs", [fd])
        state.conflict_analyses["src/foo.py"] = _make_conflict_analysis(
            confidence=0.95, can_coexist=True
        )
        phase = ConflictAnalysisPhase()
        await phase.execute(state, ctx)

        assert state.status == SystemStatus.JUDGE_REVIEWING

    async def test_conflict_analysis_human_needed_transitions_to_awaiting_human(
        self, tmp_path
    ):
        from src.core.phases.conflict_analysis import ConflictAnalysisPhase

        config = _make_config(str(tmp_path))
        mock_analyst = MagicMock()
        mock_analyst.run = AsyncMock()
        ctx = self._make_ctx(
            config,
            agents={"conflict_analyst": mock_analyst, "executor": MagicMock()},
        )

        state = _make_state(config)
        state.status = SystemStatus.ANALYZING_CONFLICTS
        fd = _make_file_diff("src/foo.py")
        object.__setattr__(state, "_file_diffs", [fd])
        state.conflict_analyses["src/foo.py"] = _make_conflict_analysis(
            confidence=0.1,
        )
        phase = ConflictAnalysisPhase()
        await phase.execute(state, ctx)

        assert state.status == SystemStatus.AWAITING_HUMAN

    async def test_judge_pass_verdict_transitions_to_generating_report(self, tmp_path):
        from src.core.phases.judge_review import JudgeReviewPhase

        config = _make_config(str(tmp_path))
        judge_verdict = JudgeVerdict(
            verdict=VerdictType.PASS,
            reviewed_files_count=1,
            passed_files=["src/foo.py"],
            failed_files=[],
            conditional_files=[],
            issues=[],
            critical_issues_count=0,
            high_issues_count=0,
            overall_confidence=0.95,
            summary="all good",
            blocking_issues=[],
            timestamp=datetime.now(),
            judge_model="claude-opus-4-6",
        )
        msg = MagicMock()
        msg.payload = {"verdict": judge_verdict.model_dump()}
        mock_judge = MagicMock()
        mock_judge.run = AsyncMock(return_value=msg)
        mock_judge.verify_customizations = MagicMock(return_value=[])
        mock_judge.build_repair_instructions = MagicMock(return_value=[])
        ctx = self._make_ctx(
            config, agents={"judge": mock_judge, "executor": MagicMock()}
        )

        state = _make_state(config)
        state.status = SystemStatus.JUDGE_REVIEWING
        phase = JudgeReviewPhase()
        await phase.execute(state, ctx)

        assert state.status == SystemStatus.GENERATING_REPORT

    async def test_judge_fail_verdict_transitions_to_awaiting_human(self, tmp_path):
        from src.core.phases.judge_review import JudgeReviewPhase

        config = _make_config(str(tmp_path))
        judge_verdict = JudgeVerdict(
            verdict=VerdictType.FAIL,
            reviewed_files_count=1,
            passed_files=[],
            failed_files=["src/foo.py"],
            conditional_files=[],
            issues=[],
            critical_issues_count=1,
            high_issues_count=0,
            overall_confidence=0.2,
            summary="fail",
            blocking_issues=["critical issue"],
            timestamp=datetime.now(),
            judge_model="claude-opus-4-6",
        )
        msg = MagicMock()
        msg.payload = {"verdict": judge_verdict.model_dump()}
        mock_judge = MagicMock()
        mock_judge.run = AsyncMock(return_value=msg)
        mock_judge.verify_customizations = MagicMock(return_value=[])
        mock_judge.build_repair_instructions = MagicMock(return_value=[])
        ctx = self._make_ctx(
            config, agents={"judge": mock_judge, "executor": MagicMock()}
        )

        state = _make_state(config)
        state.status = SystemStatus.JUDGE_REVIEWING
        phase = JudgeReviewPhase()
        await phase.execute(state, ctx)

        assert state.status == SystemStatus.AWAITING_HUMAN

    async def test_judge_conditional_verdict_awaiting_human(self, tmp_path):
        from src.core.phases.judge_review import JudgeReviewPhase

        config = _make_config(str(tmp_path))
        judge_verdict = JudgeVerdict(
            verdict=VerdictType.CONDITIONAL,
            reviewed_files_count=1,
            passed_files=[],
            failed_files=[],
            conditional_files=["src/foo.py"],
            issues=[],
            critical_issues_count=0,
            high_issues_count=0,
            overall_confidence=0.7,
            summary="conditional",
            blocking_issues=[],
            timestamp=datetime.now(),
            judge_model="claude-opus-4-6",
        )
        msg = MagicMock()
        msg.payload = {"verdict": judge_verdict.model_dump()}
        mock_judge = MagicMock()
        mock_judge.run = AsyncMock(return_value=msg)
        mock_judge.verify_customizations = MagicMock(return_value=[])
        mock_judge.build_repair_instructions = MagicMock(return_value=[])
        ctx = self._make_ctx(
            config, agents={"judge": mock_judge, "executor": MagicMock()}
        )

        state = _make_state(config)
        state.status = SystemStatus.JUDGE_REVIEWING
        phase = JudgeReviewPhase()
        await phase.execute(state, ctx)

        assert state.status == SystemStatus.AWAITING_HUMAN

    async def test_judge_no_verdict_transitions_to_generating_report(self, tmp_path):
        from src.core.phases.judge_review import JudgeReviewPhase

        config = _make_config(str(tmp_path))
        msg = MagicMock()
        msg.payload = {}
        mock_judge = MagicMock()
        mock_judge.run = AsyncMock(return_value=msg)
        mock_judge.verify_customizations = MagicMock(return_value=[])
        mock_judge.build_repair_instructions = MagicMock(return_value=[])
        ctx = self._make_ctx(
            config, agents={"judge": mock_judge, "executor": MagicMock()}
        )

        state = _make_state(config)
        state.status = SystemStatus.JUDGE_REVIEWING
        phase = JudgeReviewPhase()
        await phase.execute(state, ctx)

        assert state.status == SystemStatus.GENERATING_REPORT

    async def test_report_writes_and_transitions_completed(self, tmp_path):
        from src.core.phases.report_generation import ReportGenerationPhase

        config = _make_config(str(tmp_path))
        ctx = self._make_ctx(config)

        with (
            patch("src.core.phases.report_generation.write_json_report") as mock_json,
            patch("src.core.phases.report_generation.write_markdown_report") as mock_md,
        ):
            state = _make_state(config)
            state.status = SystemStatus.GENERATING_REPORT
            phase = ReportGenerationPhase()
            await phase.execute(state, ctx)

            mock_json.assert_called_once()
            mock_md.assert_called_once()

        assert state.status == SystemStatus.COMPLETED

    async def test_report_failure_still_completes(self, tmp_path):
        from src.core.phases.report_generation import ReportGenerationPhase

        config = _make_config(str(tmp_path))
        ctx = self._make_ctx(config)

        with (
            patch(
                "src.core.phases.report_generation.write_json_report",
                side_effect=IOError("disk"),
            ),
            patch("src.core.phases.report_generation.write_markdown_report"),
        ):
            state = _make_state(config)
            state.status = SystemStatus.GENERATING_REPORT
            phase = ReportGenerationPhase()
            await phase.execute(state, ctx)

        assert state.status == SystemStatus.COMPLETED
        assert any("Report generation failed" in e["message"] for e in state.errors)

    async def test_run_exception_transitions_to_failed(self, tmp_path):
        from src.core.orchestrator import Orchestrator

        config = _make_config(str(tmp_path))
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

        with patch("src.core.orchestrator.GitTool") as MockGit:
            mock_git = MockGit.return_value
            mock_git.get_merge_base.side_effect = RuntimeError("git error")

            orch = Orchestrator(config, agents=mock_agents)
            state = _make_state(config)
            result = await orch.run(state)

        assert result.status == SystemStatus.FAILED
        assert len(result.errors) > 0

    async def test_run_awaiting_human_returns_early(self, tmp_path):
        from src.core.orchestrator import Orchestrator

        config = _make_config(str(tmp_path))
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

        with patch("src.core.orchestrator.GitTool"):
            orch = Orchestrator(config, agents=mock_agents)
            state = _make_state(config)
            state.status = SystemStatus.AWAITING_HUMAN
            result = await orch.run(state)

        assert result.status == SystemStatus.AWAITING_HUMAN

    async def test_auto_merge_with_risky_files_transitions_to_analyzing_conflicts(
        self, tmp_path
    ):
        from src.core.phases.auto_merge import AutoMergePhase

        config = _make_config(str(tmp_path))
        mock_executor = MagicMock()
        mock_executor.execute_auto_merge = AsyncMock(
            return_value=MagicMock(file_path="src/safe.py")
        )
        ctx = self._make_ctx(config, agents={"executor": mock_executor})

        state = _make_state(config)
        state.status = SystemStatus.AUTO_MERGING
        state.merge_plan = _make_merge_plan(
            auto_safe_files=["src/safe.py"],
            risky_files=["src/risky.py"],
        )
        object.__setattr__(state, "_file_diffs", [_make_file_diff("src/safe.py")])
        phase = AutoMergePhase()
        await phase.execute(state, ctx)

        assert state.status == SystemStatus.ANALYZING_CONFLICTS

    async def test_plan_review_critical_replan_calls_planning(self, tmp_path):
        from src.core.phases.plan_review import PlanReviewPhase

        config = _make_config(str(tmp_path))
        mock_planner = MagicMock()
        mock_planner.run = AsyncMock()
        mock_pj = MagicMock()
        mock_pj.review_plan = AsyncMock(
            return_value=_make_plan_judge_verdict(PlanJudgeResult.CRITICAL_REPLAN)
        )
        ctx = self._make_ctx(
            config, agents={"planner": mock_planner, "planner_judge": mock_pj}
        )

        state = _make_state(config)
        state.status = SystemStatus.PLAN_REVIEWING
        state.merge_plan = _make_merge_plan()
        phase = PlanReviewPhase()
        await phase.execute(state, ctx)

        mock_planner.run.assert_awaited_once()
