from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from src.agents.base_agent import CIRCUIT_BREAKER_THRESHOLD
from src.core.phases.base import Phase, PhaseContext, PhaseOutcome
from src.models.conflict import ConflictAnalysis, ConflictType
from src.models.config import ThresholdConfig
from src.models.decision import MergeDecision
from src.models.diff import FileDiff, FileChangeCategory
from src.models.human import HumanDecisionRequest, DecisionOption
from src.models.plan import MergePhase
from src.models.state import MergeState, PhaseResult, SystemStatus
from src.tools.commit_replayer import CommitReplayer
from src.tools.git_committer import GitCommitter
from src.tools.git_tool import GitTool
from src.tools.rule_resolver import RuleBasedResolver

logger = logging.getLogger(__name__)


def build_commit_rounds(
    commits: list[dict[str, Any]],
    round_size: int,
) -> list[list[dict[str, Any]]]:
    rounds: list[list[dict[str, Any]]] = []
    current_round: list[dict[str, Any]] = []
    current_files: set[str] = set()

    for commit in commits:
        commit_files = set(commit.get("files", []))
        if (commit_files & current_files and current_round) or (
            len(current_round) >= round_size
        ):
            rounds.append(current_round)
            current_round = []
            current_files = set()
        current_round.append(commit)
        current_files |= commit_files

    if current_round:
        rounds.append(current_round)
    return rounds


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


def _select_merge_strategy(
    analysis: ConflictAnalysis, thresholds: ThresholdConfig
) -> MergeDecision:
    if analysis.is_security_sensitive:
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

    return HumanDecisionRequest(
        file_path=fd.file_path,
        priority=1 if fd.is_security_sensitive else 5,
        conflict_points=analysis.conflict_points,
        context_summary=f"File {fd.file_path} has conflicts requiring human review",
        upstream_change_summary=f"Upstream added {fd.lines_added} lines",
        fork_change_summary=f"Fork deleted {fd.lines_deleted} lines",
        analyst_recommendation=rec_val,
        analyst_confidence=analysis.confidence,
        analyst_rationale=analysis.rationale,
        options=options,
        created_at=datetime.now(),
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

        llm_files = [
            fp
            for fp in high_risk_files
            if fp not in rule_resolved_files and fp not in d_missing_resolved
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
            rounds = build_commit_rounds(stream_a_commits, round_size)
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
                analyses = await conflict_analyst.analyze_commit_round(
                    round_commits,
                    round_llm_files,
                    file_languages,
                    project_context=state.config.project_context,
                )

                for fp in round_llm_files:
                    if fp not in analyses:
                        analyses[fp] = ConflictAnalysis(
                            file_path=fp,
                            conflict_points=[],
                            overall_confidence=0.3,
                            recommended_strategy=MergeDecision.ESCALATE_HUMAN,
                            conflict_type=ConflictType.UNKNOWN,
                            rationale="Commit-round LLM did not return analysis for file",
                            confidence=0.3,
                        )

                state.conflict_analyses.update(analyses)
                ctx.checkpoint.save(state, f"phase3_round_{round_idx}")

                if conflict_analyst.consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
                    circuit_breaker_open = True

                logger.info(
                    "Round %d/%d done: %d files analyzed",
                    round_idx,
                    len(rounds),
                    len(round_llm_files),
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
            if conflict_analyst.git_tool and hasattr(state, "_merge_base"):
                base_content, current_content, target_content = (
                    conflict_analyst.git_tool.get_three_way_diff(
                        state._merge_base or "",
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
            fd = file_diffs_map.get(file_path)  # type: ignore[assignment]
            if fd is None:
                continue

            strategy = _select_merge_strategy(analysis, state.config.thresholds)
            decided += 1

            if strategy == MergeDecision.ESCALATE_HUMAN:
                needs_human.append(file_path)
                req = _build_human_decision_request(
                    fd,
                    analysis,
                    upstream_ref=state.config.upstream_ref,
                    fork_ref=state.config.fork_ref,
                    git_tool=ctx.git_tool,
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
                f"Strategy decided ({decided}/{total_analyses}): {file_path} → {strategy.value}",
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
