from datetime import datetime
from pathlib import Path
from src.models.decision import FileDecisionRecord, MergeDecision, DecisionSource
from src.models.diff import FileStatus
from src.models.state import MergeState
from src.tools.git_tool import GitTool


async def apply_with_snapshot(
    file_path: str,
    new_content: str,
    git_tool: GitTool,
    state: MergeState,
    phase: str = "auto_merge",
    agent: str = "executor",
    decision: MergeDecision = MergeDecision.SEMANTIC_MERGE,
    rationale: str = "",
    confidence: float | None = None,
) -> FileDecisionRecord:
    abs_path = git_tool.repo_path / file_path

    original: str | None = None
    if abs_path.exists():
        original = abs_path.read_text(encoding="utf-8")

    try:
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(new_content, encoding="utf-8")

        preview_lines = new_content.splitlines()[:50]
        preview = "\n".join(preview_lines)

        record = FileDecisionRecord(
            file_path=file_path,
            file_status=FileStatus.MODIFIED,
            decision=decision,
            decision_source=DecisionSource.AUTO_EXECUTOR,
            confidence=confidence,
            rationale=rationale or f"Applied {decision.value} merge",
            original_snapshot=original,
            merged_content_preview=preview,
            phase=phase,
            agent=agent,
            timestamp=datetime.now(),
        )
        return record

    except Exception as e:
        if original is not None:
            abs_path.write_text(original, encoding="utf-8")

        record = FileDecisionRecord(
            file_path=file_path,
            file_status=FileStatus.MODIFIED,
            decision=MergeDecision.ESCALATE_HUMAN,
            decision_source=DecisionSource.AUTO_EXECUTOR,
            confidence=0.0,
            rationale=f"Apply failed, rolled back: {e}",
            original_snapshot=original,
            phase=phase,
            agent=agent,
            timestamp=datetime.now(),
            is_rolled_back=original is not None,
            rollback_reason=str(e),
        )
        return record


def create_escalate_record(
    file_path: str,
    reason: str,
    phase: str = "auto_merge",
    agent: str = "executor",
) -> FileDecisionRecord:
    return FileDecisionRecord(
        file_path=file_path,
        file_status=FileStatus.MODIFIED,
        decision=MergeDecision.ESCALATE_HUMAN,
        decision_source=DecisionSource.AUTO_EXECUTOR,
        confidence=0.0,
        rationale=reason,
        phase=phase,
        agent=agent,
        timestamp=datetime.now(),
    )
