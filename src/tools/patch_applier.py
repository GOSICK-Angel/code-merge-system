from datetime import datetime
from src.models.decision import FileDecisionRecord, MergeDecision, DecisionSource
from src.models.diff import FileStatus
from src.models.state import MergeState
from src.tools.conflict_markers import has_conflict_markers
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

    if has_conflict_markers(new_content):
        return FileDecisionRecord(
            file_path=file_path,
            file_status=FileStatus.MODIFIED,
            decision=MergeDecision.ESCALATE_HUMAN,
            decision_source=DecisionSource.AUTO_EXECUTOR,
            confidence=0.0,
            rationale=(
                "Unresolved conflict markers (<<<<<<< / ======= / >>>>>>>) "
                "detected in proposed content — escalating to human review "
                "without writing the file (O-M1)."
            ),
            original_snapshot=original,
            phase=phase,
            agent=agent,
            timestamp=datetime.now(),
            is_rolled_back=False,
            rollback_reason="conflict_markers_in_proposed_content",
        )

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


async def apply_bytes_with_snapshot(
    file_path: str,
    new_bytes: bytes,
    git_tool: GitTool,
    state: MergeState,
    phase: str = "auto_merge",
    agent: str = "executor",
    decision: MergeDecision = MergeDecision.TAKE_TARGET,
    rationale: str = "",
    confidence: float | None = None,
) -> FileDecisionRecord:
    """O-B4: binary-safe writer. Snapshots original bytes (base64-encoded
    into the str `original_snapshot` field so the model stays unchanged),
    writes raw bytes, and rolls back on failure. Skips conflict-marker
    check since binary content cannot carry git conflict markers."""
    import base64

    abs_path = git_tool.repo_path / file_path

    original_bytes: bytes | None = None
    if abs_path.exists():
        original_bytes = abs_path.read_bytes()
    original_snapshot = (
        base64.b64encode(original_bytes).decode("ascii")
        if original_bytes is not None
        else None
    )

    try:
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(new_bytes)

        record = FileDecisionRecord(
            file_path=file_path,
            file_status=FileStatus.MODIFIED,
            decision=decision,
            decision_source=DecisionSource.AUTO_EXECUTOR,
            confidence=confidence,
            rationale=rationale or f"Applied {decision.value} (binary)",
            original_snapshot=original_snapshot,
            merged_content_preview=f"<binary:{len(new_bytes)}bytes>",
            phase=phase,
            agent=agent,
            timestamp=datetime.now(),
        )
        return record

    except Exception as e:
        if original_bytes is not None:
            abs_path.write_bytes(original_bytes)

        return FileDecisionRecord(
            file_path=file_path,
            file_status=FileStatus.MODIFIED,
            decision=MergeDecision.ESCALATE_HUMAN,
            decision_source=DecisionSource.AUTO_EXECUTOR,
            confidence=0.0,
            rationale=f"Binary apply failed, rolled back: {e}",
            original_snapshot=original_snapshot,
            phase=phase,
            agent=agent,
            timestamp=datetime.now(),
            is_rolled_back=original_bytes is not None,
            rollback_reason=str(e),
        )


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
