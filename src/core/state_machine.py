from datetime import datetime
from src.models.state import MergeState, SystemStatus


VALID_TRANSITIONS: dict[SystemStatus, list[SystemStatus]] = {
    SystemStatus.INITIALIZED: [SystemStatus.PLANNING, SystemStatus.FAILED],
    SystemStatus.PLANNING: [SystemStatus.PLAN_REVIEWING, SystemStatus.FAILED],
    SystemStatus.PLAN_REVIEWING: [
        SystemStatus.AUTO_MERGING,
        SystemStatus.PLAN_REVISING,
        SystemStatus.AWAITING_HUMAN,
        SystemStatus.PLANNING,
        SystemStatus.FAILED,
    ],
    SystemStatus.PLAN_REVISING: [SystemStatus.PLAN_REVIEWING, SystemStatus.FAILED],
    SystemStatus.AUTO_MERGING: [
        SystemStatus.ANALYZING_CONFLICTS,
        SystemStatus.JUDGE_REVIEWING,
        SystemStatus.PLAN_DISPUTE_PENDING,
        SystemStatus.FAILED,
        SystemStatus.PAUSED,
    ],
    SystemStatus.PLAN_DISPUTE_PENDING: [
        SystemStatus.PLAN_REVISING,
        SystemStatus.AWAITING_HUMAN,
    ],
    SystemStatus.ANALYZING_CONFLICTS: [
        SystemStatus.AWAITING_HUMAN,
        SystemStatus.JUDGE_REVIEWING,
        SystemStatus.PLAN_DISPUTE_PENDING,
        SystemStatus.FAILED,
    ],
    SystemStatus.AWAITING_HUMAN: [
        SystemStatus.ANALYZING_CONFLICTS,
        SystemStatus.JUDGE_REVIEWING,
        SystemStatus.FAILED,
    ],
    SystemStatus.JUDGE_REVIEWING: [
        SystemStatus.GENERATING_REPORT,
        SystemStatus.AWAITING_HUMAN,
        SystemStatus.ANALYZING_CONFLICTS,
        SystemStatus.FAILED,
    ],
    SystemStatus.GENERATING_REPORT: [SystemStatus.COMPLETED, SystemStatus.FAILED],
    SystemStatus.PAUSED: [
        SystemStatus.INITIALIZED,
        SystemStatus.PLANNING,
        SystemStatus.PLAN_REVIEWING,
        SystemStatus.PLAN_REVISING,
        SystemStatus.AUTO_MERGING,
        SystemStatus.PLAN_DISPUTE_PENDING,
        SystemStatus.ANALYZING_CONFLICTS,
        SystemStatus.AWAITING_HUMAN,
        SystemStatus.JUDGE_REVIEWING,
        SystemStatus.GENERATING_REPORT,
        SystemStatus.FAILED,
    ],
    SystemStatus.COMPLETED: [],
    SystemStatus.FAILED: [],
}


class StateMachine:
    def transition(self, state: MergeState, target: SystemStatus, reason: str) -> None:
        current = state.status
        if not self.can_transition(current, target):
            raise ValueError(
                f"Invalid state transition: {current.value} -> {target.value}. Reason: {reason}"
            )

        state.status = target
        state.updated_at = datetime.now()

        state.messages.append(
            {
                "timestamp": datetime.now().isoformat(),
                "type": "state_transition",
                "from": current.value if hasattr(current, "value") else str(current),
                "to": target.value if hasattr(target, "value") else str(target),
                "reason": reason,
            }
        )

    def can_transition(self, current: SystemStatus, target: SystemStatus) -> bool:
        allowed = VALID_TRANSITIONS.get(current, [])
        return target in allowed

    def get_valid_transitions(self, current: SystemStatus) -> list[SystemStatus]:
        return VALID_TRANSITIONS.get(current, [])
