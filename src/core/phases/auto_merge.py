from __future__ import annotations

import asyncio
import logging
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from src.agents.base_agent import CIRCUIT_BREAKER_THRESHOLD
from src.agents.executor_agent import ExecutorAgent
from src.agents.judge_agent import JudgeAgent
from src.core.phases.base import Phase, PhaseContext, PhaseOutcome
from src.core.phases._gate_helpers import (
    append_execution_record,
    build_layer_index,
    get_layer_gates,
    handle_gate_failure,
    run_gates,
    verify_layer_deps,
)
from src.core.read_only_state_view import ReadOnlyStateView
from src.models.decision import DecisionSource, FileDecisionRecord, MergeDecision
from src.models.diff import FileDiff, FileChangeCategory, FileStatus, RiskLevel
from src.models.dispute import PlanDisputeRequest
from src.models.human import DecisionOption as HumanDecisionOption
from src.models.human import HumanDecisionRequest
from src.models.judge import BatchVerdict
from src.models.plan import MergePhase, PhaseFileBatch
from src.models.plan_review import DecisionOption, UserDecisionItem
from src.models.state import MergeState, PhaseResult, SystemStatus
from src.tools.binary_assets import is_binary_asset
from src.tools.commit_replayer import CommitReplayer
from src.tools.conflict_markers import file_has_conflict_markers
from src.tools.git_committer import GitCommitter

logger = logging.getLogger(__name__)

# O-B5: drift count above which we treat the run as systemic-bug and
# escalate without running the (very expensive) downstream analysis.
_B_CLASS_DRIFT_FATAL_THRESHOLD = 100

_DEP_BUMP_RE = re.compile(
    r"(bump|chore[\(\[]deps|update[- ]dep|dependabot|renovate|"
    r"upgrade[- ]dep|security[- ]update|pin[- ]dep)",
    re.IGNORECASE,
)

_LOCK_FILE_NAMES: frozenset[str] = frozenset(
    {
        "poetry.lock",
        "package-lock.json",
        "yarn.lock",
        "Pipfile.lock",
        "go.sum",
        "Cargo.lock",
        "composer.lock",
        "Gemfile.lock",
        "pdm.lock",
        "uv.lock",
        "pnpm-lock.yaml",
        "npm-shrinkwrap.json",
        "shrinkwrap.yaml",
    }
)

_DEP_MANIFEST_NAMES: frozenset[str] = frozenset(
    {
        "requirements.txt",
        "Pipfile",
        "go.mod",
        "pyproject.toml",
        "setup.cfg",
        "setup.py",
        "package.json",
        "Cargo.toml",
        "composer.json",
        "Gemfile",
        "build.gradle",
        "pom.xml",
    }
)


def _is_dep_bump_commit(message: str) -> bool:
    return bool(_DEP_BUMP_RE.search(message))


def _is_lock_file(file_path: str) -> bool:
    return Path(file_path).name in _LOCK_FILE_NAMES


def _is_dep_manifest(file_path: str) -> bool:
    name = Path(file_path).name
    if name in _DEP_MANIFEST_NAMES:
        return True
    return bool(re.match(r"requirements[^/]*\.txt$", name, re.IGNORECASE))


class AutoMergePhase(Phase):
    name = "auto_merge"

    async def execute(self, state: MergeState, ctx: PhaseContext) -> PhaseOutcome:
        state.current_phase = MergePhase.AUTO_MERGE
        phase_result = PhaseResult(
            phase=MergePhase.AUTO_MERGE,
            status="running",
            started_at=datetime.now(),
        )
        state.phase_results[MergePhase.AUTO_MERGE.value] = phase_result

        if state.merge_plan is None:
            raise ValueError("No merge plan available for phase 2")

        executor: ExecutorAgent = ctx.agents["executor"]
        judge: JudgeAgent = ctx.agents["judge"]
        file_diffs_map: dict[str, FileDiff] = {
            fd.file_path: fd for fd in state.file_diffs
        }

        replayed_set: set[str] = set()
        if ctx.config.history.enabled and ctx.config.history.cherry_pick_clean:
            replayable = state.replayable_commits
            partial = state.partial_replayable_commits
            if replayable or partial:
                replayer = CommitReplayer()
                ctx.notify(
                    "executor",
                    f"Cherry-picking {len(replayable)} clean + "
                    f"{len(partial)} partial commits",
                )
                replay_result = await replayer.replay_clean_commits(
                    ctx.git_tool, replayable, state
                )
                # O-R1: fall back to per-file cherry-pick for mixed commits.
                if partial:
                    await replayer.replay_partial_commits(
                        ctx.git_tool, partial, replay_result
                    )
                replayed_set = set(replay_result.replayed_files)
                state.partial_replays = list(replay_result.partial_replays)
                logger.info(
                    "Replay: %d commits cherry-picked (%d partial), %d failed",
                    len(replay_result.replayed_shas),
                    len(replay_result.partial_replays),
                    len(replay_result.failed_shas),
                )
                # Record replayed files in file_decision_records so they appear
                # in `seen` during routing and are never re-sent to conflict
                # analysis. Cherry-pick == TAKE_TARGET applied by git.
                for _fp in replay_result.replayed_files:
                    if _fp in state.file_decision_records:
                        continue
                    _fd = file_diffs_map.get(_fp)
                    state.file_decision_records[_fp] = FileDecisionRecord(
                        file_path=_fp,
                        file_status=(
                            _fd.file_status if _fd is not None else FileStatus.MODIFIED
                        ),
                        decision=MergeDecision.TAKE_TARGET,
                        decision_source=DecisionSource.AUTO_EXECUTOR,
                        confidence=0.99,
                        rationale="Cherry-picked cleanly from upstream commit",
                        phase="auto_merge",
                        agent="commit_replayer",
                    )

        # --- O-M1: scan working tree for files with unresolved conflict
        # markers (<<<<<<< / ======= / >>>>>>>) left over by cherry-pick
        # fall-back. Route them directly to human review: skip AUTO_MERGE
        # and the Judge pipeline, both of which cannot recover from the
        # markers being part of the stored content. ---
        repo_path = Path(ctx.git_tool.repo_path)
        files_with_markers: list[str] = []
        for batch in state.merge_plan.phases:
            if batch.risk_level not in (RiskLevel.AUTO_SAFE, RiskLevel.AUTO_RISKY):
                continue
            for file_path in batch.file_paths:
                if file_path in replayed_set:
                    continue
                if file_path in state.file_decision_records:
                    continue
                if file_has_conflict_markers(repo_path, file_path):
                    files_with_markers.append(file_path)

        if files_with_markers:
            logger.warning(
                "O-M1: %d file(s) contain unresolved conflict markers — "
                "escalating to human review: %s",
                len(files_with_markers),
                ", ".join(files_with_markers[:10])
                + (" ..." if len(files_with_markers) > 10 else ""),
            )
            marker_set = set(files_with_markers)
            for fp in files_with_markers:
                fd_item = file_diffs_map.get(fp)
                state.file_decision_records[fp] = FileDecisionRecord(
                    file_path=fp,
                    file_status=(
                        fd_item.file_status
                        if fd_item is not None
                        else FileStatus.MODIFIED
                    ),
                    decision=MergeDecision.ESCALATE_HUMAN,
                    decision_source=DecisionSource.AUTO_EXECUTOR,
                    confidence=0.0,
                    rationale=(
                        "Working tree contains unresolved git conflict "
                        "markers (<<<<<<< / ======= / >>>>>>>) — cherry-pick "
                        "fall-back likely left an unresolved merge. Escalated "
                        "before AUTO_MERGE to avoid feeding markers to Judge/"
                        "Executor (O-M1)."
                    ),
                    phase="auto_merge",
                    agent="conflict_marker_scanner",
                )
            for batch in state.merge_plan.phases:
                if batch.risk_level in (RiskLevel.AUTO_SAFE, RiskLevel.AUTO_RISKY):
                    batch.file_paths = [
                        fp for fp in batch.file_paths if fp not in marker_set
                    ]
            existing_plan_paths = {
                item.file_path for item in state.pending_user_decisions
            }
            for fp in files_with_markers:
                if fp in existing_plan_paths:
                    continue
                state.pending_user_decisions.append(
                    UserDecisionItem(
                        item_id=f"conflict_markers_{fp}",
                        file_path=fp,
                        description=(
                            f"File '{fp}' contains unresolved git conflict "
                            "markers from cherry-pick fall-back. Needs human "
                            "resolution before merge can proceed."
                        ),
                        risk_context="unresolved_conflict_markers",
                        current_classification=RiskLevel.HUMAN_REQUIRED.value,
                        options=[
                            DecisionOption(
                                key="approve_human",
                                label="Manual review",
                                description=(
                                    "You will resolve the conflict markers by "
                                    "hand before continuing"
                                ),
                            ),
                            DecisionOption(
                                key="take_target",
                                label="Take upstream",
                                description=(
                                    "Replace the conflicted file with the "
                                    "upstream version as-is"
                                ),
                            ),
                            DecisionOption(
                                key="take_current",
                                label="Keep fork",
                                description=(
                                    "Keep the fork version and drop the "
                                    "upstream change for this file"
                                ),
                            ),
                        ],
                    )
                )

        # --- O-B3: route binary asset files (png/jpg/woff/mp3/zip/...) away
        # from the LLM batch pipeline, which cannot handle them without
        # UTF-8 decode errors. Category-aware routing:
        #   * C (both sides modified)  -> escalate to human decision
        #   * anything else (B/D_*/A)  -> TAKE_TARGET via copy_from_upstream
        binary_take_target: list[str] = []
        binary_escalate: list[str] = []
        for batch in state.merge_plan.phases:
            if batch.risk_level not in (RiskLevel.AUTO_SAFE, RiskLevel.AUTO_RISKY):
                continue
            for file_path in batch.file_paths:
                if file_path in replayed_set:
                    continue
                if file_path in state.file_decision_records:
                    continue
                if not is_binary_asset(file_path):
                    continue
                fd_item = file_diffs_map.get(file_path)
                category = fd_item.change_category if fd_item else None
                if category == FileChangeCategory.C:
                    binary_escalate.append(file_path)
                else:
                    binary_take_target.append(file_path)

        if binary_take_target or binary_escalate:
            logger.info(
                "O-B3: routing %d binary asset(s) to TAKE_TARGET and %d to "
                "human escalation (bypassing LLM batch review)",
                len(binary_take_target),
                len(binary_escalate),
            )

        for fp in binary_take_target:
            try:
                # O-B4: use the bytes-safe path — executor._copy_from_upstream
                # routes through apply_with_snapshot which write_text()s and
                # explodes on non-UTF-8 bytes (e.g. PNG magic 0x89).
                from src.tools.patch_applier import apply_bytes_with_snapshot

                content_bytes = ctx.git_tool.get_file_bytes(
                    state.config.upstream_ref, fp
                )
                if content_bytes is None:
                    raise RuntimeError("upstream bytes not found")
                record = await apply_bytes_with_snapshot(
                    fp,
                    content_bytes,
                    ctx.git_tool,
                    state,
                    phase="auto_merge",
                    agent="binary_asset_router",
                    decision=MergeDecision.TAKE_TARGET,
                    rationale=(
                        "O-B3 binary asset — taking upstream version as raw "
                        "bytes (O-B4 binary-safe writer)."
                    ),
                )
            except Exception as exc:
                logger.warning(
                    "O-B3/O-B4: binary copy failed for %s: %s",
                    fp,
                    exc,
                )
                record = FileDecisionRecord(
                    file_path=fp,
                    file_status=FileStatus.MODIFIED,
                    decision=MergeDecision.ESCALATE_HUMAN,
                    decision_source=DecisionSource.AUTO_EXECUTOR,
                    confidence=0.0,
                    rationale=(
                        f"Binary asset TAKE_TARGET failed ({exc!r}); "
                        "escalating (O-B3 fallback)."
                    ),
                    phase="auto_merge",
                    agent="binary_asset_router",
                )
            state.file_decision_records[fp] = record

        if binary_escalate:
            binary_marker_set = set(binary_escalate)
            for fp in binary_escalate:
                fd_item = file_diffs_map.get(fp)
                state.file_decision_records[fp] = FileDecisionRecord(
                    file_path=fp,
                    file_status=(
                        fd_item.file_status
                        if fd_item is not None
                        else FileStatus.MODIFIED
                    ),
                    decision=MergeDecision.ESCALATE_HUMAN,
                    decision_source=DecisionSource.AUTO_EXECUTOR,
                    confidence=0.0,
                    rationale=(
                        "Binary asset with changes on both sides (category C) — "
                        "cannot be auto-merged by LLM or text-diff; escalating "
                        "to human review (O-B3)."
                    ),
                    phase="auto_merge",
                    agent="binary_asset_router",
                )
            existing_plan_paths = {
                item.file_path for item in state.pending_user_decisions
            }
            for fp in binary_escalate:
                if fp in existing_plan_paths:
                    continue
                state.pending_user_decisions.append(
                    UserDecisionItem(
                        item_id=f"binary_asset_{fp}",
                        file_path=fp,
                        description=(
                            f"Binary asset '{fp}' has conflicting changes on "
                            "both sides and cannot be diffed by the LLM."
                        ),
                        risk_context="binary_asset_both_modified",
                        current_classification=RiskLevel.HUMAN_REQUIRED.value,
                        options=[
                            DecisionOption(
                                key="take_target",
                                label="Take upstream",
                                description="Replace with upstream version as-is",
                            ),
                            DecisionOption(
                                key="take_current",
                                label="Keep fork",
                                description="Keep fork version; drop upstream change",
                            ),
                            DecisionOption(
                                key="approve_human",
                                label="Manual review",
                                description=(
                                    "Defer to human reviewer to pick the binary"
                                ),
                            ),
                        ],
                    )
                )
            # Remove these files from their original batches so the main
            # merge loop skips the LLM call.
            for batch in state.merge_plan.phases:
                if batch.risk_level in (RiskLevel.AUTO_SAFE, RiskLevel.AUTO_RISKY):
                    batch.file_paths = [
                        fp for fp in batch.file_paths if fp not in binary_marker_set
                    ]

        if binary_take_target:
            take_target_set = set(binary_take_target)
            for batch in state.merge_plan.phases:
                if batch.risk_level in (RiskLevel.AUTO_SAFE, RiskLevel.AUTO_RISKY):
                    batch.file_paths = [
                        fp for fp in batch.file_paths if fp not in take_target_set
                    ]

        # --- Pre-pass: handle HUMAN_REQUIRED and DELETED_ONLY before any merge ---
        # Dedupe: auto_merge may run multiple times (e.g. after conflict
        # analysis rebounds); keep each file's first entry (preferring any
        # row that already has a user_choice). Without this, each invocation
        # appends fresh undecided copies and the phase loops forever.
        _seen: dict[str, int] = {}
        _deduped: list[UserDecisionItem] = []
        for item in state.pending_user_decisions:
            idx = _seen.get(item.file_path)
            if idx is None:
                _seen[item.file_path] = len(_deduped)
                _deduped.append(item)
            else:
                if _deduped[idx].user_choice is None and item.user_choice is not None:
                    _deduped[idx] = item
        if len(_deduped) != len(state.pending_user_decisions):
            logger.info(
                "Deduplicated pending_user_decisions: %d -> %d",
                len(state.pending_user_decisions),
                len(_deduped),
            )
            state.pending_user_decisions = _deduped
        existing_item_paths = {item.file_path for item in state.pending_user_decisions}
        for batch in state.merge_plan.phases:
            if batch.risk_level == RiskLevel.HUMAN_REQUIRED:
                for file_path in batch.file_paths:
                    if file_path in existing_item_paths:
                        continue
                    state.pending_user_decisions.append(
                        UserDecisionItem(
                            item_id=f"human_required_{file_path}",
                            file_path=file_path,
                            description=(
                                f"File '{file_path}' requires human review "
                                f"(risk_level=HUMAN_REQUIRED)."
                            ),
                            risk_context=(
                                f"Change category: {batch.change_category}. "
                                "High risk or security-sensitive file."
                            ),
                            current_classification=RiskLevel.HUMAN_REQUIRED.value,
                            options=[
                                DecisionOption(
                                    key="A",
                                    label="approve_merge",
                                    description="Approve auto-merge attempt for this file",
                                ),
                                DecisionOption(
                                    key="B",
                                    label="keep_current",
                                    description="Keep fork version (skip upstream changes)",
                                ),
                                DecisionOption(
                                    key="C",
                                    label="take_upstream",
                                    description="Take upstream version as-is",
                                ),
                            ],
                        )
                    )
            elif batch.risk_level == RiskLevel.DELETED_ONLY:
                for file_path in batch.file_paths:
                    if file_path in existing_item_paths:
                        continue
                    fd = file_diffs_map.get(file_path)
                    if fd is not None:
                        item = await executor.analyze_deletion(file_path, fd, state)
                        state.pending_user_decisions.append(item)

        undecided_items = [
            item for item in state.pending_user_decisions if item.user_choice is None
        ]
        if undecided_items:
            ctx.state_machine.transition(
                state,
                SystemStatus.AWAITING_HUMAN,
                "pre-pass: HUMAN_REQUIRED or DELETED_ONLY decisions needed before merge",
            )
            return PhaseOutcome(
                target_status=SystemStatus.AWAITING_HUMAN,
                reason="pre-pass: pending human decisions before merge",
                checkpoint_tag="after_phase2_prepass",
                memory_phase="auto_merge",
            )

        # O-L5: execute UserDecisionItem take_target / take_current choices.
        # Previously these user selections updated state but never wrote any
        # file — downstream pipelines saw the ESCALATE_HUMAN record that O-M1
        # / O-B3 seeded and the working tree was never updated. We now
        # actualize the choice here, overwrite file_decision_records, and
        # remove the file from future batches.
        _l5_take_target_keys = {"take_target"}
        _l5_take_current_keys = {"take_current"}
        _l5_applied: set[str] = set()
        for item in state.pending_user_decisions:
            if item.user_choice not in _l5_take_target_keys | _l5_take_current_keys:
                continue
            fp = item.file_path
            if fp in _l5_applied:
                continue
            ref = (
                state.config.upstream_ref
                if item.user_choice in _l5_take_target_keys
                else state.config.fork_ref
            )
            decision_value = (
                MergeDecision.TAKE_TARGET
                if item.user_choice in _l5_take_target_keys
                else MergeDecision.TAKE_CURRENT
            )
            try:
                if is_binary_asset(fp):
                    from src.tools.patch_applier import apply_bytes_with_snapshot

                    content_bytes = ctx.git_tool.get_file_bytes(ref, fp)
                    if content_bytes is None:
                        raise RuntimeError(f"{ref}:{fp} bytes not found")
                    record = await apply_bytes_with_snapshot(
                        fp,
                        content_bytes,
                        ctx.git_tool,
                        state,
                        phase="auto_merge",
                        agent="user_choice_executor",
                        decision=decision_value,
                        rationale=(
                            f"O-L5: executing user_choice={item.user_choice!r} "
                            f"for {item.risk_context or item.item_id} via "
                            "binary-safe path"
                        ),
                    )
                else:
                    from src.tools.patch_applier import apply_with_snapshot

                    content = ctx.git_tool.get_file_content(ref, fp)
                    if content is None:
                        raise RuntimeError(f"{ref}:{fp} content not found")
                    record = await apply_with_snapshot(
                        fp,
                        content,
                        ctx.git_tool,
                        state,
                        phase="auto_merge",
                        agent="user_choice_executor",
                        decision=decision_value,
                        rationale=(
                            f"O-L5: executing user_choice={item.user_choice!r} "
                            f"for {item.risk_context or item.item_id}"
                        ),
                    )
            except Exception as exc:
                logger.warning(
                    "O-L5: failed to execute user_choice=%s for %s: %s",
                    item.user_choice,
                    fp,
                    exc,
                )
                record = FileDecisionRecord(
                    file_path=fp,
                    file_status=FileStatus.MODIFIED,
                    decision=MergeDecision.ESCALATE_HUMAN,
                    decision_source=DecisionSource.AUTO_EXECUTOR,
                    confidence=0.0,
                    rationale=(
                        f"O-L5 execute user_choice={item.user_choice!r} failed "
                        f"({exc!r}); keeping ESCALATE_HUMAN."
                    ),
                    phase="auto_merge",
                    agent="user_choice_executor",
                )
            state.file_decision_records[fp] = record
            _l5_applied.add(fp)

        if _l5_applied:
            logger.info(
                "O-L5: executed user_choice for %d file(s) (take_target/take_current)",
                len(_l5_applied),
            )
            for batch in state.merge_plan.phases:
                batch.file_paths = [
                    fp for fp in batch.file_paths if fp not in _l5_applied
                ]

        # All plan-level decisions filled in: apply downgrades so the main
        # loop actually processes HUMAN_REQUIRED files the user downgraded.
        # Split each HUMAN_REQUIRED batch into:
        #   - keep_human: stays HUMAN_REQUIRED (user chose approve_human)
        #   - downgrade_risky: new AUTO_RISKY batch (will go through gates)
        #   - downgrade_safe: new AUTO_SAFE batch (trust system)
        user_choice_by_path: dict[str, str] = {
            it.file_path: it.user_choice
            for it in state.pending_user_decisions
            if it.user_choice is not None
        }
        if user_choice_by_path:
            new_phases: list[PhaseFileBatch] = []
            _risky_keys = {"downgrade_risky", "confirm_risky"}
            _safe_keys = {"downgrade_safe"}
            _human_keys = {"approve_human", "upgrade_human", "approve_merge"}
            for batch in state.merge_plan.phases:
                if batch.risk_level != RiskLevel.HUMAN_REQUIRED:
                    new_phases.append(batch)
                    continue
                bucket_human: list[str] = []
                bucket_risky: list[str] = []
                bucket_safe: list[str] = []
                for fp in batch.file_paths:
                    choice = user_choice_by_path.get(fp)
                    if choice in _risky_keys:
                        bucket_risky.append(fp)
                    elif choice in _safe_keys:
                        bucket_safe.append(fp)
                    else:
                        bucket_human.append(fp)
                if bucket_human:
                    new_phases.append(
                        batch.model_copy(update={"file_paths": bucket_human})
                    )
                if bucket_risky:
                    new_phases.append(
                        batch.model_copy(
                            update={
                                "batch_id": f"{batch.batch_id}_downgrade_risky",
                                "file_paths": bucket_risky,
                                "risk_level": RiskLevel.AUTO_RISKY,
                            }
                        )
                    )
                if bucket_safe:
                    new_phases.append(
                        batch.model_copy(
                            update={
                                "batch_id": f"{batch.batch_id}_downgrade_safe",
                                "file_paths": bucket_safe,
                                "risk_level": RiskLevel.AUTO_SAFE,
                            }
                        )
                    )
            state.merge_plan = state.merge_plan.model_copy(
                update={"phases": new_phases}
            )
            logger.info(
                "Applied user downgrades: %d files affected",
                sum(
                    1
                    for c in user_choice_by_path.values()
                    if c in _risky_keys | _safe_keys
                ),
            )

        # --- Main loop: layer-based, parallel within each layer ---
        batch_count = 0
        phase_changed_files: list[str] = []
        completed_layers: set[int] = set()
        layer_index = build_layer_index(state)
        max_dispute = ctx.config.max_dispute_rounds

        # Group AUTO_SAFE / AUTO_RISKY batches by layer_id (None = no layer)
        layer_batches: dict[int | None, list[PhaseFileBatch]] = defaultdict(list)
        for batch in state.merge_plan.phases:
            if batch.risk_level in (RiskLevel.AUTO_SAFE, RiskLevel.AUTO_RISKY):
                layer_batches[batch.layer_id].append(batch)

        # Sort: None-layer first (no deps), then layers in ascending order
        sorted_layer_ids: list[int | None] = []
        if None in layer_batches:
            sorted_layer_ids.append(None)
        sorted_layer_ids.extend(sorted(k for k in layer_batches if k is not None))

        skipped_layer_files: list[str] = []

        for layer_id in sorted_layer_ids:
            batches = layer_batches[layer_id]

            if layer_id is not None:
                if not verify_layer_deps(layer_id, completed_layers, state):
                    logger.warning("Skipping layer %d: dependencies not met", layer_id)
                    for batch in batches:
                        for fp in batch.file_paths:
                            cat = batch.change_category
                            if cat is None:
                                fd_lookup = file_diffs_map.get(fp)
                                cat = fd_lookup.change_category if fd_lookup else None
                            if (
                                cat == FileChangeCategory.D_MISSING
                                and fp not in replayed_set
                                and fp not in state.file_decision_records
                            ):
                                record = await executor._copy_from_upstream(fp, state)
                                state.file_decision_records[fp] = record
                                phase_changed_files.append(fp)
                                batch_count += 1
                                logger.info(
                                    "D-missing %s copied directly (layer %d deps skipped)",
                                    fp,
                                    layer_id,
                                )
                            else:
                                skipped_layer_files.append(fp)
                    continue

            # Parallel execution of all batches in this layer
            layer_results = await asyncio.gather(
                *[
                    self._execute_batch(
                        batch, executor, file_diffs_map, replayed_set, state
                    )
                    for batch in batches
                ],
                return_exceptions=True,
            )

            layer_files: list[str] = []
            for result in layer_results:
                if isinstance(result, Exception):
                    logger.error(
                        "Batch execution error in layer %s: %s", layer_id, result
                    )
                else:
                    files: list[str] = result  # type: ignore[assignment]
                    phase_changed_files.extend(files)
                    layer_files.extend(files)
                    batch_count += len(files)

            if batch_count % 10 == 0 and batch_count > 0:
                ctx.checkpoint.save(state, f"phase2_batch_{batch_count}")

            # Per-layer batch Judge sub-review + Executor ↔ Judge dispute loop
            if layer_files:
                readonly = ReadOnlyStateView(state)
                batch_verdict: BatchVerdict = await judge.review_batch(
                    layer_id, layer_files, readonly
                )

                for dispute_round in range(max_dispute):
                    if batch_verdict.approved:
                        ctx.checkpoint.save(
                            state, f"phase2_layer_{layer_id}_batch_approved"
                        )
                        break

                    # O-2: skip remaining dispute rounds if executor circuit breaker is open
                    if executor.consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
                        logger.warning(
                            "Executor circuit breaker OPEN after %d failures — "
                            "aborting dispute rounds for layer %s",
                            executor.consecutive_failures,
                            layer_id,
                        )
                        break

                    rebuttal = await executor.build_rebuttal(
                        batch_verdict.issues, state
                    )

                    if rebuttal.accepts_all:
                        if rebuttal.repair_instructions:
                            await executor.repair(rebuttal.repair_instructions, state)
                        batch_verdict = await judge.review_batch(
                            layer_id, layer_files, ReadOnlyStateView(state)
                        )
                        continue

                    batch_verdict = await judge.re_evaluate(
                        rebuttal, batch_verdict, ReadOnlyStateView(state)
                    )

                if not batch_verdict.approved:
                    logger.warning(
                        "Layer %s batch judge sub-review: no consensus after %d dispute rounds",
                        layer_id,
                        max_dispute,
                    )
                    # O-L3: create real HumanDecisionRequest entries for the
                    # stuck files so HumanReviewPhase.Case 1 can drive them
                    # through the normal pending-decisions flow. Also record
                    # the exhausted layer so the review phase never bounces
                    # the run back into AUTO_MERGING (that was the O-L3 loop).
                    self._register_dispute_exhaustion(
                        state=state,
                        layer_id=layer_id,
                        layer_files=layer_files,
                        batch_verdict=batch_verdict,
                        max_dispute=max_dispute,
                    )
                    ctx.state_machine.transition(
                        state,
                        SystemStatus.AWAITING_HUMAN,
                        f"layer {layer_id} batch judge sub-review failed after "
                        f"{max_dispute} dispute rounds",
                    )
                    return PhaseOutcome(
                        target_status=SystemStatus.AWAITING_HUMAN,
                        reason=f"layer {layer_id} judge sub-review: no consensus",
                        checkpoint_tag="after_phase2",
                        memory_phase="auto_merge",
                        extra={"paused": True},
                    )

            # Layer gate checks
            if layer_id is not None:
                completed_layers.add(layer_id)
                layer_gates = get_layer_gates(layer_id, layer_index)
                if layer_gates:
                    gate_ok = await run_gates(
                        state, ctx, f"layer_{layer_id}", layer_gates
                    )
                    if not gate_ok:
                        gate_blocked = await handle_gate_failure(state, ctx)
                        if gate_blocked:
                            return PhaseOutcome(
                                target_status=SystemStatus.AWAITING_HUMAN,
                                reason="gate failure during layer merge",
                                checkpoint_tag="after_phase2",
                                memory_phase="auto_merge",
                            )

        gate_ok = await run_gates(state, ctx, "auto_merge")
        if not gate_ok:
            gate_blocked = await handle_gate_failure(state, ctx)
            if gate_blocked:
                return PhaseOutcome(
                    target_status=SystemStatus.AWAITING_HUMAN,
                    reason="gate failure after auto-merge",
                    checkpoint_tag="after_phase2",
                    memory_phase="auto_merge",
                )

        commit_sha: str | None = None
        if (
            ctx.config.history.enabled
            and ctx.config.history.commit_after_phase
            and phase_changed_files
        ):
            committer = GitCommitter()
            commit_sha = committer.commit_phase_changes(
                ctx.git_tool,
                state,
                "auto_merge",
                phase_changed_files,
            )

        has_auto_risky = any(
            batch.risk_level == RiskLevel.AUTO_RISKY
            for batch in state.merge_plan.phases
        )

        # Files that didn't make it through auto-merge (skipped layers + non
        # -replayable commits whose files are still untouched) need explicit
        # human conflict analysis. Without this routing the conflict_analyst
        # agent is dead code in the replay path.
        unhandled_conflict_files: list[str] = []
        seen: set[str] = set(state.file_decision_records.keys())
        for fp in skipped_layer_files:
            if fp in seen:
                continue
            unhandled_conflict_files.append(fp)
            seen.add(fp)
        dep_bump_applied = 0
        for commit in state.non_replayable_commits or []:
            commit_msg: str = str(commit.get("message", ""))
            is_bump = _is_dep_bump_commit(commit_msg)
            for fp in commit.get("files", []):
                if fp in seen:
                    continue
                seen.add(fp)
                fd = file_diffs_map.get(fp)
                auto_take = fd is not None and (
                    _is_lock_file(fp) or (is_bump and _is_dep_manifest(fp))
                )
                if auto_take and fd is not None:
                    record = await executor.execute_auto_merge(
                        fd, MergeDecision.TAKE_TARGET, state
                    )
                    state.file_decision_records[fp] = record
                    dep_bump_applied += 1
                    logger.info("Dep-bump auto take_target: %s", fp)
                else:
                    unhandled_conflict_files.append(fp)
        if dep_bump_applied:
            logger.info(
                "Dep-bump pre-filter: applied TAKE_TARGET to %d files, "
                "%d routed to conflict analysis",
                dep_bump_applied,
                len(unhandled_conflict_files),
            )
        # O-B5: byte-level sanity-check on B-class files. Plan promises these
        # equal upstream after auto-merge; if not, the take_target path
        # silently failed somewhere (e.g. cherry-pick `-X theirs` resolved to
        # HEAD content for some files, replay_clean_commits used to mark them
        # all replayed regardless). Without this check, the gap is only
        # caught by Judge — at the cost of 1000+ LLM calls per run.
        b_drift = await self._b_class_sanity_check(state, ctx)
        if len(b_drift) > _B_CLASS_DRIFT_FATAL_THRESHOLD:
            logger.error(
                "O-B5: B-class sanity-check found %d files drifted from "
                "upstream (threshold=%d) — escalating to human, likely "
                "systemic bug. First 10: %s",
                len(b_drift),
                _B_CLASS_DRIFT_FATAL_THRESHOLD,
                b_drift[:10],
            )
            state.pending_conflict_files = b_drift
            return PhaseOutcome(
                target_status=SystemStatus.AWAITING_HUMAN,
                reason=(
                    f"B-class drift ({len(b_drift)} files) exceeds "
                    f"threshold {_B_CLASS_DRIFT_FATAL_THRESHOLD}"
                ),
                checkpoint_tag="after_phase2",
                memory_phase="auto_merge",
            )
        elif b_drift:
            logger.warning(
                "O-B5: %d B-class files drifted from upstream — adding to "
                "conflict analysis queue",
                len(b_drift),
            )
            for fp in b_drift:
                if fp not in seen:
                    unhandled_conflict_files.append(fp)
                    seen.add(fp)

        if unhandled_conflict_files:
            logger.info(
                "Routing %d unhandled files (skipped layers + non-replayable "
                "commits + B-class drift) to conflict analysis",
                len(unhandled_conflict_files),
            )
            state.pending_conflict_files = unhandled_conflict_files

        phase_result = phase_result.model_copy(
            update={"status": "completed", "completed_at": datetime.now()}
        )
        state.phase_results[MergePhase.AUTO_MERGE.value] = phase_result

        append_execution_record(
            state, "auto_merge", phase_result, batch_count, commit_sha=commit_sha
        )

        if state.plan_disputes:
            ctx.state_machine.transition(
                state,
                SystemStatus.PLAN_DISPUTE_PENDING,
                "executor raised plan dispute",
            )
            await self._handle_plan_dispute(state, ctx, state.plan_disputes[-1])
            return PhaseOutcome(
                target_status=state.status,
                reason="plan dispute handled",
                checkpoint_tag="after_phase2",
                memory_phase="auto_merge",
            )
        elif has_auto_risky or unhandled_conflict_files:
            ctx.state_machine.transition(
                state,
                SystemStatus.ANALYZING_CONFLICTS,
                "proceeding to conflict analysis",
            )
            return PhaseOutcome(
                target_status=SystemStatus.ANALYZING_CONFLICTS,
                reason="proceeding to conflict analysis",
                checkpoint_tag="after_phase2",
                memory_phase="auto_merge",
            )
        else:
            ctx.state_machine.transition(
                state,
                SystemStatus.JUDGE_REVIEWING,
                "no risky files, skip to judge review",
            )
            return PhaseOutcome(
                target_status=SystemStatus.JUDGE_REVIEWING,
                reason="no risky files, skip to judge review",
                checkpoint_tag="after_phase2",
                memory_phase="auto_merge",
            )

    def _register_dispute_exhaustion(
        self,
        state: MergeState,
        layer_id: int | None,
        layer_files: list[str],
        batch_verdict: BatchVerdict,
        max_dispute: int,
    ) -> None:
        """O-L3: persist a proper AWAITING_HUMAN signal after batch judge
        dispute exhaustion so the run does not loop.

        Per the validation report's §5.3 finding ("batch dispute upgrade
        is overly conservative"), only files that carry a blocking-level
        issue (per ``config.judge_blocking_levels``) escalate to a
        ``HumanDecisionRequest``. The remaining files in the batch
        reached partial consensus — Judge raised at most advisory issues
        — and are auto-recorded as ``SEMANTIC_MERGE`` so the user is not
        forced to triage 100+ files when only a few are genuinely stuck.

        Writes:
        * one ``HumanDecisionRequest`` per blocking-issue file (unless one
          already exists), so ``HumanReviewPhase.Case 1`` drives the user
          through the normal pending-decisions flow;
        * a partial-consensus ``FileDecisionRecord`` for non-blocking files;
        * the exhausted ``layer_id`` onto
          ``state.auto_merge_dispute_exhausted_layers`` so
          ``HumanReviewPhase.Case 2`` refuses to bounce the run back into
          ``AUTO_MERGING`` after the user provides decisions.
        """

        layer_tag = "None" if layer_id is None else str(layer_id)
        if layer_tag not in state.auto_merge_dispute_exhausted_layers:
            state.auto_merge_dispute_exhausted_layers.append(layer_tag)

        blocking_levels = {
            level.lower()
            for level in (state.config.judge_blocking_levels or ["critical", "high"])
        }

        issues_by_file: dict[str, list[str]] = defaultdict(list)
        blocking_files: set[str] = set()
        for issue in batch_verdict.issues:
            level = (
                issue.issue_level.value
                if hasattr(issue.issue_level, "value")
                else str(issue.issue_level)
            )
            issues_by_file[issue.file_path].append(
                f"[{level}] {issue.issue_type}: {issue.description}"
            )
            if level.lower() in blocking_levels:
                blocking_files.add(issue.file_path)

        non_blocking_count = 0
        for file_path in layer_files:
            if file_path in blocking_files:
                continue
            if file_path in state.file_decision_records:
                continue
            advisory_lines = issues_by_file.get(file_path, [])
            rationale_tail = (
                f"; advisory issues: {len(advisory_lines)}"
                if advisory_lines
                else ""
            )
            state.file_decision_records[file_path] = FileDecisionRecord(
                file_path=file_path,
                file_status=FileStatus.MODIFIED,
                decision=MergeDecision.SEMANTIC_MERGE,
                decision_source=DecisionSource.AUTO_PLANNER,
                confidence=0.7,
                rationale=(
                    f"batch dispute partial-consensus (layer={layer_tag}): "
                    f"no blocking-level issue after {max_dispute} rounds"
                    f"{rationale_tail}"
                ),
                phase="auto_merge",
                agent="dispute_exhaustion",
            )
            non_blocking_count += 1

        if non_blocking_count:
            logger.info(
                "Layer %s dispute exhaustion: %d files auto-resolved "
                "(no blocking issue), %d files escalated to human",
                layer_tag,
                non_blocking_count,
                len(blocking_files),
            )

        now = datetime.now()
        for file_path in sorted(blocking_files):
            if file_path in state.human_decision_requests:
                continue
            issue_lines = issues_by_file.get(file_path, [])
            summary_blob = (
                "\n".join(issue_lines)
                if issue_lines
                else (
                    "Judge did not converge on this file within "
                    f"{max_dispute} dispute rounds."
                )
            )
            state.human_decision_requests[file_path] = HumanDecisionRequest(
                file_path=file_path,
                priority=5,
                conflict_points=[],
                context_summary=(
                    f"AUTO_MERGE layer {layer_tag} batch judge sub-review did "
                    f"not reach consensus after {max_dispute} dispute rounds "
                    f"(O-L3). Executor's repairs did not resolve all "
                    "remaining blocking issues."
                ),
                upstream_change_summary=(
                    "(see Judge issues summary in options.preview_content)"
                ),
                fork_change_summary=(
                    "(see Judge issues summary in options.preview_content)"
                ),
                analyst_recommendation=MergeDecision.ESCALATE_HUMAN,
                analyst_confidence=0.0,
                analyst_rationale=(
                    f"{len(issue_lines)} remaining Judge issue(s) after "
                    f"{max_dispute} dispute rounds"
                ),
                options=[
                    HumanDecisionOption(
                        option_key="approve_merge",
                        decision=MergeDecision.SEMANTIC_MERGE,
                        description=(
                            "Accept the current merged content as-is "
                            "(advisory issues only)."
                        ),
                        preview_content=summary_blob,
                    ),
                    HumanDecisionOption(
                        option_key="take_target",
                        decision=MergeDecision.TAKE_TARGET,
                        description="Replace with the upstream version as-is.",
                        preview_content=summary_blob,
                    ),
                    HumanDecisionOption(
                        option_key="take_current",
                        decision=MergeDecision.TAKE_CURRENT,
                        description="Keep the fork version; drop upstream change.",
                        preview_content=summary_blob,
                    ),
                ],
                created_at=now,
            )

    async def _b_class_sanity_check(
        self,
        state: MergeState,
        ctx: PhaseContext,
    ) -> list[str]:
        """O-B5: compare worktree blob sha vs upstream blob sha for every
        B-class file in the plan. Returns the list of drifted paths.

        B-class means "upstream changed, fork did not" — after auto-merge
        the worktree should byte-equal upstream. Anything else is a bug
        in the take_target / cherry-pick path.
        """
        if state.merge_plan is None:
            return []
        upstream_ref = state.config.upstream_ref
        drift: list[str] = []
        checked = 0
        for batch in state.merge_plan.phases:
            if batch.change_category != FileChangeCategory.B:
                continue
            for fp in batch.file_paths:
                checked += 1
                upstream_sha = ctx.git_tool.get_file_hash(upstream_ref, fp)
                worktree_sha = ctx.git_tool.get_worktree_blob_sha(fp)
                if upstream_sha is None or worktree_sha is None:
                    # File missing on one side; downstream conflict path
                    # already covers this (D-missing / D-extra logic).
                    continue
                if upstream_sha != worktree_sha:
                    drift.append(fp)
        logger.info(
            "O-B5 sanity-check: %d/%d B-class files drift from upstream",
            len(drift),
            checked,
        )
        return drift

    async def _execute_batch(
        self,
        batch: PhaseFileBatch,
        executor: ExecutorAgent,
        file_diffs_map: dict[str, FileDiff],
        replayed_set: set[str],
        state: MergeState,
    ) -> list[str]:
        async def _process_one(file_path: str) -> str | None:
            if file_path in replayed_set:
                return None
            if file_path in state.file_decision_records:
                existing = state.file_decision_records[file_path]
                if existing.decision != MergeDecision.ESCALATE_HUMAN:
                    return file_path
                return None

            category = batch.change_category
            if category is None:
                fd_lookup = file_diffs_map.get(file_path)
                category = fd_lookup.change_category if fd_lookup else None

            if category == FileChangeCategory.D_MISSING:
                record = await executor._copy_from_upstream(file_path, state)
                state.file_decision_records[file_path] = record
                return file_path

            fd_item: FileDiff | None = file_diffs_map.get(file_path)
            if fd_item is None:
                return None

            strategy = executor._select_strategy_by_category(category, batch.risk_level)
            record = await executor.execute_auto_merge(fd_item, strategy, state)
            state.file_decision_records[file_path] = record
            return file_path

        results = await asyncio.gather(
            *[_process_one(fp) for fp in batch.file_paths],
            return_exceptions=True,
        )

        changed_files: list[str] = []
        for fp, result in zip(batch.file_paths, results):
            if isinstance(result, BaseException):
                logger.error("Batch file processing error for %s: %s", fp, result)
            elif result is not None:
                changed_files.append(result)

        return changed_files

    async def _handle_plan_dispute(
        self,
        state: MergeState,
        ctx: PhaseContext,
        dispute: PlanDisputeRequest,
    ) -> None:
        from src.models.plan_judge import PlanJudgeResult

        planner = ctx.agents["planner"]
        planner_judge = ctx.agents["planner_judge"]

        # Let Coordinator decide whether to attempt standard revision or meta-review.
        if ctx.coordinator is not None:
            decision = ctx.coordinator.route_dispute(state, dispute)
            if decision.action == "meta_review":
                await self._run_plan_meta_review(state, ctx, planner, decision.reason)
                return

        try:
            ctx.state_machine.transition(
                state,
                SystemStatus.PLAN_REVISING,
                f"dispute: {dispute.dispute_reason}",
            )
            revised_plan = await planner.handle_dispute(state, dispute)
            state.merge_plan = revised_plan

            file_diffs: list[FileDiff] = state.file_diffs
            ctx.state_machine.transition(
                state, SystemStatus.PLAN_REVIEWING, "dispute revision complete"
            )

            verdict = await planner_judge.review_plan(
                revised_plan, file_diffs, 0, lang=ctx.config.output.language
            )
            state.plan_judge_verdict = verdict

            if verdict.result == PlanJudgeResult.APPROVED:
                dispute.resolved = True
                dispute.resolution_summary = "Plan revised and approved after dispute"
                ctx.state_machine.transition(
                    state,
                    SystemStatus.AUTO_MERGING,
                    "dispute resolved, plan approved",
                )
            else:
                ctx.state_machine.transition(
                    state,
                    SystemStatus.AWAITING_HUMAN,
                    "dispute could not be resolved automatically",
                )
        except Exception as e:
            logger.error("Plan dispute handling failed: %s", e)
            ctx.state_machine.transition(
                state,
                SystemStatus.AWAITING_HUMAN,
                f"dispute handling error: {e}",
            )

    async def _run_plan_meta_review(
        self,
        state: MergeState,
        ctx: PhaseContext,
        planner: object,
        trigger_reason: str,
    ) -> None:
        from src.core.coordinator import Coordinator

        logger.info("Coordinator: running plan meta-review (%s)", trigger_reason)
        try:
            raw = await planner.meta_review(state)  # type: ignore[attr-defined]
            if ctx.coordinator is not None:
                result = Coordinator.build_meta_review_result(
                    phase="auto_merge",
                    trigger="plan_dispute",
                    raw=raw,
                )
                state.coordinator_directives.append(result)
                logger.info(
                    "Plan meta-review: assessment=%r recommendation=%r",
                    result.assessment,
                    result.recommendation,
                )
        except Exception as exc:
            logger.warning("Plan meta-review failed: %s", exc)
        ctx.state_machine.transition(
            state,
            SystemStatus.AWAITING_HUMAN,
            f"plan dispute escalated to meta-review: {trigger_reason}",
        )
