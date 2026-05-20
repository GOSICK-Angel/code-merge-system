import json
from datetime import datetime
from typing import Any
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


def _extract_json(raw: str | dict[str, Any]) -> dict[str, Any]:
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
        return json.loads(text)  # type: ignore[no-any-return]
    except json.JSONDecodeError as e:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])  # type: ignore[no-any-return]
            except json.JSONDecodeError:
                pass
        raise ParseError(f"Cannot extract JSON from response: {e}\nRaw: {raw[:500]}")


def _validate_confidence(value: float) -> float:
    if not isinstance(value, (int, float)):
        raise ParseError(f"Confidence must be a number, got {type(value)}")
    if not 0.0 <= value <= 1.0:
        raise ParseError(f"Confidence must be in [0.0, 1.0], got {value}")
    return float(value)


def _validate_enum(value: str, enum_class: Any, field_name: str) -> str:
    valid_values = {e.value for e in enum_class}
    if value in valid_values:
        return value
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    for v in valid_values:
        if normalized == v or normalized.startswith(v):
            return str(v)
    raise ParseError(
        f"Invalid {field_name} value '{value}'. Must be one of: {valid_values}"
    )


def _normalize_plan_judge_json(data: dict[str, Any]) -> dict[str, Any]:
    """Map non-standard LLM output keys to the expected schema."""
    if "result" not in data:
        quality = str(data.get("quality", data.get("verdict", ""))).lower()
        has_issues = bool(data.get("issues"))
        if quality in ("poor", "questionable", "bad", "critical"):
            data["result"] = "revision_needed"
        elif quality in ("good", "excellent", "acceptable"):
            data["result"] = "approved"
        elif has_issues:
            data["result"] = "revision_needed"
        else:
            data["result"] = "approved"

    if "summary" not in data:
        data["summary"] = data.get(
            "overall_assessment",
            data.get("assessment", data.get("correctness", "")),
        )

    for issue in data.get("issues", []):
        if "file_path" not in issue and "file" in issue:
            issue["file_path"] = issue["file"]
        if "issue_type" not in issue and "type" in issue:
            issue["issue_type"] = issue["type"]
        if "current_classification" not in issue:
            issue["current_classification"] = "auto_safe"
        if "suggested_classification" not in issue:
            issue["suggested_classification"] = "human_required"

    return data


def parse_plan_judge_verdict(
    raw: str | dict[str, Any], judge_model: str = "unknown", revision_round: int = 0
) -> PlanJudgeVerdict:
    data = _extract_json(raw)
    data = _normalize_plan_judge_json(data)

    result_raw = data.get("result", "")
    _validate_enum(result_raw, PlanJudgeResult, "result")
    result = PlanJudgeResult(result_raw)

    issues: list[PlanIssue] = []
    for issue_data in data.get("issues", []):
        current_raw = issue_data.get("current_classification", "auto_safe")
        suggested_raw = issue_data.get("suggested_classification", "human_required")
        try:
            current_val = _validate_enum(
                current_raw, RiskLevel, "current_classification"
            )
            suggested_val = _validate_enum(
                suggested_raw, RiskLevel, "suggested_classification"
            )
        except ParseError:
            continue
        issues.append(
            PlanIssue(
                file_path=issue_data.get("file_path", ""),
                current_classification=RiskLevel(current_val),
                suggested_classification=RiskLevel(suggested_val),
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
    raw: str | dict[str, Any], file_path: str, model: str = "unknown"
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
    raw: str | dict[str, Any],
    reviewed_files: list[str],
    judge_model: str = "unknown",
    all_issues: list[JudgeIssue] | None = None,
) -> JudgeVerdict:
    data = _extract_json(raw)
    all_issues = all_issues or []

    critical_count = sum(
        1 for i in all_issues if i.issue_level == IssueSeverity.CRITICAL
    )
    high_count = sum(1 for i in all_issues if i.issue_level == IssueSeverity.HIGH)

    # P0-3: verdict is deterministic from issue counts. The LLM's free-form
    # `verdict` field is intentionally ignored — to declare failure, the Judge
    # must produce a structured JudgeIssue at CRITICAL/HIGH severity.
    if critical_count > 0 or high_count > 0:
        verdict = VerdictType.FAIL
    elif all_issues:
        verdict = VerdictType.CONDITIONAL
    else:
        verdict = VerdictType.PASS

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


def parse_merge_result(
    raw: "str | dict[str, Any] | LLMResponseLike",
    *,
    current_size: int | None = None,
    target_size: int | None = None,
) -> str:
    """Strip code fences and run the merge-output quality gate.

    Quality gate order (any single failure raises ``ParseError`` —
    callers route to ``create_escalate_record``):

    1. ``stop_reason in {"max_tokens", "length"}`` — provider signalled
       truncation. The text is incomplete by definition; don't even
       look at it.
    2. ``has_prose_preamble`` — the first non-empty line is narrative
       ("Looking at the current content..."). The LLM ignored the
       "return ONLY the merged content" instruction.
    3. ``has_elision`` — explicit ``# ... (N sections omitted)`` style
       markers echoed back in the output.
    4. ``looks_truncated`` (when sizes are provided) — last line ends
       mid-token AND output is < 60% of the smaller input.

    Accepts three input shapes for backward compatibility:

    - ``LLMResponse`` (new) — carries ``stop_reason`` for gate #1
    - ``dict`` (legacy) — older callers passing an envelope
    - ``str`` (legacy) — gate #1 is skipped, the rest still run

    ``current_size`` / ``target_size`` enable gate #4. Pass them as
    ``len(current_content)`` / ``len(target_content)`` from
    ``execute_semantic_merge`` so the heuristic has the reference
    point it needs to flag suspiciously short output.
    """
    stop_reason: str | None = None
    if hasattr(raw, "text") and hasattr(raw, "stop_reason"):
        # LLMResponse — duck-type so we don't pull a circular import.
        stop_reason = getattr(raw, "stop_reason", None)
        raw_text = getattr(raw, "text")
        text = str(raw_text).strip()
    elif isinstance(raw, dict):
        text = str(raw.get("content", "")).strip()
    else:
        text = str(raw).strip()

    # Gate 1: provider-side truncation — refuse before anything else,
    # the bytes after the cut are missing by definition.
    if stop_reason in {"max_tokens", "length"}:
        raise ParseError(
            f"Refusing LLM merge result truncated at provider boundary "
            f"(stop_reason={stop_reason!r}). The output ran past the "
            f"``max_tokens`` ceiling; the trailing bytes are not in the "
            f"response. Escalate to human review and bump max_tokens or "
            f"split the file before retrying."
        )

    # Gate 1b: cooperative truncation — the prompt asks the LLM to emit
    # the sentinel ``OUTPUT_TOO_LARGE`` instead of producing a truncated
    # file when it can't fit the full content. Treat as a refuse the
    # same way provider-side truncation is treated; the caller will
    # route to chunked merging.
    if text.strip() == "OUTPUT_TOO_LARGE":
        raise ParseError(
            "LLM signalled OUTPUT_TOO_LARGE — the merged file would exceed "
            "the model's output buffer. Caller should fall back to chunked "
            "semantic merge."
        )

    # Gate 2: prose preamble before any fence stripping — the
    # ``has_prose_preamble`` detector itself skips fenced output.
    from src.tools.elision_detector import (
        has_elision,
        has_prose_preamble,
        looks_truncated,
    )

    prose_hit, prose_line = has_prose_preamble(text)
    if prose_hit:
        raise ParseError(
            f"Refusing merge result that opens with conversational preamble "
            f"(LLM ignored 'return ONLY the merged content' instruction): "
            f"{prose_line!r}. Escalate to human review instead of writing "
            f"chain-of-thought into the file."
        )

    # Strip the code fence wrapper if present — same logic as before,
    # but only after the preamble gate so a fenced preamble (extremely
    # rare) is still caught by gate 3 against the unwrapped text.
    if text.startswith("```"):
        lines = text.splitlines()
        start = 1
        end = len(lines)
        for i in range(len(lines) - 1, 0, -1):
            if lines[i].strip() == "```":
                end = i
                break
        result = "\n".join(lines[start:end])
    else:
        result = text

    # Gate 3: explicit elision markers in the body.
    hit, sample = has_elision(result)
    if hit:
        raise ParseError(
            f"Refusing merge result containing elision marker (likely "
            f"truncated LLM output): {sample!r}. Escalate to human review "
            f"instead of writing a partial file."
        )

    # Gate 4: heuristic truncation check — only when caller passed
    # input sizes so the length sanity guard can run.
    trunc_hit, trunc_sample = looks_truncated(
        result, current_size=current_size, target_size=target_size
    )
    if trunc_hit:
        raise ParseError(
            f"Refusing merge result that appears truncated mid-line "
            f"(tail looks unfinished, output dramatically shorter than "
            f"inputs): {trunc_sample!r}. Escalate to human review."
        )

    return result


# Forward-ref for the duck-typed ``LLMResponse`` argument above —
# avoids a hard import cycle (llm.client → models.diff → ...).
LLMResponseLike = Any


_GROUNDING_REQUIRED_LEVELS: frozenset[IssueSeverity] = frozenset(
    {IssueSeverity.CRITICAL, IssueSeverity.HIGH}
)
_DOWNGRADE_SUFFIX = " [downgraded: ungrounded]"
_HALLUCINATED_SUFFIX = " [downgraded: hallucinated evidence]"


def _apply_grounding_rule(
    level: IssueSeverity,
    affected_lines: list[int],
    evidence_excerpt: str | None,
    description: str,
) -> tuple[IssueSeverity, str]:
    """P1-3: CRITICAL/HIGH issues without ``affected_lines`` and without a
    non-empty ``evidence_excerpt`` are auto-downgraded to MEDIUM. The LLM
    sometimes fires off severity-without-evidence claims that, after P0-3,
    deterministically force FAIL — drop those to MEDIUM so they end up as
    CONDITIONAL noise instead of blocking the merge.
    """
    if level not in _GROUNDING_REQUIRED_LEVELS:
        return level, description
    has_lines = bool(affected_lines)
    has_excerpt = bool(evidence_excerpt and evidence_excerpt.strip())
    if has_lines or has_excerpt:
        return level, description
    return IssueSeverity.MEDIUM, description + _DOWNGRADE_SUFFIX


def _validate_evidence_grounded(
    level: IssueSeverity,
    evidence_excerpt: str | None,
    merged_content: str | None,
    description: str,
) -> tuple[IssueSeverity, str]:
    """P-γ-4 F-judge-source-of-truth: when merged_content is supplied and the
    LLM's evidence_excerpt does not appear in it, the issue is hallucinated.
    Downgrade CRITICAL/HIGH to MEDIUM and annotate the description so the
    trail is visible in reports. Skipped when merged_content is None or the
    stripped excerpt is empty (legacy grounding rule already handles those).
    """
    if merged_content is None:
        return level, description
    if level not in _GROUNDING_REQUIRED_LEVELS:
        return level, description
    if evidence_excerpt is None:
        return level, description
    stripped = evidence_excerpt.strip()
    if not stripped:
        return level, description
    if stripped in merged_content:
        return level, description
    return IssueSeverity.MEDIUM, description + _HALLUCINATED_SUFFIX


def parse_file_review_issues(
    raw: str | dict[str, Any],
    default_file_path: str,
    merged_content: str | None = None,
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

        affected_lines = item.get("affected_lines", []) or []
        evidence_excerpt = item.get("evidence_excerpt")
        description = item.get("description", "")
        level, description = _apply_grounding_rule(
            level, affected_lines, evidence_excerpt, description
        )
        level, description = _validate_evidence_grounded(
            level, evidence_excerpt, merged_content, description
        )

        issues.append(
            JudgeIssue(
                file_path=item.get("file_path", default_file_path),
                issue_level=level,
                issue_type=item.get("issue_type", "other"),
                description=description,
                affected_lines=affected_lines,
                evidence_excerpt=evidence_excerpt,
                suggested_fix=item.get("suggested_fix"),
                must_fix_before_merge=bool(item.get("must_fix_before_merge", False)),
            )
        )

    return issues


def parse_commit_round_analyses(
    raw: str | dict[str, Any], file_paths: list[str]
) -> dict[str, "ConflictAnalysis"]:
    from uuid import uuid4 as _uuid4

    result: dict[str, ConflictAnalysis] = {}
    try:
        data = _extract_json(raw)
    except ParseError:
        return result

    for entry in data.get("files", []):
        fp = entry.get("file_path", "")
        if fp not in file_paths:
            continue

        conflict_type_raw = entry.get("conflict_type", "unknown")
        try:
            _validate_enum(conflict_type_raw, ConflictType, "conflict_type")
            conflict_type = ConflictType(conflict_type_raw)
        except ParseError:
            conflict_type = ConflictType.UNKNOWN

        recommended_raw = entry.get("recommended_strategy", "escalate_human")
        try:
            _validate_enum(recommended_raw, MergeDecision, "recommended_strategy")
            recommended = MergeDecision(recommended_raw)
        except ParseError:
            recommended = MergeDecision.ESCALATE_HUMAN

        try:
            confidence = _validate_confidence(float(entry.get("confidence", 0.5)))
        except (ParseError, ValueError):
            confidence = 0.5

        up_data = entry.get("upstream_intent", {})
        fk_data = entry.get("fork_intent", {})
        upstream_intent = ChangeIntent(
            description=up_data.get("description", ""),
            intent_type=up_data.get("intent_type", "unknown"),
            confidence=float(up_data.get("confidence", 0.5)),
        )
        fork_intent = ChangeIntent(
            description=fk_data.get("description", ""),
            intent_type=fk_data.get("intent_type", "unknown"),
            confidence=float(fk_data.get("confidence", 0.5)),
        )
        conflict_point = ConflictPoint(
            file_path=fp,
            hunk_id=str(_uuid4()),
            conflict_type=conflict_type,
            upstream_intent=upstream_intent,
            fork_intent=fork_intent,
            can_coexist=bool(entry.get("can_coexist", False)),
            suggested_decision=recommended,
            confidence=confidence,
            rationale=entry.get("rationale", ""),
        )
        result[fp] = ConflictAnalysis(
            file_path=fp,
            conflict_points=[conflict_point],
            overall_confidence=confidence,
            recommended_strategy=recommended,
            conflict_type=conflict_type,
            can_coexist=bool(entry.get("can_coexist", False)),
            is_security_sensitive=bool(entry.get("is_security_sensitive", False)),
            rationale=entry.get("rationale", ""),
            confidence=confidence,
        )

    return result


def parse_batch_file_review_issues(
    raw: str | dict[str, Any],
    file_paths: list[str],
    merged_contents: dict[str, str] | None = None,
) -> dict[str, list[JudgeIssue]]:
    if merged_contents is not None and not isinstance(merged_contents, dict):
        raise TypeError(
            "merged_contents must be a dict[str, str] or None; "
            f"got {type(merged_contents).__name__}"
        )

    result: dict[str, list[JudgeIssue]] = {fp: [] for fp in file_paths}
    try:
        data = _extract_json(raw)
    except ParseError:
        return result

    for file_entry in data.get("files", []):
        fp = file_entry.get("file_path", "")
        if fp not in result:
            continue
        per_file_content = (
            merged_contents.get(fp) if merged_contents is not None else None
        )
        for item in file_entry.get("issues", []):
            level_raw = item.get("issue_level", "medium")
            try:
                _validate_enum(level_raw, IssueSeverity, "issue_level")
                level = IssueSeverity(level_raw)
            except ParseError:
                level = IssueSeverity.MEDIUM
            affected_lines = item.get("affected_lines", []) or []
            evidence_excerpt = item.get("evidence_excerpt")
            description = item.get("description", "")
            level, description = _apply_grounding_rule(
                level, affected_lines, evidence_excerpt, description
            )
            level, description = _validate_evidence_grounded(
                level, evidence_excerpt, per_file_content, description
            )
            result[fp].append(
                JudgeIssue(
                    file_path=fp,
                    issue_level=level,
                    issue_type=item.get("issue_type", "other"),
                    description=description,
                    affected_lines=affected_lines,
                    evidence_excerpt=evidence_excerpt,
                    suggested_fix=item.get("suggested_fix"),
                    must_fix_before_merge=bool(
                        item.get("must_fix_before_merge", False)
                    ),
                )
            )
    return result


def _severity_order(severity: IssueSeverity) -> int:
    order = {
        IssueSeverity.INFO: 0,
        IssueSeverity.LOW: 1,
        IssueSeverity.MEDIUM: 2,
        IssueSeverity.HIGH: 3,
        IssueSeverity.CRITICAL: 4,
    }
    return order.get(severity, 0)
