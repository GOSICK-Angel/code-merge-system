from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Literal

from src.agents.base_agent import BaseAgent
from src.models.config import AgentLLMConfig
from src.models.message import AgentType, AgentMessage, MessageType
from src.models.plan import MergePhase
from src.models.diff import FileDiff, FileChangeCategory, RiskLevel, FileStatus
from src.models.conflict import ConflictAnalysis
from src.models.decision import MergeDecision, FileDecisionRecord, DecisionSource
from src.models.judge import (
    RepairInstruction,
    JudgeIssue,
    ExecutorRebuttal,
    DisputePoint,
)
from src.models.human import HumanDecisionRequest
from src.models.dispute import PlanDisputeRequest
from src.models.plan_review import UserDecisionItem, DecisionOption
from src.models.state import MergeState
from src.llm.prompts.executor_prompts import (
    EXECUTOR_SYSTEM,
    build_semantic_merge_prompt,
    build_deletion_analysis_prompt,
    build_rebuttal_prompt,
)
from src.llm.client import ParseError
from src.llm.response_parser import parse_merge_result
from src.tools.patch_applier import apply_with_snapshot, create_escalate_record
from src.tools.file_classifier import _fork_deleted_skip_record, is_fork_deleted
from src.tools.git_tool import GitReadStatus, GitTool
from src.tools.import_symbol_harvester import harvest_imports_for_file
from src.tools.diff_stasher import stash_upstream_diff
from src.cli.paths import get_diff_stash_dir
from src.core.parallel_file_runner import (
    ParallelFileRunner,
    assert_disjoint_file_shards,
)

logger = logging.getLogger(__name__)

# Cap on how many JudgeIssues feed one rebuttal LLM call. Each issue
# contributes ~250-400 chars of input AND must produce a JSON decision
# entry in the output (~50 chars). Calibrated against forgejo where a
# 222-issue single-shot rebuttal serialised to 54KB input and pushed
# the model past the long-request timeout (272s). Past ~30 the output
# also bumps the 8K max_tokens ceiling. 25 keeps both well under the
# danger zone with comfortable headroom for verbose judge descriptions.
_REBUTTAL_CHUNK_SIZE = 25


def _safe_harvest_symbols(
    file_path: str,
    source_content: str | None,
    ref: str | None,
    git_tool: GitTool | None,
) -> dict[str, list[str]]:
    """Best-effort view of which symbols each namespace import exposes, fed to
    the semantic-merge prompt so the executor can ground references instead of
    inventing them. Any failure degrades to ``{}`` — grounding context must
    never make a merge fail."""
    if not source_content or not ref or git_tool is None:
        return {}
    try:
        return harvest_imports_for_file(file_path, source_content, ref, git_tool)
    except Exception:
        return {}


class ExecutorAgent(BaseAgent):
    agent_type = AgentType.EXECUTOR
    contract_name = "executor"

    def __init__(self, llm_config: AgentLLMConfig, git_tool: GitTool | None = None):
        super().__init__(llm_config)
        self.git_tool = git_tool
        # P4: most recent merge-generation LLMResponse.stop_reason — fed
        # forward to ``build_rebuttal_prompt`` so the dispute round can
        # tell the LLM "your last output was truncated at max_tokens"
        # instead of mechanically asking it to regenerate identical
        # garbage. Concurrent per-file merges can race on this (the
        # last writer wins) but the failure mode is graceful: rebuttal
        # gets a slightly stale signal, never wrong information that
        # could cause a regression.
        self._last_merge_stop_reason: str | None = None
        self._last_merge_had_prose_preamble: bool = False

    async def run(self, state: MergeState) -> AgentMessage:
        view = self.restricted_view(state)
        if view.merge_plan is None:
            return AgentMessage(
                sender=AgentType.EXECUTOR,
                receiver=AgentType.ORCHESTRATOR,
                phase=MergePhase.AUTO_MERGE,
                message_type=MessageType.ERROR,
                subject="No merge plan available",
                payload={},
            )

        processed = 0
        disputes: list[str] = []

        file_diffs_map: dict[str, FileDiff] = {}
        for fd in view.file_diffs:
            file_diffs_map[fd.file_path] = fd

        from src.tools.sentinel_scanner import SentinelScanner

        sentinel_scanner = SentinelScanner.from_config_extras(
            list(getattr(view.config, "sentinels_extra", None) or [])
        )

        for batch in view.merge_plan.phases:
            if batch.risk_level not in (RiskLevel.AUTO_SAFE, RiskLevel.AUTO_RISKY):
                continue

            for file_path in batch.file_paths:
                fd = file_diffs_map.get(file_path)
                category = batch.change_category or (fd.change_category if fd else None)
                strategy = self._select_strategy_by_category(category, batch.risk_level)

                if strategy == MergeDecision.SKIP:
                    if fd is not None:
                        record = await self.execute_auto_merge(fd, strategy, state)
                        state.file_decision_records[file_path] = record
                        processed += 1
                    continue

                if (
                    batch.risk_level == RiskLevel.AUTO_SAFE
                    and self.git_tool is not None
                ):
                    fork_content = self.git_tool.get_file_content(
                        view.config.fork_ref, file_path
                    )
                    if fork_content:
                        hits = sentinel_scanner.scan(fork_content, file_path)
                        hits = hits + sentinel_scanner.check_fork_delta(fd)
                        if hits:
                            state.sentinel_hits[file_path] = hits
                            if fd is None:
                                from src.models.diff import FileDiff, FileStatus

                                fd = FileDiff(
                                    file_path=file_path,
                                    file_status=FileStatus.MODIFIED,
                                    risk_level=RiskLevel.AUTO_SAFE,
                                    risk_score=0.5,
                                )
                            self.raise_plan_dispute(
                                fd,
                                reason=(
                                    f"Sentinel marker(s) found in AUTO_SAFE fork "
                                    f"file '{file_path}': "
                                    + "; ".join(
                                        f"line {h.line_number}: {h.matched_text[:60]}"
                                        for h in hits[:3]
                                    )
                                ),
                                suggested={file_path: RiskLevel.HUMAN_REQUIRED},
                                impact=(
                                    "File contains fork-customization markers. "
                                    "Overwriting with upstream may silently drop "
                                    "fork-only logic."
                                ),
                                state=state,
                            )
                            disputes.append(file_path)
                            continue

                if category == FileChangeCategory.D_MISSING:
                    if is_fork_deleted(state, file_path):
                        state.file_decision_records[file_path] = (
                            _fork_deleted_skip_record(file_path)
                        )
                    else:
                        record = await self._copy_from_upstream(file_path, state)
                        state.file_decision_records[file_path] = record
                    processed += 1
                    continue

                if fd is None:
                    continue

                record = await self.execute_auto_merge(fd, strategy, state)
                state.file_decision_records[file_path] = record
                processed += 1

        return AgentMessage(
            sender=AgentType.EXECUTOR,
            receiver=AgentType.ORCHESTRATOR,
            phase=MergePhase.AUTO_MERGE,
            message_type=MessageType.PHASE_COMPLETED,
            subject=f"Processed {processed} auto-merge files",
            payload={"processed": processed, "disputes": disputes},
        )

    def _select_strategy_by_category(
        self,
        category: FileChangeCategory | None,
        risk_level: RiskLevel,
    ) -> MergeDecision:
        if category == FileChangeCategory.B:
            return MergeDecision.TAKE_TARGET
        if category == FileChangeCategory.D_MISSING:
            return MergeDecision.TAKE_TARGET
        if category == FileChangeCategory.A or category == FileChangeCategory.E:
            return MergeDecision.SKIP
        if category == FileChangeCategory.D_EXTRA:
            return MergeDecision.SKIP
        if category == FileChangeCategory.C:
            if risk_level == RiskLevel.HUMAN_REQUIRED:
                return MergeDecision.ESCALATE_HUMAN
            return MergeDecision.SEMANTIC_MERGE
        return MergeDecision.TAKE_TARGET

    async def _copy_from_upstream(
        self, file_path: str, state: MergeState
    ) -> FileDecisionRecord:
        if self.git_tool is None:
            return create_escalate_record(
                file_path, "No git tool available", phase="auto_merge", agent="executor"
            )

        content = self.git_tool.get_file_content(state.config.upstream_ref, file_path)
        if content is None:
            return create_escalate_record(
                file_path,
                "Could not fetch upstream content for D-missing file",
                phase="auto_merge",
                agent="executor",
            )

        current_phase_str = (
            state.current_phase.value
            if hasattr(state.current_phase, "value")
            else str(state.current_phase)
        )
        return await apply_with_snapshot(
            file_path,
            content,
            self.git_tool,
            state,
            phase=current_phase_str,
            agent="executor",
            decision=MergeDecision.TAKE_TARGET,
            rationale="D-missing: copying new file from upstream",
        )

    async def execute_auto_merge(
        self,
        file_diff: FileDiff,
        strategy: MergeDecision,
        state: MergeState,
    ) -> FileDecisionRecord:
        if self.git_tool is None:
            return create_escalate_record(
                file_diff.file_path,
                "No git tool available",
                phase="auto_merge",
                agent="executor",
            )

        current_phase_str = (
            state.current_phase.value
            if hasattr(state.current_phase, "value")
            else str(state.current_phase)
        )

        if strategy == MergeDecision.TAKE_TARGET:
            content = self.git_tool.get_file_content(
                state.config.upstream_ref, file_diff.file_path
            )
            if content is None:
                return create_escalate_record(
                    file_diff.file_path,
                    "Could not fetch target content",
                    phase=current_phase_str,
                )
            return await apply_with_snapshot(
                file_diff.file_path,
                content,
                self.git_tool,
                state,
                phase=current_phase_str,
                agent="executor",
                decision=strategy,
                rationale="Taking target (upstream) version",
            )

        elif strategy == MergeDecision.TAKE_CURRENT:
            content = self.git_tool.get_file_content(
                state.config.fork_ref, file_diff.file_path
            )
            if content is None:
                return create_escalate_record(
                    file_diff.file_path,
                    "Could not fetch current content",
                    phase=current_phase_str,
                )
            return await apply_with_snapshot(
                file_diff.file_path,
                content,
                self.git_tool,
                state,
                phase=current_phase_str,
                agent="executor",
                decision=strategy,
                rationale="Taking current (fork) version",
            )

        elif strategy == MergeDecision.SKIP:
            return FileDecisionRecord(
                file_path=file_diff.file_path,
                file_status=file_diff.file_status,
                decision=MergeDecision.SKIP,
                decision_source=DecisionSource.AUTO_PLANNER,
                rationale="File skipped per plan",
                phase=current_phase_str,
                agent="executor",
                timestamp=datetime.now(),
            )

        if strategy == MergeDecision.SEMANTIC_MERGE:
            # Defensive guard. SEMANTIC_MERGE must be routed through
            # ConflictAnalysisPhase, which produces the ConflictAnalysis
            # required by execute_semantic_merge(). Reaching this branch
            # means the auto_merge dispatcher failed to defer the file.
            return create_escalate_record(
                file_diff.file_path,
                "SEMANTIC_MERGE reached execute_auto_merge without a "
                "ConflictAnalysis — the auto_merge dispatcher should defer "
                "this strategy to conflict_analysis (see auto_merge.py).",
                phase=current_phase_str,
            )

        return create_escalate_record(
            file_diff.file_path,
            f"Unsupported auto-merge strategy: {strategy}",
            phase=current_phase_str,
        )

    def _stash_upstream_diff_for_escalation(
        self, file_path: str, state: MergeState
    ) -> str | None:
        """P2-3 (§6.2 item 2): write the upstream-side delta to a patch
        file so the human reviewer has the missing piece in hand when
        an LLM semantic_merge falls back to escalate_human.

        Returns a short note suitable for appending to the escalation
        rationale, or ``None`` if the diff could not be produced (no
        git_tool, missing merge_base, empty diff, etc.).
        """
        if self.git_tool is None:
            return None
        merge_base = state.merge_base_commit or ""
        upstream_ref = state.config.upstream_ref
        if not merge_base or not upstream_ref:
            return None
        stash_dir = get_diff_stash_dir(state.config.repo_path, state.run_id)
        try:
            patch_path = stash_upstream_diff(
                file_path,
                merge_base,
                upstream_ref,
                self.git_tool,
                stash_dir,
            )
        except Exception as exc:
            logger.warning(
                "executor: stash_upstream_diff failed for %s: %s", file_path, exc
            )
            return None
        if patch_path is None:
            return None
        return (
            f"upstream delta stashed at {patch_path} "
            f"(take_current_with_diff_note: fork blob preserved; "
            f"apply this patch to integrate upstream changes manually)"
        )

    async def execute_semantic_merge(
        self,
        file_diff: FileDiff,
        conflict_analysis: ConflictAnalysis,
        state: MergeState,
    ) -> FileDecisionRecord:
        if self.git_tool is None:
            return create_escalate_record(
                file_diff.file_path,
                "No git tool available",
            )

        current_content = self.git_tool.get_file_content(
            state.config.fork_ref, file_diff.file_path
        )
        target_content = self.git_tool.get_file_content(
            state.config.upstream_ref, file_diff.file_path
        )

        if current_content is None or target_content is None:
            return create_escalate_record(
                file_diff.file_path,
                "Could not fetch file contents for semantic merge",
            )

        # Keep the untruncated originals — build_staged_content below rebinds
        # current_content/target_content to budget-trimmed views, but the
        # fidelity guard must compare the merge against the FULL sources.
        orig_current_content = current_content
        orig_target_content = target_content

        chunk_size = self._effective_chunk_size(state)
        if max(len(current_content), len(target_content)) > chunk_size:
            logger.info(
                "Large file (%d chars): routing %s to chunked semantic merge",
                max(len(current_content), len(target_content)),
                file_diff.file_path,
            )
            return await self._execute_chunked_semantic_merge(
                file_diff,
                conflict_analysis,
                current_content,
                target_content,
                state,
            )

        # U1.A: build_staged_content runs regardless of memory_store
        # availability. Only the memory-text injection remains gated.
        from src.llm.prompt_builders import AgentPromptBuilder
        from src.llm.relevance import weights_from_fanin

        builder = AgentPromptBuilder(
            self.llm_config, self._memory_store, self._memory_hit_tracker
        )
        enriched_context = state.config.project_context
        if self._memory_store is not None:
            memory_text = builder.build_memory_context_text(
                [file_diff.file_path], current_phase=self._current_phase
            )
            if memory_text:
                enriched_context = (
                    f"{enriched_context}\n\n{memory_text}"
                    if enriched_context
                    else memory_text
                )

        diff_ranges = _extract_diff_ranges(file_diff)
        target_ranges = _extract_diff_ranges(file_diff, side="target")
        referenced = state.dependency_graph.referenced_symbols(file_diff.file_path)
        # OPP-10: degree-weight the referenced symbols by fan-in so a high
        # fan-in public interface stays FULL under staged compression. Empty
        # graph -> empty dict -> flat reference boost (safe degrade).
        symbol_weights = weights_from_fanin(
            state.dependency_graph.symbol_fanin(file_diff.file_path)
        )
        content_budget = builder.compute_content_budget(
            EXECUTOR_SYSTEM + enriched_context
        )
        budget_tokens = content_budget // 4
        current_content = builder.build_staged_content(
            current_content,
            file_diff.file_path,
            diff_ranges,
            budget_tokens // 2,
            is_security_sensitive=file_diff.is_security_sensitive,
            referenced_names=referenced,
            symbol_weights=symbol_weights,
        )
        target_content = builder.build_staged_content(
            target_content,
            file_diff.file_path,
            target_ranges,
            budget_tokens // 2,
            is_security_sensitive=file_diff.is_security_sensitive,
            referenced_names=referenced,
            symbol_weights=symbol_weights,
        )

        # Phase B step 8: warn the merge to preserve the public interface of a
        # file other files import. Empty graph -> empty list -> no prompt
        # change (safe degrade).
        dependents = state.dependency_graph.dependents_of(file_diff.file_path)
        # P0-1: hand the executor the symbols each namespace import actually
        # exposes (harvested from the FULL fork content, not the staged view)
        # so it grounds references instead of fabricating them.
        imported_symbols = _safe_harvest_symbols(
            file_diff.file_path,
            orig_current_content,
            state.config.fork_ref,
            self.git_tool,
        )
        prompt = build_semantic_merge_prompt(
            file_diff,
            conflict_analysis,
            current_content,
            target_content,
            enriched_context,
            dependents=dependents,
            referenced_symbols=referenced,
            imported_symbols=imported_symbols,
        )
        messages = [{"role": "user", "content": prompt}]

        try:
            raw = await self._call_llm_with_retry_meta(messages, system=EXECUTOR_SYSTEM)
            self._last_merge_stop_reason = raw.stop_reason
            self._last_merge_had_prose_preamble = False
            merged_content = parse_merge_result(
                raw,
                # B2 fix: the truncation/length-floor guard must measure the
                # merge against the FULL file sizes, not the budget-trimmed
                # staged views (current_content/target_content were rebound to
                # build_staged_content output above). Using the trimmed sizes
                # let a heavily-elided merge pass gate-4 silently.
                current_size=len(orig_current_content),
                target_size=len(orig_target_content),
            )
        except Exception as e:
            if isinstance(e, ParseError) and "preamble" in str(e).lower():
                self._last_merge_had_prose_preamble = True
            logger.warning("Semantic merge failed for %s: %s", file_diff.file_path, e)
            stash_note = self._stash_upstream_diff_for_escalation(
                file_diff.file_path, state
            )
            reason = f"SEMANTIC_MERGE_FAILED: {e}"
            if stash_note:
                reason = f"{reason} — {stash_note}"
            return create_escalate_record(
                file_diff.file_path,
                reason,
            )

        # Mirror the chunked path's deduplication so a seam/regeneration that
        # duplicates a top-level declaration is cleaned before guards run.
        from src.tools.duplicate_symbol_check import (
            remove_duplicate_top_level_symbols,
        )

        deduped = remove_duplicate_top_level_symbols(
            merged_content, file_diff.file_path
        )
        if deduped != merged_content:
            logger.info(
                "Semantic merge for %s: removed duplicate top-level declaration(s)",
                file_diff.file_path,
            )
            merged_content = deduped

        fidelity_reason = self._single_shot_fidelity_issue(
            file_diff.file_path,
            merged_content,
            orig_current_content,
            orig_target_content,
            state,
        )
        if fidelity_reason is not None:
            logger.warning(
                "Semantic merge for %s failed fidelity guard: %s",
                file_diff.file_path,
                fidelity_reason,
            )
            return create_escalate_record(file_diff.file_path, fidelity_reason)

        current_phase_str = (
            state.current_phase.value
            if hasattr(state.current_phase, "value")
            else str(state.current_phase)
        )
        return await apply_with_snapshot(
            file_diff.file_path,
            merged_content,
            self.git_tool,
            state,
            phase=current_phase_str,
            agent="executor",
            decision=MergeDecision.SEMANTIC_MERGE,
            rationale=conflict_analysis.rationale,
            confidence=conflict_analysis.confidence,
        )

    def _effective_chunk_size(self, state: MergeState) -> int:
        """#9D: chunk size coupled to the executor's output budget.

        A chunked merge emits, per chunk pair, up to ``cur_chunk + tgt_chunk``
        chars (~``2 * chunk_size``). If that exceeds the model's ``max_tokens``
        output ceiling the response truncates — caught by parse_merge_result
        gate-1 (good: no corruption) but the file then escalates instead of
        merging. Observed on zod ``core/schemas.ts`` (148 KB) with the default
        ``max_tokens=8192``: every chunk hit the cap.

        Cap the split size so a chunk pair's merged output fits under
        ``max_tokens`` with headroom: output_tokens ≈ 2*chunk/3.5; keep it under
        0.8*max_tokens → chunk < 1.4*max_tokens chars. Never EXCEED the
        configured ``chunk_size_chars`` (operators may want smaller). A larger
        ``max_tokens`` (e.g. 32k) lets the configured value stand.
        """
        configured = state.config.chunk_size_chars
        max_tokens = getattr(self.llm_config, "max_tokens", 8192) or 8192
        output_safe = int(max_tokens * 1.4)
        return max(2000, min(configured, output_safe))

    def _single_shot_fidelity_issue(
        self,
        file_path: str,
        merged_content: str,
        orig_current_content: str,
        orig_target_content: str,
        state: MergeState,
    ) -> str | None:
        """Post-merge fidelity guards for the single-shot (whole-file) path.

        Historically this path ran only ``_foreign_chars`` (non-ASCII only),
        so a fabricated cross-module symbol (the ``core._isoWeek`` class) or a
        silently dropped fork export committed clean as a high-confidence
        SEMANTIC_MERGE. Mirror the chunked path's invented-symbol guard and add
        a deterministic additive-fork-export preservation check. All inputs are
        the UNTRIMMED originals so staged compression cannot cause a false
        escalation. Returns an escalation reason, or ``None`` when clean.
        """
        # 1) Non-ASCII opaque-blob corruption (existing guard).
        foreign = _foreign_chars(
            merged_content, orig_current_content, orig_target_content
        )
        if foreign is not None:
            return (
                f"SEMANTIC_MERGE_INFIDELITY: merge output introduced "
                f"character(s) {foreign!r} present in neither fork nor "
                f"upstream — likely LLM corruption of an opaque blob."
            )

        # 2) Hallucinated cross-module member access (was chunked-path only).
        from src.tools.hallucinated_symbol_guard import (
            find_invented_member_accesses,
        )

        invented = find_invented_member_accesses(
            merged_content, [orig_current_content, orig_target_content], file_path
        )
        if invented:
            return (
                f"SEMANTIC_MERGE_INFIDELITY: merge introduced cross-module "
                f"reference(s) {invented} present in neither fork nor upstream "
                f"— likely a hallucinated symbol. Escalating for human review."
            )

        # 3) Additive fork-export preservation: a public top-level symbol the
        # fork ADDED over the merge base must survive the merge. Needs the
        # merge-base blob, which this path does not otherwise fetch. Best-effort
        # — any git failure degrades to "no check" rather than blocking.
        if self.git_tool is not None and state.merge_base_commit:
            base_content: str | None
            read_status: GitReadStatus
            try:
                base_content, read_status = self.git_tool.get_file_content_checked(
                    state.merge_base_commit, file_path
                )
            except Exception as exc:  # defensive: a non-git escape must not be silent
                base_content, read_status = None, GitReadStatus.GIT_ERROR
                logger.debug("fork-export merge-base read raised: %r", exc)
            if read_status == GitReadStatus.GIT_ERROR:
                # W1: a genuine git error — distinct from a legitimately-absent
                # base blob (ABSENT = fork added the file = nothing to preserve) —
                # silently disabled the fork-export preservation check. Record it
                # so the run reports partial_failure instead of a clean COMPLETED.
                from src.tools.gate_skip import gate_skip_entry

                state.errors.append(
                    gate_skip_entry(
                        "fork_export_preservation",
                        file_path,
                        "merge-base read failed",
                    )
                )
            if base_content is not None:
                from src.tools.feature_preservation import (
                    added_exported_symbols,
                    missing_symbols,
                )

                expected = added_exported_symbols(
                    base_content, orig_current_content, file_path
                )
                dropped = missing_symbols(merged_content, expected, file_path)
                if dropped:
                    return (
                        f"SEMANTIC_MERGE_INFIDELITY: merge dropped fork-added "
                        f"public symbol(s) {sorted(dropped)} that the fork "
                        f"introduced over the merge base — silent fork-feature "
                        f"loss. Escalating for human review."
                    )
        return None

    async def _execute_chunked_semantic_merge(
        self,
        file_diff: FileDiff,
        conflict_analysis: ConflictAnalysis,
        current_content: str,
        target_content: str,
        state: MergeState,
    ) -> FileDecisionRecord:
        from src.tools.chunk_processor import (
            align_chunks,
            merge_chunks,
            seam_balanced,
            split_with_forced_flag,
        )
        from src.tools.duplicate_symbol_check import (
            find_duplicate_function_impls,
            remove_duplicate_top_level_symbols,
        )
        from src.tools.hallucinated_symbol_guard import (
            find_invented_member_accesses,
        )

        file_path = file_diff.file_path
        chunk_size = self._effective_chunk_size(state)

        current_chunks, cur_forced = split_with_forced_flag(
            current_content, file_path, chunk_size
        )
        target_chunks, tgt_forced = split_with_forced_flag(
            target_content, file_path, chunk_size
        )
        # #10: a forced mid-body split cuts one semantic unit into brace-
        # incomplete halves that cannot be merged slice-by-slice. Escalate
        # rather than feed the LLM an unmergeable fragment.
        if cur_forced or tgt_forced:
            logger.warning(
                "Chunked merge for %s: an oversized unit (> 2x chunk_size=%d) "
                "forced a mid-body split — escalating instead of merging "
                "brace-incomplete halves.",
                file_path,
                chunk_size,
            )
            return create_escalate_record(
                file_path,
                f"CHUNKED_MERGE_FORCED_SPLIT: a single semantic unit exceeds "
                f"2x chunk_size ({chunk_size} chars) with no safe split point, "
                f"so chunking would cut it mid-body into unmergeable halves. "
                f"Raise the executor max_tokens / chunk_size_chars, or resolve "
                f"this file manually.",
            )
        pairs = align_chunks(current_chunks, target_chunks)

        logger.info(
            "Chunked merge %s: %d current chunks, %d target chunks → %d pairs",
            file_path,
            len(current_chunks),
            len(target_chunks),
            len(pairs),
        )

        memory_text = self.get_memory_context(self._current_phase, [file_path])
        merged_chunks: list[str] = []
        for idx, (curr_chunk, tgt_chunk) in enumerate(pairs):
            # #10: a fork-only region with no upstream counterpart (empty target
            # chunk). Pass the fork content through verbatim — sending it to the
            # LLM with an empty "upstream" side invites a needless rewrite /
            # hallucination of fork code that has nothing to merge against.
            if not tgt_chunk.strip():
                merged_chunks.append(curr_chunk)
                continue
            prompt = _build_chunk_merge_prompt(
                file_path,
                curr_chunk,
                tgt_chunk,
                idx + 1,
                len(pairs),
                state.config.project_context,
                conflict_analysis.rationale,
            )
            if memory_text:
                prompt = f"{prompt}\n\n# Prior Knowledge\n{memory_text}"
            try:
                raw = await self._call_llm_with_retry_meta(
                    [{"role": "user", "content": prompt}],
                    system=EXECUTOR_SYSTEM,
                )
                merged_chunks.append(
                    parse_merge_result(
                        raw,
                        current_size=len(curr_chunk),
                        target_size=len(tgt_chunk),
                    )
                )
            except Exception as e:
                logger.warning(
                    "Chunk %d/%d merge failed for %s: %s — escalating",
                    idx + 1,
                    len(pairs),
                    file_path,
                    e,
                )
                stash_note = self._stash_upstream_diff_for_escalation(file_path, state)
                reason = f"CHUNKED_MERGE_FAILED (chunk {idx + 1}/{len(pairs)}): {e}"
                if stash_note:
                    reason = f"{reason} — {stash_note}"
                return create_escalate_record(
                    file_path,
                    reason,
                )

        merged_content = merge_chunks(merged_chunks)
        deduped = remove_duplicate_top_level_symbols(merged_content, file_path)
        if deduped != merged_content:
            logger.info(
                "Chunked merge for %s: removed duplicate top-level "
                "declaration(s) at chunk seam",
                file_path,
            )
            merged_content = deduped
        # #10: a chunk seam can re-emit a JS/TS function implementation, a
        # TS2451 redeclaration the const/class dedup above cannot remove safely
        # (deleting a span risks dropping a real overload). Escalate instead.
        dup_fns = find_duplicate_function_impls(merged_content, file_path)
        if dup_fns:
            logger.warning(
                "Chunked merge for %s redeclares function implementation(s) %s "
                "at a chunk seam — escalating instead of committing.",
                file_path,
                dup_fns,
            )
            return create_escalate_record(
                file_path,
                f"CHUNKED_MERGE_DUP_FUNCTION: function implementation(s) "
                f"{dup_fns} declared more than once after reassembly (TS2451 "
                f"redeclaration) — likely a chunk mispairing. Escalating.",
            )
        # #10: structural seam gate. A mispaired or partially-merged chunk can
        # produce a brace/paren/bracket-imbalanced (or unterminated-string)
        # reassembly even when each chunk parsed in isolation. Escalate rather
        # than commit uncompilable output — defense-in-depth behind alignment.
        if not seam_balanced(merged_content, file_path):
            logger.warning(
                "Chunked merge for %s is brace-imbalanced after reassembly "
                "(chunk seam corruption) — escalating instead of committing.",
                file_path,
            )
            return create_escalate_record(
                file_path,
                "CHUNKED_MERGE_SEAM_IMBALANCE: reassembled chunked merge is "
                "structurally unbalanced (brackets / strings) — a likely chunk "
                "mispairing or partial merge. Escalating for human resolution.",
            )
        foreign = _foreign_chars(merged_content, current_content, target_content)
        if foreign is not None:
            logger.warning(
                "Chunked merge for %s invented characters absent from both "
                "sources (%r) — escalating instead of committing corruption",
                file_path,
                foreign,
            )
            return create_escalate_record(
                file_path,
                f"SEMANTIC_MERGE_INFIDELITY: chunked merge output introduced "
                f"character(s) {foreign!r} present in neither fork nor "
                f"upstream — likely LLM corruption of an opaque blob.",
            )
        invented_refs = find_invented_member_accesses(
            merged_content, [current_content, target_content], file_path
        )
        if invented_refs:
            logger.warning(
                "Chunked merge for %s references symbol(s) %s absent from both "
                "sources — likely hallucinated; escalating",
                file_path,
                invented_refs,
            )
            return create_escalate_record(
                file_path,
                f"SEMANTIC_MERGE_INFIDELITY: chunked merge introduced "
                f"cross-module reference(s) {invented_refs} present in neither "
                f"fork nor upstream — likely a hallucinated symbol (the analyst "
                f"may have pre-warned a new dependency is required). Escalating "
                f"for human resolution.",
            )
        current_phase_str = (
            state.current_phase.value
            if hasattr(state.current_phase, "value")
            else str(state.current_phase)
        )
        return await apply_with_snapshot(
            file_path,
            merged_content,
            self.git_tool,  # type: ignore[arg-type]  # checked by caller
            state,
            phase=current_phase_str,
            agent="executor",
            decision=MergeDecision.SEMANTIC_MERGE,
            rationale=f"chunked_merge ({len(pairs)} chunks): {conflict_analysis.rationale}",
            confidence=conflict_analysis.confidence,
        )

    async def execute_human_decision(
        self,
        request: HumanDecisionRequest,
        state: MergeState,
    ) -> FileDecisionRecord:
        if request.human_decision is None:
            return create_escalate_record(
                request.file_path,
                "Human decision not provided",
            )

        current_phase_str = (
            state.current_phase.value
            if hasattr(state.current_phase, "value")
            else str(state.current_phase)
        )

        if request.human_decision == MergeDecision.MANUAL_PATCH:
            if not request.custom_content:
                return create_escalate_record(
                    request.file_path,
                    "MANUAL_PATCH selected but no custom content provided",
                )
            if self.git_tool is None:
                return create_escalate_record(request.file_path, "No git tool")
            return await apply_with_snapshot(
                request.file_path,
                request.custom_content,
                self.git_tool,
                state,
                phase=current_phase_str,
                agent="executor",
                decision=MergeDecision.MANUAL_PATCH,
                rationale=request.reviewer_notes or "Manual patch applied",
            )

        fd_map: dict[str, FileDiff] = {}
        for fd in state.file_diffs:
            fd_map[fd.file_path] = fd

        fd = fd_map.get(request.file_path)  # type: ignore[assignment]
        if fd is None:
            fd = FileDiff(
                file_path=request.file_path,
                file_status=FileStatus.MODIFIED,
                risk_level=RiskLevel.HUMAN_REQUIRED,
                risk_score=0.9,
            )

        # SEMANTIC_MERGE cannot go through execute_auto_merge — that path
        # has a defensive guard that escalates SEMANTIC_MERGE back to human
        # (it needs a ConflictAnalysis it does not have). When the reviewer
        # explicitly picks semantic_merge, route to execute_semantic_merge
        # with the analysis ConflictAnalysisPhase already produced; otherwise
        # the human's choice silently degrades to escalate_human and the file
        # is left at fork baseline with upstream changes dropped.
        if request.human_decision == MergeDecision.SEMANTIC_MERGE:
            analysis = (state.conflict_analyses or {}).get(request.file_path)
            if analysis is None:
                return create_escalate_record(
                    request.file_path,
                    "Reviewer chose semantic_merge but no ConflictAnalysis is "
                    "available for this file — cannot perform the merge.",
                    phase=current_phase_str,
                )
            record = await self.execute_semantic_merge(fd, analysis, state)
        else:
            record = await self.execute_auto_merge(fd, request.human_decision, state)
        return FileDecisionRecord(
            record_id=record.record_id,
            file_path=record.file_path,
            file_status=record.file_status,
            decision=record.decision,
            decision_source=DecisionSource.HUMAN,
            confidence=record.confidence,
            rationale=request.reviewer_notes or record.rationale,
            original_snapshot=record.original_snapshot,
            merged_content_preview=record.merged_content_preview,
            human_notes=request.reviewer_notes,
            phase=record.phase,
            agent=record.agent,
            timestamp=datetime.now(),
        )

    async def repair(
        self,
        instructions: list[RepairInstruction],
        state: MergeState,
    ) -> list[FileDecisionRecord]:
        if self.git_tool is None:
            return []

        results: list[FileDecisionRecord] = []
        current_phase_str = (
            state.current_phase.value
            if hasattr(state.current_phase, "value")
            else str(state.current_phase)
        )

        # Budget: repair prompts embed current + upstream content. Without a
        # cap a single 300k-char lock file blows past the context window
        # (we observed 93k tokens = 46% of 200k). Skip files that would
        # force an unsafe "fill in the blanks" trim — lock files and
        # generated artifacts should be regenerated by their toolchain,
        # not patched by an LLM. Limit is configurable via
        # ``AgentLLMConfig.repair_max_file_chars`` (O-P1).
        _MAX_CONTENT_CHARS_PER_SIDE = getattr(
            self.llm_config, "repair_max_file_chars", 30_000
        )
        _NON_LLM_REPAIR_SUFFIXES = (
            ".lock",
            "uv.lock",
            "package-lock.json",
            "pnpm-lock.yaml",
            "poetry.lock",
            "Cargo.lock",
            "yarn.lock",
            "Gemfile.lock",
        )

        for instr in instructions:
            if not instr.is_repairable:
                continue

            # Never overwrite an operator's explicit decision. When the Judge
            # flags a file the human already resolved (take_target / take_current
            # / manual_patch), the dispute-round repair must NOT silently
            # re-merge it back to SEMANTIC_MERGE — that drops the human override
            # exactly like the human_review Bug B. The issue instead stays
            # unrepaired, the verdict stays FAIL, and the run escalates to the
            # Judge gate so the operator can re-decide or RERUN.
            existing = state.file_decision_records.get(instr.file_path)
            if (
                existing is not None
                and existing.decision_source == DecisionSource.HUMAN
            ):
                self.logger.info(
                    "Skipping dispute-round repair for %s — operator decided it "
                    "(%s); human decision must not be overwritten",
                    instr.file_path,
                    existing.decision.value,
                )
                continue

            if instr.file_path.endswith(_NON_LLM_REPAIR_SUFFIXES):
                self.logger.info(
                    "Skipping LLM repair for generated/lock file %s "
                    "(regenerate via toolchain instead)",
                    instr.file_path,
                )
                continue

            current_content = self.git_tool.get_file_content(
                state.config.fork_ref, instr.file_path
            )
            target_content = self.git_tool.get_file_content(
                state.config.upstream_ref, instr.file_path
            )

            if current_content is None and target_content is None:
                continue

            cur_len = len(current_content or "")
            tgt_len = len(target_content or "")
            if (
                cur_len > _MAX_CONTENT_CHARS_PER_SIDE
                or tgt_len > _MAX_CONTENT_CHARS_PER_SIDE
            ):
                self.logger.warning(
                    "Skipping LLM repair for oversized file %s "
                    "(current=%d chars, upstream=%d chars, limit=%d). "
                    "Escalating: file needs manual attention.",
                    instr.file_path,
                    cur_len,
                    tgt_len,
                    _MAX_CONTENT_CHARS_PER_SIDE,
                )
                continue

            prompt = (
                f"Repair the file '{instr.file_path}' based on this instruction:\n"
                f"{instr.instruction}\n\n"
                f"Current content:\n```\n{current_content or '(file does not exist)'}\n```\n\n"
                f"Upstream content:\n```\n{target_content or '(file does not exist)'}\n```\n\n"
                "Output ONLY the repaired file content, no explanation."
            )
            memory_text = self.get_memory_context(
                self._current_phase, [instr.file_path]
            )
            if memory_text:
                prompt = f"{prompt}\n\n# Prior Knowledge\n{memory_text}"
            messages = [{"role": "user", "content": prompt}]

            try:
                raw = await self._call_llm_with_retry_meta(
                    messages, system=EXECUTOR_SYSTEM
                )
                repaired = parse_merge_result(
                    raw,
                    current_size=len(current_content) if current_content else None,
                    target_size=len(target_content) if target_content else None,
                )
            except Exception as exc:
                self.logger.warning("Repair failed for %s: %s", instr.file_path, exc)
                continue

            record = await apply_with_snapshot(
                instr.file_path,
                repaired,
                self.git_tool,
                state,
                phase=current_phase_str,
                agent="executor",
                decision=MergeDecision.SEMANTIC_MERGE,
                rationale=f"Repair: {instr.instruction}",
            )
            results.append(record)
            state.file_decision_records[instr.file_path] = record

        return results

    def raise_plan_dispute(
        self,
        file_diff: FileDiff,
        reason: str,
        suggested: dict[str, RiskLevel],
        impact: str,
        state: MergeState,
    ) -> PlanDisputeRequest:
        current_phase_str = (
            state.current_phase.value
            if hasattr(state.current_phase, "value")
            else str(state.current_phase)
        )

        dispute = PlanDisputeRequest(
            raised_by=AgentType.EXECUTOR.value,
            phase=current_phase_str,
            disputed_files=list(suggested.keys()),
            dispute_reason=reason,
            suggested_reclassification=suggested,
            impact_assessment=impact,
        )

        state.plan_disputes.append(dispute)
        return dispute

    async def analyze_deletion(
        self,
        file_path: str,
        file_diff: FileDiff,
        state: MergeState,
    ) -> UserDecisionItem:
        # Phase B step 8: surface downstream dependents (AST-precise) and
        # sentinel hits (text recall) before recommending a deletion. Empty
        # graph -> empty list -> prompt unchanged (safe degrade).
        dependents = state.dependency_graph.dependents_of(file_path)
        sentinel_count = len(state.sentinel_hits.get(file_path, []))
        prompt = build_deletion_analysis_prompt(
            file_path,
            file_diff.lines_deleted,
            state.config.project_context,
            dependents=dependents,
        )
        memory_text = self.get_memory_context(self._current_phase, [file_path])
        if memory_text:
            prompt = f"{prompt}\n\n# Prior Knowledge\n{memory_text}"
        rationale = "File deleted in upstream branch."
        try:
            raw = await self._call_llm_with_retry(
                [{"role": "user", "content": prompt}], system=EXECUTOR_SYSTEM
            )
            rationale = str(raw).strip()
        except Exception as exc:
            logger.warning("analyze_deletion LLM failed for %s: %s", file_path, exc)

        if dependents or sentinel_count:
            signals: list[str] = []
            if dependents:
                signals.append(
                    f"{len(dependents)} dependent file(s) still import this "
                    "(dependency graph)"
                )
            if sentinel_count:
                signals.append(f"{sentinel_count} sentinel hit(s) (text scan)")
            rationale = f"{rationale}\n\n⚠ Deletion risk: {'; '.join(signals)}."

        return UserDecisionItem(
            item_id=f"deleted_only_{file_path}",
            file_path=file_path,
            description=f"File '{file_path}' is deleted in upstream.",
            risk_context=rationale,
            current_classification=RiskLevel.DELETED_ONLY.value,
            options=[
                DecisionOption(
                    key="A",
                    label="confirm_delete",
                    description="Apply deletion (remove this file)",
                ),
                DecisionOption(
                    key="B",
                    label="keep",
                    description="Keep file (do not apply upstream deletion)",
                ),
            ],
        )

    async def build_rebuttal(
        self,
        issues: list[JudgeIssue],
        state: MergeState,
    ) -> ExecutorRebuttal:
        if not issues:
            return ExecutorRebuttal(accepts_all=True)

        if len(issues) <= _REBUTTAL_CHUNK_SIZE:
            return await self._run_rebuttal_chunk(issues, state.config.project_context)

        chunks = _chunk_issues_by_file(issues, _REBUTTAL_CHUNK_SIZE)
        logger.info(
            "build_rebuttal: chunking %d issues across %d files into %d chunks",
            len(issues),
            len({i.file_path for i in issues}),
            len(chunks),
        )

        async def _process(idx: int) -> ExecutorRebuttal:
            return await self._run_rebuttal_chunk(
                chunks[idx], state.config.project_context
            )

        # U5: ``_chunk_issues_by_file`` already groups issues by file so each
        # chunk's file set should be disjoint from every other chunk's; the
        # assert pins that invariant down as a regression net.
        assert_disjoint_file_shards(
            [[issue.file_path for issue in chunk] for chunk in chunks]
        )
        runner = ParallelFileRunner.from_api_key_env_list(
            self.llm_config.api_key_env_list,
            override=state.config.parallel_file_concurrency,
        )
        results = await runner.run_files(list(range(len(chunks))), _process)

        merged_accepts_all = True
        merged_disputes: list[DisputePoint] = []
        merged_repairs: list[RepairInstruction] = []
        rationales: list[str] = []
        for idx in range(len(chunks)):
            result = results.get(idx)
            if isinstance(result, BaseException):
                # ParallelFileRunner swallows exceptions; _run_rebuttal_chunk
                # already has its own try/except + accept-all fallback, so
                # reaching here means runner-side cancellation — degrade
                # to accept-all for this chunk so the dispute loop keeps
                # moving instead of stalling the whole layer.
                logger.warning("rebuttal chunk %d crashed in runner: %s", idx, result)
                merged_repairs.extend(
                    RepairInstruction(
                        file_path=i.file_path,
                        instruction=i.description,
                        severity=i.issue_level,
                        is_repairable=True,
                    )
                    for i in chunks[idx]
                    if i.must_fix_before_merge
                )
                continue
            assert isinstance(result, ExecutorRebuttal)
            if not result.accepts_all:
                merged_accepts_all = False
            merged_disputes.extend(result.dispute_points)
            merged_repairs.extend(result.repair_instructions)
            if result.overall_rationale:
                rationales.append(result.overall_rationale)

        merged_rationale = (
            f"chunked rebuttal: {len(chunks)} chunks · " + " | ".join(rationales)
            if rationales
            else f"chunked rebuttal: {len(chunks)} chunks"
        )
        return ExecutorRebuttal(
            accepts_all=merged_accepts_all or not merged_disputes,
            dispute_points=merged_disputes,
            repair_instructions=merged_repairs,
            overall_rationale=merged_rationale,
        )

    async def _run_rebuttal_chunk(
        self,
        issues: list[JudgeIssue],
        project_context: str,
    ) -> ExecutorRebuttal:
        issues_summary = "\n".join(
            f"- [{i.issue_id}] {i.issue_level.value}: {i.description}" for i in issues
        )
        file_paths = list({i.file_path for i in issues})
        prompt = build_rebuttal_prompt(
            issues_summary,
            file_paths,
            project_context,
            last_stop_reason=self._last_merge_stop_reason,
            last_had_prose_preamble=self._last_merge_had_prose_preamble,
        )
        memory_text = self.get_memory_context(self._current_phase, file_paths)
        if memory_text:
            prompt = f"{prompt}\n\n# Prior Knowledge\n{memory_text}"
        try:
            raw = await self._call_llm_with_retry(
                [{"role": "user", "content": prompt}], system=EXECUTOR_SYSTEM
            )
            raw_str = str(raw).strip()
            if raw_str.startswith("```"):
                lines = raw_str.splitlines()
                raw_str = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
            data = json.loads(raw_str)
        except Exception as exc:
            logger.warning("build_rebuttal LLM failed: %s", exc)
            return ExecutorRebuttal(
                accepts_all=True,
                repair_instructions=[
                    RepairInstruction(
                        file_path=i.file_path,
                        instruction=i.description,
                        severity=i.issue_level,
                        is_repairable=True,
                    )
                    for i in issues
                    if i.must_fix_before_merge
                ],
                overall_rationale="Rebuttal analysis failed; accepting all issues for repair.",
            )

        accepts_all: bool = bool(data.get("accepts_all", False))
        dispute_points: list[DisputePoint] = []
        repair_instructions: list[RepairInstruction] = []
        issue_map = {i.issue_id: i for i in issues}

        for decision in data.get("decisions", []):
            issue_id = decision.get("issue_id", "")
            action = decision.get("action", "accept")
            evidence = decision.get("counter_evidence", "")
            if action == "dispute":
                dispute_points.append(
                    DisputePoint(
                        issue_id=issue_id, counter_evidence=evidence, accepts=False
                    )
                )
            else:
                original = issue_map.get(issue_id)
                if original and original.must_fix_before_merge:
                    repair_instructions.append(
                        RepairInstruction(
                            file_path=original.file_path,
                            instruction=original.description,
                            severity=original.issue_level,
                            is_repairable=True,
                            source_issue_id=issue_id,
                        )
                    )

        return ExecutorRebuttal(
            accepts_all=accepts_all or not dispute_points,
            dispute_points=dispute_points,
            repair_instructions=repair_instructions,
            overall_rationale=data.get("overall_rationale", ""),
        )

    def can_handle(self, state: MergeState) -> bool:
        from src.models.state import SystemStatus

        return state.status in (
            SystemStatus.AUTO_MERGING,
            SystemStatus.ANALYZING_CONFLICTS,
        )


def _chunk_issues_by_file(
    issues: list[JudgeIssue], chunk_size: int
) -> list[list[JudgeIssue]]:
    """Pack issues into chunks of at most ``chunk_size`` items while keeping
    all issues for a single file in the same chunk.

    Splitting one file across two rebuttal calls would let the LLM disagree
    with itself on shared context (same diff, two verdicts). Keep file groups
    intact. A single file with more issues than ``chunk_size`` still lands
    in one over-sized chunk — that's rare and still smaller than the
    "everything in one prompt" failure mode this function exists to fix.
    """
    by_file: dict[str, list[JudgeIssue]] = {}
    for issue in issues:
        by_file.setdefault(issue.file_path, []).append(issue)

    chunks: list[list[JudgeIssue]] = []
    current: list[JudgeIssue] = []
    for group in by_file.values():
        if current and len(current) + len(group) > chunk_size:
            chunks.append(current)
            current = []
        current.extend(group)
    if current:
        chunks.append(current)
    return chunks


def _extract_diff_ranges(
    file_diff: FileDiff, side: Literal["current", "target"] = "current"
) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    if file_diff.hunks:
        for hunk in file_diff.hunks:
            if side == "target":
                ranges.append((hunk.start_line_target, hunk.end_line_target))
            else:
                ranges.append((hunk.start_line_current, hunk.end_line_current))
    elif file_diff.lines_added > 0 or file_diff.lines_deleted > 0:
        ranges.append((1, file_diff.lines_added + file_diff.lines_deleted + 100))
    return ranges


def _foreign_chars(merged: str, *sources: str) -> str | None:
    """Return a sample of non-ASCII glyphs the merge invented, or None.

    A faithful merge selects and combines lines that already exist in the fork
    or upstream version; merging ASCII source code never needs to invent a new
    non-ASCII glyph. When the LLM hallucinates inside an opaque blob (observed:
    a base64 cert literal where a fullwidth ``，`` U+FF0C was injected,
    breaking the Go string literal so the file no longer compiles) the output
    gains a non-ASCII character present in neither input. We treat that as a
    corruption signal and escalate rather than commit unparseable bytes.

    Scope is deliberately narrow — only **non-ASCII** characters absent from
    *both* sources are flagged. Pure-ASCII recombination is left alone so the
    guard never second-guesses a legitimate merge; if a source genuinely
    contains non-ASCII text (e.g. CJK comments) those glyphs are in the union
    and therefore allowed.
    """
    allowed: set[str] = set()
    for src in sources:
        allowed.update(src)
    foreign = [
        ch for ch in dict.fromkeys(merged) if ord(ch) > 127 and ch not in allowed
    ]
    if not foreign:
        return None
    return "".join(foreign)[:20]


def _build_chunk_merge_prompt(
    file_path: str,
    current_chunk: str,
    target_chunk: str,
    chunk_num: int,
    total_chunks: int,
    project_context: str,
    rationale: str,
) -> str:
    fence = "```"
    lang = file_path.rsplit(".", 1)[-1] if "." in file_path else ""
    return (
        f"Merge chunk {chunk_num}/{total_chunks} of file `{file_path}`.\n\n"
        f"# Project Context\n{project_context or 'No project context provided.'}\n\n"
        f"# Merge Rationale\n{rationale}\n\n"
        f"# Current (Fork) — chunk {chunk_num}/{total_chunks}\n"
        f"{fence}{lang}\n{current_chunk}\n{fence}\n\n"
        f"# Target (Upstream) — chunk {chunk_num}/{total_chunks}\n"
        f"{fence}{lang}\n{target_chunk}\n{fence}\n\n"
        "Merge these two sections: preserve fork customisations, incorporate upstream "
        "changes. Return ONLY the merged content, no explanations, no code fences.\n\n"
        "# GROUNDING — CHUNK ISOLATION\n"
        "You are merging ONE self-contained slice of a larger file, not the whole "
        "file. Symbols defined in other slices are NOT visible here.\n"
        "- Do NOT introduce a symbol, import, type, or member access that does not "
        "appear in the two sections above. A symbol not visible here does not exist "
        "at this location — do not fabricate it.\n"
        "- If combining both sides would require a symbol not present in these "
        "sections, keep the side that is self-consistent without it.\n"
        "- Do not add a closing or opening brace to 'balance' a slice that looks "
        "incomplete — the slice boundary is intentional; emit only the merged lines."
    )


from src.agents.registry import AgentRegistry  # noqa: E402

AgentRegistry.register("executor", ExecutorAgent, extra_kwargs=["git_tool"])
