from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from src.agents.base_agent import CIRCUIT_BREAKER_THRESHOLD
from src.core.phases.base import Phase, PhaseContext, PhaseOutcome
from src.llm.relevance import weights_from_fanin
from src.models.conflict import ConflictAnalysis, ConflictType
from src.models.config import ThresholdConfig
from src.models.decision import MergeDecision
from src.models.dependency import DependencyImpactHint
from src.models.diff import FileChangeCategory, FileDiff, FileStatus, RiskLevel
from src.models.human import HumanDecisionRequest, DecisionOption
from src.models.plan import MergePhase
from src.models.state import MergeState, PhaseResult, SystemStatus
from src.tools.binary_assets import is_binary_asset
from src.tools.commit_replayer import CommitReplayer
from src.tools.git_committer import GitCommitter
from src.tools.git_tool import GitTool
from src.tools.patch_applier import apply_bytes_with_snapshot, create_escalate_record
from src.tools.rule_resolver import RuleBasedResolver

logger = logging.getLogger(__name__)


_FILE_TOKEN_ESTIMATE = 1000
_COMMIT_TOKEN_ESTIMATE = 200


def _estimate_round_tokens(file_count: int, commit_count: int) -> int:
    return file_count * _FILE_TOKEN_ESTIMATE + commit_count * _COMMIT_TOKEN_ESTIMATE


def build_commit_rounds(
    commits: list[dict[str, Any]],
    round_size: int,
    max_files_per_round: int | None = None,
    max_est_tokens_per_round: int | None = None,
) -> list[list[dict[str, Any]]]:
    rounds: list[list[dict[str, Any]]] = []
    current_round: list[dict[str, Any]] = []
    current_files: set[str] = set()

    for commit in commits:
        commit_files = set(commit.get("files", []))
        projected_files = current_files | commit_files
        projected_count = len(current_round) + 1

        overflow_by_count = len(current_round) >= round_size
        overflow_by_files = (
            max_files_per_round is not None
            and current_round
            and len(projected_files) > max_files_per_round
        )
        overflow_by_tokens = (
            max_est_tokens_per_round is not None
            and current_round
            and _estimate_round_tokens(len(projected_files), projected_count)
            > max_est_tokens_per_round
        )
        overlaps_current = bool(commit_files & current_files) and bool(current_round)

        if (
            overlaps_current
            or overflow_by_count
            or overflow_by_files
            or overflow_by_tokens
        ):
            rounds.append(current_round)
            current_round = []
            current_files = set()
        current_round.append(commit)
        current_files |= commit_files

    if current_round:
        rounds.append(current_round)
    return rounds


async def _analyze_round_with_bisect(
    conflict_analyst: Any,
    round_commits: list[dict[str, Any]],
    round_llm_files: dict[str, tuple[str | None, str | None, str | None]],
    file_languages: dict[str, str],
    project_context: str,
    *,
    per_file_instructions: dict[str, str] | None = None,
    max_depth: int = 2,
    _depth: int = 0,
    fork_ref: str | None = None,
) -> dict[str, ConflictAnalysis]:
    """Run analyze_commit_round; if it returns 0 analyses for a multi-commit
    round, recursively bisect the commit list and merge results. Bounded by
    max_depth so worst-case extra LLM calls per failed round = 2**max_depth - 1.
    """
    analyses: dict[str, ConflictAnalysis] = await conflict_analyst.analyze_commit_round(
        round_commits,
        round_llm_files,
        file_languages,
        project_context=project_context,
        per_file_instructions=per_file_instructions,
        fork_ref=fork_ref,
    )

    can_bisect = _depth < max_depth and len(round_commits) >= 2
    if analyses or not can_bisect:
        return analyses

    mid = len(round_commits) // 2
    halves = [round_commits[:mid], round_commits[mid:]]
    logger.info(
        "Bisect retry (depth=%d): splitting %d-commit round into %d+%d",
        _depth + 1,
        len(round_commits),
        len(halves[0]),
        len(halves[1]),
    )

    merged: dict[str, ConflictAnalysis] = {}
    for half in halves:
        half_files = {
            fp
            for fp in round_llm_files
            if any(fp in (c.get("files") or []) for c in half)
        }
        sub_files = {fp: round_llm_files[fp] for fp in half_files}
        sub_languages = {fp: file_languages.get(fp, "") for fp in sub_files}
        if not sub_files:
            continue
        sub_analyses = await _analyze_round_with_bisect(
            conflict_analyst,
            half,
            sub_files,
            sub_languages,
            project_context,
            per_file_instructions=per_file_instructions,
            max_depth=max_depth,
            _depth=_depth + 1,
            fork_ref=fork_ref,
        )
        merged.update(sub_analyses)
    return merged


async def _get_round_three_way(
    round_commits: list[dict[str, Any]],
    already_resolved: set[str],
    state: MergeState,
    ctx: PhaseContext,
) -> dict[str, tuple[str | None, str | None, str | None]]:
    result: dict[str, tuple[str | None, str | None, str | None]] = {}
    for commit in round_commits:
        for fp in commit.get("files", []):
            if fp in result or fp in already_resolved:
                continue
            base_c = current_c = target_c = None
            if ctx.git_tool:
                base_c, current_c, target_c = ctx.git_tool.get_three_way_diff(
                    state.merge_base_commit,
                    state.config.fork_ref,
                    state.config.upstream_ref,
                    fp,
                )
            result[fp] = (base_c, current_c, target_c)
    return result


def _synthesize_minimal_filediff(
    file_path: str,
    git_tool: "GitTool | None",
    upstream_ref: str,
    fork_ref: str,
) -> FileDiff:
    """Build a minimal FileDiff for a file surfaced by auto_merge that lacks
    an entry in ``state.file_diffs`` (e.g. O-B5 B-class drift or fork
    preservation losses). The synthesized record carries only what
    downstream execution needs — file_path + a best-effort file_status —
    so TAKE_TARGET / TAKE_CURRENT / SKIP can complete without skipping.

    Hunks, line counts and risk fields are left at conservative defaults;
    semantic_merge requires hunks and is downgraded by the caller.
    """
    file_status = FileStatus.MODIFIED
    if git_tool is not None:
        try:
            upstream_exists = (
                git_tool.get_file_content(upstream_ref, file_path) is not None
            )
            fork_exists = git_tool.get_file_content(fork_ref, file_path) is not None
        except Exception:
            upstream_exists = fork_exists = True
        if upstream_exists and not fork_exists:
            file_status = FileStatus.ADDED
        elif fork_exists and not upstream_exists:
            file_status = FileStatus.DELETED

    return FileDiff(
        file_path=file_path,
        file_status=file_status,
        risk_level=RiskLevel.AUTO_RISKY,
        risk_score=0.5,
        risk_factors=["synthesized_from_pending_conflict_files"],
    )


def _select_merge_strategy(
    analysis: ConflictAnalysis, thresholds: ThresholdConfig
) -> MergeDecision:
    if analysis.is_security_sensitive:
        return MergeDecision.ESCALATE_HUMAN

    # #12: the analyst rationale referenced a member access fabricated on a real
    # module (present in neither fork nor upstream, not declared via REQUIRES NEW
    # API). This is a strong hallucination signal scoped to the fabricated subset
    # of grounding warnings (verb-mismatch warnings stay advisory) — escalate
    # rather than auto-merge on a rationale we know is partly invented.
    if analysis.fabricated_symbols:
        return MergeDecision.ESCALATE_HUMAN

    if analysis.semantic_compatibility == "incompatible":
        return MergeDecision.ESCALATE_HUMAN

    if analysis.conflict_type == ConflictType.LOGIC_CONTRADICTION:
        if analysis.confidence < 0.90:
            return MergeDecision.ESCALATE_HUMAN

    if analysis.conflict_type == ConflictType.SEMANTIC_EQUIVALENT:
        if analysis.confidence >= thresholds.auto_merge_confidence:
            return MergeDecision.TAKE_TARGET

    if analysis.can_coexist and analysis.confidence >= thresholds.auto_merge_confidence:
        return MergeDecision.SEMANTIC_MERGE

    if analysis.confidence >= thresholds.auto_merge_confidence:
        return analysis.recommended_strategy

    # For unambiguous directional decisions (take_current / take_target),
    # accept a lower confidence floor to reduce unnecessary human escalations.
    # Semantic merge and security-sensitive files always require higher confidence.
    if (
        analysis.recommended_strategy
        in (MergeDecision.TAKE_CURRENT, MergeDecision.TAKE_TARGET)
        and analysis.confidence >= 0.4
        and analysis.conflict_type != ConflictType.LOGIC_CONTRADICTION
    ):
        return analysis.recommended_strategy

    return MergeDecision.ESCALATE_HUMAN


def _build_diff_preview(
    file_path: str,
    upstream_ref: str,
    fork_ref: str,
    git_tool: "GitTool | None",
    max_lines: int = 120,
) -> tuple[str, str]:
    """O-G1: produce a unified diff preview of upstream vs fork content.

    Returns ``(take_target_preview, take_current_preview)`` — short bodies
    suitable for ``DecisionOption.preview_content``. Empty strings are
    returned when the git tool is unavailable or both refs miss the file.
    """
    if git_tool is None:
        return "", ""
    try:
        upstream_raw = git_tool.get_file_content(upstream_ref, file_path)
        current_raw = git_tool.get_file_content(fork_ref, file_path)
    except Exception:
        return "", ""
    upstream = upstream_raw if isinstance(upstream_raw, str) else ""
    current = current_raw if isinstance(current_raw, str) else ""
    if not upstream and not current:
        return "", ""

    import difflib

    upstream_lines = upstream.splitlines(keepends=True)
    current_lines = current.splitlines(keepends=True)
    diff = list(
        difflib.unified_diff(
            current_lines,
            upstream_lines,
            fromfile=f"fork:{file_path}",
            tofile=f"upstream:{file_path}",
            n=3,
        )
    )
    if len(diff) > max_lines:
        diff = diff[:max_lines] + [f"... (+{len(diff) - max_lines} more lines)\n"]
    take_target = "".join(diff) or "(no textual diff)"

    reverse = list(
        difflib.unified_diff(
            upstream_lines,
            current_lines,
            fromfile=f"upstream:{file_path}",
            tofile=f"fork:{file_path}",
            n=3,
        )
    )
    if len(reverse) > max_lines:
        reverse = reverse[:max_lines] + [
            f"... (+{len(reverse) - max_lines} more lines)\n"
        ]
    take_current = "".join(reverse) or "(no textual diff)"
    return take_target, take_current


def _build_human_decision_request(
    fd: FileDiff,
    analysis: ConflictAnalysis,
    upstream_ref: str | None = None,
    fork_ref: str | None = None,
    git_tool: "GitTool | None" = None,
    impact_hint: "DependencyImpactHint | None" = None,
) -> HumanDecisionRequest:
    rec_val = analysis.recommended_strategy

    take_target_preview = ""
    take_current_preview = ""
    if upstream_ref and fork_ref:
        take_target_preview, take_current_preview = _build_diff_preview(
            fd.file_path, upstream_ref, fork_ref, git_tool
        )

    options = [
        DecisionOption(
            option_key="A",
            decision=MergeDecision.TAKE_CURRENT,
            description="Keep fork (current) version",
            preview_content=take_current_preview or None,
        ),
        DecisionOption(
            option_key="B",
            decision=MergeDecision.TAKE_TARGET,
            description="Take upstream (target) version",
            preview_content=take_target_preview or None,
        ),
        DecisionOption(
            option_key="C",
            decision=MergeDecision.SEMANTIC_MERGE,
            description="Attempt semantic merge",
        ),
        DecisionOption(
            option_key="D",
            decision=MergeDecision.MANUAL_PATCH,
            description="Provide custom content",
        ),
    ]

    conf_pct = int(round(analysis.confidence * 100))
    rec_label = rec_val.value if hasattr(rec_val, "value") else str(rec_val)

    context_summary = (
        f"Analyst attempted auto-resolution and escalated to human "
        f"(confidence {conf_pct}%, recommended={rec_label}). "
        f"Change shape: +{fd.lines_added}/-{fd.lines_deleted} lines."
    )
    if analysis.rationale:
        context_summary = f"{context_summary} Rationale: {analysis.rationale}"

    # Phase C §4: surface dependency blast radius on the card.
    if impact_hint is not None and impact_hint.has_signal:
        hub = " (dependency hub)" if impact_hint.is_god_node else ""
        context_summary = (
            f"{context_summary} Dependency impact: "
            f"{impact_hint.direct_dependents} direct dependent(s), "
            f"impact radius {impact_hint.impact_radius}{hub}."
        )

    upstream_intents = [
        cp.upstream_intent.description
        for cp in analysis.conflict_points
        if cp.upstream_intent and cp.upstream_intent.description
    ]
    fork_intents = [
        cp.fork_intent.description
        for cp in analysis.conflict_points
        if cp.fork_intent and cp.fork_intent.description
    ]
    upstream_change_summary = (
        " · ".join(upstream_intents[:2])
        if upstream_intents
        else (
            take_target_preview
            or f"Upstream changed (+{fd.lines_added}/-{fd.lines_deleted})"
        )
    )
    fork_change_summary = (
        " · ".join(fork_intents[:2])
        if fork_intents
        else (
            take_current_preview
            or f"Fork changed (+{fd.lines_added}/-{fd.lines_deleted})"
        )
    )

    return HumanDecisionRequest(
        file_path=fd.file_path,
        priority=1 if fd.is_security_sensitive else 5,
        conflict_points=analysis.conflict_points,
        context_summary=context_summary,
        upstream_change_summary=upstream_change_summary,
        fork_change_summary=fork_change_summary,
        analyst_recommendation=rec_val,
        analyst_confidence=analysis.confidence,
        analyst_rationale=analysis.rationale,
        options=options,
        created_at=datetime.now(),
        dependents_count=impact_hint.direct_dependents if impact_hint else 0,
        blast_radius=impact_hint.impact_radius if impact_hint else 0,
        is_god_node=impact_hint.is_god_node if impact_hint else False,
        grounding_warnings=list(analysis.grounding_warnings),
        required_new_apis=list(analysis.required_new_apis),
        semantic_compatibility=analysis.semantic_compatibility,
    )


class ConflictAnalysisPhase(Phase):
    name = "conflict_analysis"

    async def execute(self, state: MergeState, ctx: PhaseContext) -> PhaseOutcome:
        state.current_phase = MergePhase.CONFLICT_ANALYSIS
        phase_result = PhaseResult(
            phase=MergePhase.CONFLICT_ANALYSIS,
            status="running",
            started_at=datetime.now(),
        )
        state.phase_results[MergePhase.CONFLICT_ANALYSIS.value] = phase_result

        conflict_analyst = ctx.agents["conflict_analyst"]
        executor = ctx.agents["executor"]

        file_diffs_map: dict[str, FileDiff] = {}
        for fd in state.file_diffs:
            file_diffs_map[fd.file_path] = fd

        high_risk_files: list[str] = []
        if state.merge_plan:
            from src.models.diff import RiskLevel as _RL

            for batch in state.merge_plan.phases:
                if batch.risk_level in (_RL.HUMAN_REQUIRED, _RL.AUTO_RISKY):
                    high_risk_files.extend(batch.file_paths)

        # Include files auto_merge surfaced as unhandled (skipped layers +
        # non-replayable commits). Dedupe while preserving order.
        _seen_hr = set(high_risk_files)
        for fp in state.pending_conflict_files or []:
            if fp in _seen_hr:
                continue
            if fp in state.file_decision_records:
                continue
            high_risk_files.append(fp)
            _seen_hr.add(fp)

        # Opt-1: D-missing files (new upstream files absent from fork) need no LLM
        # analysis — always take_target directly.
        d_missing_resolved: set[str] = set()
        for file_path in high_risk_files:
            fd_opt = file_diffs_map.get(file_path)
            if fd_opt is None or fd_opt.change_category != FileChangeCategory.D_MISSING:
                continue
            if file_path in state.file_decision_records:
                d_missing_resolved.add(file_path)
                continue
            state.conflict_analyses[file_path] = ConflictAnalysis(
                file_path=file_path,
                conflict_points=[],
                overall_confidence=0.99,
                recommended_strategy=MergeDecision.TAKE_TARGET,
                conflict_type=ConflictType.SEMANTIC_EQUIVALENT,
                rationale="D-missing: new upstream file, taking target directly",
                confidence=0.99,
            )
            d_missing_resolved.add(file_path)
            ctx.notify(
                "conflict_analyst",
                f"D-missing {file_path} → take_target (no LLM needed)",
            )

        rule_resolver = RuleBasedResolver()
        rule_resolved_files: set[str] = set()
        for file_path in high_risk_files:
            if file_path in d_missing_resolved:
                continue
            fd = file_diffs_map.get(file_path)  # type: ignore[assignment]
            if fd is None:
                continue
            base_c = target_c = current_c = None
            if ctx.git_tool:
                base_c, current_c, target_c = ctx.git_tool.get_three_way_diff(
                    state.merge_base_commit,
                    state.config.fork_ref,
                    state.config.upstream_ref,
                    file_path,
                )
            rule_result = rule_resolver.try_resolve(base_c, current_c, target_c)
            if rule_result.resolved and rule_result.pattern is not None:
                pattern_name = rule_result.pattern.value
                rule_resolved_files.add(file_path)
                state.conflict_analyses[file_path] = ConflictAnalysis(
                    file_path=file_path,
                    conflict_points=[],
                    overall_confidence=rule_result.confidence,
                    recommended_strategy=MergeDecision.TAKE_TARGET,
                    conflict_type=ConflictType.SEMANTIC_EQUIVALENT,
                    rationale=(
                        f"Rule-based resolution ({pattern_name}): "
                        f"{rule_result.description}"
                    ),
                    confidence=rule_result.confidence,
                )
                ctx.notify(
                    "conflict_analyst",
                    f"Rule-resolved {file_path} ({pattern_name})",
                )

        # --- O-B3 (conflict_analysis): route binary assets away from the LLM
        # pipeline before building llm_files.  Mirrors auto_merge's O-B3:
        #   * C (both sides modified) → escalate to human
        #   * anything else           → TAKE_TARGET via apply_bytes_with_snapshot
        binary_resolved: set[str] = set()
        for file_path in high_risk_files:
            if file_path in d_missing_resolved or file_path in rule_resolved_files:
                continue
            if file_path in state.file_decision_records:
                continue
            if not is_binary_asset(file_path):
                continue
            binary_resolved.add(file_path)
            fd_bin = file_diffs_map.get(file_path)
            if fd_bin is None or fd_bin.change_category == FileChangeCategory.C:
                state.conflict_analyses[file_path] = ConflictAnalysis(
                    file_path=file_path,
                    conflict_points=[],
                    overall_confidence=0.0,
                    recommended_strategy=MergeDecision.ESCALATE_HUMAN,
                    conflict_type=ConflictType.UNKNOWN,
                    rationale=(
                        "Binary asset (both sides modified) — cannot be "
                        "text-merged by LLM; escalating to human (O-B3)."
                    ),
                    confidence=0.0,
                )
                ctx.notify(
                    "conflict_analyst",
                    f"Binary {file_path} → escalate_human (O-B3, both-sides)",
                )
            else:
                try:
                    if ctx.git_tool is None:
                        raise RuntimeError("no git tool")
                    content_bytes = ctx.git_tool.get_file_bytes(
                        state.config.upstream_ref, file_path
                    )
                    if content_bytes is None:
                        raise RuntimeError("upstream bytes not found")
                    record = await apply_bytes_with_snapshot(
                        file_path,
                        content_bytes,
                        ctx.git_tool,
                        state,
                        phase="conflict_analysis",
                        agent="binary_asset_router",
                        decision=MergeDecision.TAKE_TARGET,
                        rationale=(
                            "O-B3 binary asset in conflict_analysis — taking "
                            "upstream bytes (O-B4 binary-safe writer)."
                        ),
                    )
                except Exception as exc:
                    logger.warning(
                        "O-B3/O-B4 in conflict_analysis: binary copy failed for %s: %s",
                        file_path,
                        exc,
                    )
                    record = create_escalate_record(
                        file_path,
                        f"Binary asset TAKE_TARGET failed ({exc!r}); escalating (O-B3 fallback).",
                        phase="conflict_analysis",
                        agent="binary_asset_router",
                    )
                state.file_decision_records[file_path] = record
                ctx.notify(
                    "conflict_analyst",
                    f"Binary {file_path} → take_target bytes (O-B3)",
                )

        if binary_resolved:
            logger.info(
                "O-B3 in conflict_analysis: routed %d binary asset(s) "
                "(%d take_target, %d escalate) — skipping LLM",
                len(binary_resolved),
                sum(1 for fp in binary_resolved if fp in state.file_decision_records),
                sum(
                    1 for fp in binary_resolved if fp not in state.file_decision_records
                ),
            )

        llm_files = [
            fp
            for fp in high_risk_files
            if fp not in rule_resolved_files
            and fp not in d_missing_resolved
            and fp not in binary_resolved
        ]
        if rule_resolved_files:
            logger.info(
                "Rule-based resolver handled %d/%d files, %d remain for LLM",
                len(rule_resolved_files),
                len(high_risk_files),
                len(llm_files),
            )

        # --- Split llm_files into two streams ---
        # Stream A: files from non_replayable_commits  → commit-round LLM (commit context)
        # Stream B: plan HUMAN_REQUIRED/AUTO_RISKY + other pending → per-file LLM
        non_replay_file_to_commit: dict[str, dict[str, Any]] = {}
        for _commit in state.non_replayable_commits or []:
            for _fp in _commit.get("files", []):
                non_replay_file_to_commit.setdefault(_fp, _commit)

        stream_a_shas_seen: set[str] = set()
        stream_a_commits: list[dict[str, Any]] = []
        stream_a_files: set[str] = set()
        stream_b_files: list[str] = []

        for fp in llm_files:
            if fp in non_replay_file_to_commit:
                stream_a_files.add(fp)
                commit_sha = non_replay_file_to_commit[fp]["sha"]
                if commit_sha not in stream_a_shas_seen:
                    stream_a_shas_seen.add(commit_sha)
                    stream_a_commits.append(non_replay_file_to_commit[fp])
            else:
                stream_b_files.append(fp)

        circuit_breaker_open = False

        # --- Stream A: commit-round analysis ---
        if stream_a_commits:
            round_size = ctx.config.commit_round_size
            rounds = build_commit_rounds(
                stream_a_commits,
                round_size,
                max_files_per_round=ctx.config.commit_round_max_files,
                max_est_tokens_per_round=ctx.config.commit_round_max_est_tokens,
            )
            logger.info(
                "Commit-stream: %d commits → %d rounds (%d files)",
                len(stream_a_commits),
                len(rounds),
                len(stream_a_files),
            )

            for round_idx, round_commits in enumerate(rounds, 1):
                if circuit_breaker_open:
                    for _fp in stream_a_files:
                        if _fp not in state.conflict_analyses:
                            state.conflict_analyses[_fp] = ConflictAnalysis(
                                file_path=_fp,
                                conflict_points=[],
                                overall_confidence=0.0,
                                recommended_strategy=MergeDecision.ESCALATE_HUMAN,
                                conflict_type=ConflictType.UNKNOWN,
                                rationale="Circuit breaker open — skipped",
                                confidence=0.0,
                            )
                    break

                ctx.notify(
                    "conflict_analyst",
                    f"Commit-stream round {round_idx}/{len(rounds)} "
                    f"({len(round_commits)} commits)",
                )

                already_done: set[str] = (
                    set(state.file_decision_records.keys()) | rule_resolved_files
                )
                three_way = await _get_round_three_way(
                    round_commits, already_done, state, ctx
                )
                round_llm_files = {
                    fp: tw for fp, tw in three_way.items() if fp in stream_a_files
                }
                if not round_llm_files:
                    continue

                file_languages = {
                    fp: (
                        file_diffs_map[fp].language or ""
                        if fp in file_diffs_map
                        else ""
                    )
                    for fp in round_llm_files
                }
                per_file_instructions = {
                    it.file_path: it.custom_instruction
                    for it in state.pending_user_decisions
                    if it.user_choice == "llm_with_instruction"
                    and it.custom_instruction
                }
                analyses = await _analyze_round_with_bisect(
                    conflict_analyst,
                    round_commits,
                    round_llm_files,
                    file_languages,
                    project_context=state.config.project_context,
                    per_file_instructions=per_file_instructions or None,
                    fork_ref=state.config.fork_ref,
                )
                parsed_count = len(analyses)
                requested_count = len(round_llm_files)
                missing_files = [fp for fp in round_llm_files if fp not in analyses]

                for fp in missing_files:
                    analyses[fp] = ConflictAnalysis(
                        file_path=fp,
                        conflict_points=[],
                        overall_confidence=0.3,
                        recommended_strategy=MergeDecision.ESCALATE_HUMAN,
                        conflict_type=ConflictType.UNKNOWN,
                        rationale="Commit-round LLM did not return analysis for file",
                        confidence=0.3,
                    )

                if missing_files:
                    sample = missing_files[:3]
                    ctx.notify(
                        "conflict_analyst",
                        f"Round {round_idx}/{len(rounds)}: "
                        f"{len(missing_files)}/{requested_count} files missing "
                        f"LLM analysis → escalate_human (sample={sample})",
                    )
                    logger.warning(
                        "Round %d/%d: only %d/%d analyses parsed; "
                        "%d files fell back to ESCALATE_HUMAN (sample=%s)",
                        round_idx,
                        len(rounds),
                        parsed_count,
                        requested_count,
                        len(missing_files),
                        sample,
                    )

                state.conflict_analyses.update(analyses)
                ctx.checkpoint.save(state, f"phase3_round_{round_idx}")

                if conflict_analyst.consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
                    circuit_breaker_open = True
                    logger.error(
                        "Circuit breaker tripped after round %d/%d "
                        "(consecutive_failures=%d) — remaining rounds will "
                        "be skipped",
                        round_idx,
                        len(rounds),
                        conflict_analyst.consecutive_failures,
                    )
                    ctx.notify(
                        "conflict_analyst",
                        f"Circuit breaker open after round {round_idx}/{len(rounds)} "
                        "— skipping remaining commit-rounds",
                    )

                logger.info(
                    "Round %d/%d done: %d/%d analyses parsed",
                    round_idx,
                    len(rounds),
                    parsed_count,
                    requested_count,
                )

        # --- Stream B: per-file LLM (plan HUMAN_REQUIRED/AUTO_RISKY, no commit context) ---
        total_b = len(stream_b_files)
        for idx, file_path in enumerate(stream_b_files, 1):
            fd = file_diffs_map.get(file_path)  # type: ignore[assignment]
            if fd is None:
                continue

            if circuit_breaker_open:
                logger.warning(
                    "Circuit breaker open — skipping %s, escalating to human",
                    file_path,
                )
                state.conflict_analyses[file_path] = ConflictAnalysis(
                    file_path=file_path,
                    conflict_points=[],
                    overall_confidence=0.0,
                    recommended_strategy=MergeDecision.ESCALATE_HUMAN,
                    conflict_type=ConflictType.UNKNOWN,
                    rationale="LLM analysis skipped — circuit breaker open",
                    confidence=0.0,
                )
                continue

            ctx.notify(
                "conflict_analyst",
                f"Analyzing {file_path} ({idx}/{total_b})",
            )

            base_content = target_content = current_content = None
            if conflict_analyst.git_tool and state.merge_base_commit:
                base_content, current_content, target_content = (
                    conflict_analyst.git_tool.get_three_way_diff(
                        state.merge_base_commit,
                        state.config.fork_ref,
                        state.config.upstream_ref,
                        file_path,
                    )
                )

            analysis = await conflict_analyst.analyze_file(
                fd,
                base_content=base_content,
                current_content=current_content,
                target_content=target_content,
                project_context=state.config.project_context,
                referenced_names=state.dependency_graph.referenced_symbols(
                    fd.file_path
                ),
                symbol_weights=weights_from_fanin(
                    state.dependency_graph.symbol_fanin(fd.file_path)
                ),
                impact_hint=state.dependency_graph.impact_hint(
                    fd.file_path,
                    max_depth=state.config.dependency_graph.max_depth,
                    god_node_min_dependents=(
                        state.config.dependency_graph.god_node_min_dependents
                    ),
                ),
                fork_ref=state.config.fork_ref,
            )
            state.conflict_analyses[file_path] = analysis

            ctx.notify(
                "conflict_analyst",
                f"Analyzed {file_path} ({idx}/{total_b}) — "
                f"confidence={analysis.confidence:.0%}",
            )

            if conflict_analyst.consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
                circuit_breaker_open = True

        needs_human: list[str] = []
        decided = 0
        total_analyses = len(state.conflict_analyses)
        for file_path, analysis in state.conflict_analyses.items():
            if file_path in state.file_decision_records:
                continue

            fd = file_diffs_map.get(file_path)  # type: ignore[assignment]
            synthesized = False
            if fd is None:
                # A-fix: files surfaced by auto_merge (B-class drift / fork
                # preservation losses) carry an analysis but have no FileDiff
                # entry. Synthesize a minimal one so the strategy executes
                # rather than being silently dropped, which would leave the
                # file undecided and trigger an AWAITING_HUMAN ⇄ ANALYZING
                # ping-pong via _unanalyzed_conflict_files.
                fd = _synthesize_minimal_filediff(
                    file_path,
                    ctx.git_tool,
                    state.config.upstream_ref,
                    state.config.fork_ref,
                )
                file_diffs_map[file_path] = fd
                synthesized = True

            strategy = _select_merge_strategy(analysis, state.config.thresholds)
            # SEMANTIC_MERGE needs hunks/diff context that a synthesized
            # FileDiff cannot provide — downgrade to ESCALATE_HUMAN.
            if synthesized and strategy == MergeDecision.SEMANTIC_MERGE:
                strategy = MergeDecision.ESCALATE_HUMAN
            decided += 1

            if strategy == MergeDecision.ESCALATE_HUMAN:
                needs_human.append(file_path)
                req = _build_human_decision_request(
                    fd,
                    analysis,
                    upstream_ref=state.config.upstream_ref,
                    fork_ref=state.config.fork_ref,
                    git_tool=ctx.git_tool,
                    impact_hint=state.dependency_graph.impact_hint(
                        file_path,
                        max_depth=state.config.dependency_graph.max_depth,
                        god_node_min_dependents=(
                            state.config.dependency_graph.god_node_min_dependents
                        ),
                    ),
                )
                state.human_decision_requests[file_path] = req
            elif strategy == MergeDecision.SEMANTIC_MERGE:
                record = await executor.execute_semantic_merge(fd, analysis, state)
                state.file_decision_records[file_path] = record
                ctx.checkpoint.save(state, f"phase3_{file_path.replace('/', '_')}")
            else:
                record = await executor.execute_auto_merge(fd, strategy, state)
                state.file_decision_records[file_path] = record

            ctx.notify(
                "conflict_analyst",
                f"Strategy decided ({decided}/{total_analyses}): {file_path} → {strategy.value}"
                + (" [synthesized fd]" if synthesized else ""),
            )

        if ctx.config.history.enabled and ctx.config.history.commit_after_phase:
            resolved_files = [
                fp
                for fp in state.conflict_analyses
                if fp in state.file_decision_records
                and not state.file_decision_records[fp].is_rolled_back
                and fp not in needs_human
            ]
            if resolved_files:
                committer = GitCommitter()
                replayer = CommitReplayer()
                upstream_ctx = replayer.collect_upstream_messages(
                    ctx.git_tool,
                    state.merge_base_commit,
                    state.config.upstream_ref,
                    resolved_files,
                )
                committer.commit_phase_changes(
                    ctx.git_tool,
                    state,
                    "conflict_resolution",
                    resolved_files,
                    upstream_context=upstream_ctx,
                )

        phase_result = phase_result.model_copy(
            update={"status": "completed", "completed_at": datetime.now()}
        )
        state.phase_results[MergePhase.CONFLICT_ANALYSIS.value] = phase_result

        if needs_human:
            ctx.state_machine.transition(
                state,
                SystemStatus.AWAITING_HUMAN,
                f"{len(needs_human)} files need human review",
            )
            return PhaseOutcome(
                target_status=SystemStatus.AWAITING_HUMAN,
                reason=f"{len(needs_human)} files need human review",
                checkpoint_tag="after_phase3",
                memory_phase="conflict_analysis",
            )
        else:
            ctx.state_machine.transition(
                state,
                SystemStatus.JUDGE_REVIEWING,
                "conflict analysis complete",
            )
            return PhaseOutcome(
                target_status=SystemStatus.JUDGE_REVIEWING,
                reason="conflict analysis complete",
                checkpoint_tag="after_phase3",
                memory_phase="conflict_analysis",
            )
