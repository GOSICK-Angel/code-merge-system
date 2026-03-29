import json
from datetime import datetime
from uuid import uuid4
from src.llm.client import ParseError
from src.models.plan_judge import PlanJudgeVerdict, PlanJudgeResult, PlanIssue
from src.models.conflict import (
    ConflictAnalysis,
    ConflictType,
    ChangeIntent,
    ConflictPoint,
)
from src.models.decision import MergeDecision
from src.models.judge import JudgeVerdict, JudgeIssue, VerdictType, IssueSeverity
from src.models.diff import RiskLevel


def _extract_json(raw: str | dict) -> dict:
    if isinstance(raw, dict):
        return raw
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        start = 1
        end = len(lines)
        for i in range(len(lines) - 1, 0, -1):
            if lines[i].strip() == "```":
                end = i
                break
        text = "\n".join(lines[start:end])
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass
        raise ParseError(f"Cannot extract JSON from response: {e}\nRaw: {raw[:500]}")


def _validate_confidence(value: float) -> float:
    if not isinstance(value, (int, float)):
        raise ParseError(f"Confidence must be a number, got {type(value)}")
    if not 0.0 <= value <= 1.0:
        raise ParseError(f"Confidence must be in [0.0, 1.0], got {value}")
    return float(value)


def _validate_enum(value: str, enum_class: type, field_name: str) -> str:
    valid_values = {e.value for e in enum_class}
    if value not in valid_values:
        raise ParseError(
            f"Invalid {field_name} value '{value}'. Must be one of: {valid_values}"
        )
    return value


def parse_plan_judge_verdict(
    raw: str | dict, judge_model: str = "unknown", revision_round: int = 0
) -> PlanJudgeVerdict:
    data = _extract_json(raw)

    result_raw = data.get("result", "")
    _validate_enum(result_raw, PlanJudgeResult, "result")
    result = PlanJudgeResult(result_raw)

    issues: list[PlanIssue] = []
    for issue_data in data.get("issues", []):
        current_raw = issue_data.get("current_classification", "auto_safe")
        suggested_raw = issue_data.get("suggested_classification", "human_required")
        _validate_enum(current_raw, RiskLevel, "current_classification")
        _validate_enum(suggested_raw, RiskLevel, "suggested_classification")
        issues.append(
            PlanIssue(
                file_path=issue_data.get("file_path", ""),
                current_classification=RiskLevel(current_raw),
                suggested_classification=RiskLevel(suggested_raw),
                reason=issue_data.get("reason", ""),
                issue_type=issue_data.get("issue_type", "risk_underestimated"),
            )
        )

    return PlanJudgeVerdict(
        result=result,
        revision_round=revision_round,
        issues=issues,
        approved_files_count=int(data.get("approved_files_count", 0)),
        flagged_files_count=int(data.get("flagged_files_count", len(issues))),
        summary=data.get("summary", ""),
        judge_model=judge_model,
        timestamp=datetime.now(),
    )


def parse_conflict_analysis(
    raw: str | dict, file_path: str, model: str = "unknown"
) -> ConflictAnalysis:
    data = _extract_json(raw)

    conflict_type_raw = data.get("conflict_type", "unknown")
    try:
        _validate_enum(conflict_type_raw, ConflictType, "conflict_type")
        conflict_type = ConflictType(conflict_type_raw)
    except ParseError:
        conflict_type = ConflictType.UNKNOWN

    recommended_raw = data.get("recommended_strategy", "escalate_human")
    try:
        _validate_enum(recommended_raw, MergeDecision, "recommended_strategy")
        recommended = MergeDecision(recommended_raw)
    except ParseError:
        recommended = MergeDecision.ESCALATE_HUMAN

    confidence = _validate_confidence(float(data.get("confidence", 0.5)))

    upstream_data = data.get("upstream_intent", {})
    fork_data = data.get("fork_intent", {})

    upstream_intent = ChangeIntent(
        description=upstream_data.get("description", ""),
        intent_type=upstream_data.get("intent_type", "unknown"),
        confidence=float(upstream_data.get("confidence", 0.5)),
    )
    fork_intent = ChangeIntent(
        description=fork_data.get("description", ""),
        intent_type=fork_data.get("intent_type", "unknown"),
        confidence=float(fork_data.get("confidence", 0.5)),
    )

    conflict_point = ConflictPoint(
        file_path=file_path,
        hunk_id=str(uuid4()),
        conflict_type=conflict_type,
        upstream_intent=upstream_intent,
        fork_intent=fork_intent,
        can_coexist=bool(data.get("can_coexist", False)),
        suggested_decision=recommended,
        confidence=confidence,
        rationale=data.get("rationale", ""),
    )

    return ConflictAnalysis(
        file_path=file_path,
        conflict_points=[conflict_point],
        overall_confidence=confidence,
        recommended_strategy=recommended,
        conflict_type=conflict_type,
        can_coexist=bool(data.get("can_coexist", False)),
        is_security_sensitive=bool(data.get("is_security_sensitive", False)),
        rationale=data.get("rationale", ""),
        confidence=confidence,
    )


def parse_judge_verdict(
    raw: str | dict,
    reviewed_files: list[str],
    judge_model: str = "unknown",
    all_issues: list[JudgeIssue] | None = None,
) -> JudgeVerdict:
    data = _extract_json(raw)
    all_issues = all_issues or []

    verdict_raw = data.get("verdict", "conditional")
    try:
        _validate_enum(verdict_raw, VerdictType, "verdict")
        verdict = VerdictType(verdict_raw)
    except ParseError:
        verdict = VerdictType.CONDITIONAL

    critical_count = sum(
        1 for i in all_issues if i.issue_level == IssueSeverity.CRITICAL
    )
    high_count = sum(1 for i in all_issues if i.issue_level == IssueSeverity.HIGH)

    passed_files: list[str] = []
    failed_files: list[str] = []
    conditional_files: list[str] = []

    issue_file_map: dict[str, IssueSeverity] = {}
    for issue in all_issues:
        existing = issue_file_map.get(issue.file_path)
        if existing is None or _severity_order(issue.issue_level) > _severity_order(
            existing
        ):
            issue_file_map[issue.file_path] = issue.issue_level

    for fp in reviewed_files:
        worst = issue_file_map.get(fp)
        if worst is None:
            passed_files.append(fp)
        elif worst in (IssueSeverity.CRITICAL, IssueSeverity.HIGH):
            failed_files.append(fp)
        else:
            conditional_files.append(fp)

    return JudgeVerdict(
        verdict=verdict,
        reviewed_files_count=len(reviewed_files),
        passed_files=passed_files,
        failed_files=failed_files,
        conditional_files=conditional_files,
        issues=all_issues,
        critical_issues_count=critical_count,
        high_issues_count=high_count,
        overall_confidence=float(data.get("confidence", 0.7)),
        summary=data.get("summary", data.get("overall_assessment", "")),
        blocking_issues=data.get("blocking_issues", []),
        timestamp=datetime.now(),
        judge_model=judge_model,
    )


def parse_merge_result(raw: str | dict) -> str:
    if isinstance(raw, dict):
        return str(raw.get("content", ""))
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        start = 1
        end = len(lines)
        for i in range(len(lines) - 1, 0, -1):
            if lines[i].strip() == "```":
                end = i
                break
        return "\n".join(lines[start:end])
    return text


def parse_file_review_issues(
    raw: str | dict, default_file_path: str
) -> list[JudgeIssue]:
    data = _extract_json(raw)
    issues: list[JudgeIssue] = []

    for item in data.get("issues", []):
        level_raw = item.get("issue_level", "medium")
        try:
            _validate_enum(level_raw, IssueSeverity, "issue_level")
            level = IssueSeverity(level_raw)
        except ParseError:
            level = IssueSeverity.MEDIUM

        issues.append(
            JudgeIssue(
                file_path=item.get("file_path", default_file_path),
                issue_level=level,
                issue_type=item.get("issue_type", "other"),
                description=item.get("description", ""),
                affected_lines=item.get("affected_lines", []),
                suggested_fix=item.get("suggested_fix"),
                must_fix_before_merge=bool(item.get("must_fix_before_merge", False)),
            )
        )

    return issues


def _severity_order(severity: IssueSeverity) -> int:
    order = {
        IssueSeverity.INFO: 0,
        IssueSeverity.LOW: 1,
        IssueSeverity.MEDIUM: 2,
        IssueSeverity.HIGH: 3,
        IssueSeverity.CRITICAL: 4,
    }
    return order.get(severity, 0)
