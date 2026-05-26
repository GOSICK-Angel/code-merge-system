from typing import Any, Literal

from src.agents.base_agent import BaseAgent
from src.core.parallel_file_runner import (
    ParallelFileRunner,
    assert_disjoint_file_shards,
)
from src.models.config import AgentLLMConfig
from src.models.message import AgentType, AgentMessage, MessageType
from src.models.plan import MergePhase
from src.models.diff import FileDiff
from src.models.conflict import ConflictAnalysis, ConflictType
from src.models.decision import MergeDecision
from src.models.state import MergeState
from src.llm.prompt_builders import AgentPromptBuilder
from src.llm.prompts.analyst_prompts import (
    ANALYST_SYSTEM,
    build_commit_round_prompt,
    build_conflict_analysis_prompt,
    build_decision_proposal_prompt,
    parse_decision_proposals,
)
from src.llm.response_parser import parse_commit_round_analyses, parse_conflict_analysis
from src.models.forks_profile import ForksProfile
from src.tools.chunk_processor import split_by_semantic_boundary
from src.tools.forks_profile_loader import format_analyst_context
from src.tools.git_tool import GitTool


# U1 reducer constants (doc/large-scale-file-processing-optimization.md §5.1.1).
PENALTY_FACTOR: float = 0.8
HARD_CAP_CHUNKS: int = 8
HARD_CAP_BYTES: int = 10 * 1024 * 1024
HARD_CAP_CONFIDENCE: float = 0.3
_STRATEGY_PRECEDENCE: tuple[MergeDecision, ...] = (
    MergeDecision.ESCALATE_HUMAN,
    MergeDecision.SEMANTIC_MERGE,
    MergeDecision.TAKE_TARGET,
    MergeDecision.TAKE_CURRENT,
)


class ConflictAnalystAgent(BaseAgent):
    agent_type = AgentType.CONFLICT_ANALYST
    contract_name = "conflict_analyst"

    def __init__(self, llm_config: AgentLLMConfig, git_tool: GitTool | None = None):
        super().__init__(llm_config)
        self.git_tool = git_tool

    async def run(self, state: MergeState) -> AgentMessage:
        view = self.restricted_view(state)
        results: dict[str, ConflictAnalysis] = {}

        if view.merge_plan is None:
            return AgentMessage(
                sender=AgentType.CONFLICT_ANALYST,
                receiver=AgentType.ORCHESTRATOR,
                phase=MergePhase.CONFLICT_ANALYSIS,
                message_type=MessageType.PHASE_COMPLETED,
                subject="Conflict analysis skipped: no plan",
                payload={},
            )

        high_risk_files: list[str] = []
        for batch in view.merge_plan.phases:
            from src.models.diff import RiskLevel

            if batch.risk_level in (RiskLevel.HUMAN_REQUIRED, RiskLevel.AUTO_RISKY):
                high_risk_files.extend(batch.file_paths)

        file_diffs_map: dict[str, FileDiff] = {}
        for fd in view.file_diffs:
            file_diffs_map[fd.file_path] = fd

        forks_profile: ForksProfile | None = getattr(view, "forks_profile", None)
        # lock #27 path A: drive chunked thresholds from MergeState.thresholds
        # snapshot populated by InitializePhase. analyze_file falls back to
        # its own defaults when these are None, preserving call-site freedom.
        thresholds = view.thresholds
        chunk_size = view.config.chunk_size_chars
        min_chunked_confidence = thresholds.chunked_aggregation_min_confidence

        async def _analyze_one(file_path: str) -> ConflictAnalysis | None:
            fd = file_diffs_map.get(file_path)
            if fd is None:
                return None
            base_content = target_content = current_content = None
            if self.git_tool and hasattr(view, "_merge_base"):
                base_content, current_content, target_content = (
                    self.git_tool.get_three_way_diff(
                        view._merge_base or "",
                        view.config.fork_ref,
                        view.config.upstream_ref,
                        file_path,
                    )
                )
            return await self.analyze_file(
                fd,
                base_content=base_content,
                current_content=current_content,
                target_content=target_content,
                project_context=view.config.project_context,
                forks_profile=forks_profile,
                chunk_size_chars=chunk_size,
                min_chunked_confidence=min_chunked_confidence,
            )

        # U5: each file gets its own shard; duplicates in ``high_risk_files``
        # would cause double-analysis and waste an LLM call, so refuse early.
        assert_disjoint_file_shards([[fp] for fp in high_risk_files])
        runner = ParallelFileRunner.from_api_key_env_list(
            self.llm_config.api_key_env_list,
            override=view.config.parallel_file_concurrency,
        )
        file_results = await runner.run_files(high_risk_files, _analyze_one)
        for fp, result in file_results.items():
            if isinstance(result, BaseException):
                self.logger.error(
                    "Parallel conflict analysis failed for %s: %s", fp, result
                )
                continue
            if result is not None:
                results[fp] = result

        state.conflict_analyses.update(results)

        return AgentMessage(
            sender=AgentType.CONFLICT_ANALYST,
            receiver=AgentType.ORCHESTRATOR,
            phase=MergePhase.CONFLICT_ANALYSIS,
            message_type=MessageType.PHASE_COMPLETED,
            subject=f"Analyzed {len(results)} files",
            payload={"analyzed_count": len(results)},
        )

    async def analyze_file(
        self,
        file_diff: FileDiff,
        base_content: str | None,
        current_content: str | None,
        target_content: str | None,
        project_context: str = "",
        forks_profile: ForksProfile | None = None,
        chunk_size_chars: int | None = None,
        min_chunked_confidence: float | None = None,
    ) -> ConflictAnalysis:
        # U1.A: build_staged_content runs regardless of memory_store
        # availability. Only the memory-text injection remains gated.
        builder = AgentPromptBuilder(
            self.llm_config, self._memory_store, self._memory_hit_tracker
        )
        enriched_context = project_context
        if self._memory_store is not None:
            memory_text = builder.build_memory_context_text(
                [file_diff.file_path], current_phase=self._current_phase
            )
            if memory_text:
                enriched_context = (
                    f"{project_context}\n\n{memory_text}"
                    if project_context
                    else memory_text
                )

        # §9 P0/P1: prepend a short forks-profile context block so the
        # analyst won't recommend take_target on paths the fork has
        # deliberately dropped or rewritten. ``format_analyst_context``
        # returns ``""`` when the profile has nothing useful to inject.
        if forks_profile is not None:
            profile_block = format_analyst_context(forks_profile, file_diff.file_path)
            if profile_block:
                enriched_context = (
                    f"{profile_block}\n\n{enriched_context}"
                    if enriched_context
                    else profile_block
                )

        # U1: chunked path when either side exceeds chunk_size_chars * 2
        # (default 40KB). Reuses src/tools/chunk_processor.split_by_semantic_boundary
        # (facts.md D2 / plan P1-2).
        chunk_size = chunk_size_chars if chunk_size_chars is not None else 20000
        max_len = max(len(current_content or ""), len(target_content or ""))
        if max_len > chunk_size * 2:
            return await self._chunked_analyze_file(
                file_diff,
                base_content,
                current_content or "",
                target_content or "",
                enriched_context,
                chunk_size=chunk_size,
                min_chunked_confidence=(
                    min_chunked_confidence
                    if min_chunked_confidence is not None
                    else 0.85
                ),
            )

        diff_ranges = _extract_diff_ranges(file_diff)
        target_ranges = _extract_diff_ranges(file_diff, side="target")
        content_budget = builder.compute_content_budget(
            ANALYST_SYSTEM + enriched_context
        )
        content_budget_tokens = content_budget // 4  # chars -> rough tokens
        if current_content:
            current_content = builder.build_staged_content(
                current_content,
                file_diff.file_path,
                diff_ranges,
                content_budget_tokens // 2,
            )
        if target_content:
            target_content = builder.build_staged_content(
                target_content,
                file_diff.file_path,
                target_ranges,
                content_budget_tokens // 2,
            )
        if base_content:
            base_content = builder.build_staged_content(
                base_content,
                file_diff.file_path,
                diff_ranges,
                content_budget_tokens // 4,
            )

        prompt = build_conflict_analysis_prompt(
            file_diff,
            base_content,
            current_content,
            target_content,
            enriched_context,
        )
        messages = [{"role": "user", "content": prompt}]

        try:
            raw = await self._call_llm_with_retry(messages, system=ANALYST_SYSTEM)
            return parse_conflict_analysis(
                str(raw), file_diff.file_path, self.llm_config.model
            )
        except Exception as e:
            self.logger.error(
                f"Conflict analysis failed for {file_diff.file_path}: {e}"
            )
            return ConflictAnalysis(
                file_path=file_diff.file_path,
                conflict_points=[],
                overall_confidence=0.3,
                recommended_strategy=MergeDecision.ESCALATE_HUMAN,
                conflict_type=ConflictType.UNKNOWN,
                rationale=f"Analysis failed: {e}",
                confidence=0.3,
            )

    async def _chunked_analyze_file(
        self,
        file_diff: FileDiff,
        base_content: str | None,
        current_content: str,
        target_content: str,
        enriched_context: str,
        *,
        chunk_size: int,
        min_chunked_confidence: float,
    ) -> ConflictAnalysis:
        """Split large file into chunks, fan-out LLM calls, aggregate deterministically."""
        file_path = file_diff.file_path
        current_chunks = split_by_semantic_boundary(
            current_content, file_path, chunk_size
        )
        target_chunks = split_by_semantic_boundary(
            target_content, file_path, chunk_size
        )
        # Pair shorter / longer side by zipping the shorter length; remainder is
        # appended with empty target so every current_content chunk gets analyzed.
        pairs: list[tuple[str, str]] = []
        for idx in range(max(len(current_chunks), len(target_chunks))):
            cur = current_chunks[idx] if idx < len(current_chunks) else ""
            tgt = target_chunks[idx] if idx < len(target_chunks) else ""
            pairs.append((cur, tgt))

        # ③ Relevance pre-filter: a pair whose current and target chunks are
        # byte-identical has no divergence, so it cannot host a conflict — skip
        # its LLM call. Only prune when some (but not all) pairs are unchanged:
        # an all-identical file (no anchor to analyze) and an all-changed file
        # both keep every pair, so the aggregate never sees an empty set and the
        # degenerate paths behave exactly as before. This is a strict subset of
        # the old fan-out, so it never changes the verdict — only the cost.
        changed_indices = [i for i, (cur, tgt) in enumerate(pairs) if cur != tgt]
        if changed_indices and len(changed_indices) < len(pairs):
            analyze_indices = changed_indices
        else:
            analyze_indices = list(range(len(pairs)))
        skipped = len(pairs) - len(analyze_indices)
        if skipped:
            self.logger.info(
                "Chunked analysis %s: skipping %d/%d unchanged chunk pairs",
                file_path,
                skipped,
                len(pairs),
            )

        async def _analyze_chunk(idx: int) -> ConflictAnalysis:
            cur_chunk, tgt_chunk = pairs[idx]
            prompt = build_conflict_analysis_prompt(
                file_diff,
                base_content,
                cur_chunk,
                tgt_chunk,
                enriched_context,
            )
            messages = [{"role": "user", "content": prompt}]
            raw = await self._call_llm_with_retry(messages, system=ANALYST_SYSTEM)
            return parse_conflict_analysis(str(raw), file_path, self.llm_config.model)

        # U5: chunks of a single file are tagged ``"<file>#<idx>"`` so the
        # disjointness contract still applies (each chunk is its own shard);
        # this keeps the assert form uniform across all 6 fan-out call sites.
        assert_disjoint_file_shards([[f"{file_path}#{idx}"] for idx in analyze_indices])
        runner = ParallelFileRunner.from_api_key_env_list(
            self.llm_config.api_key_env_list
        )
        results = await runner.run_files(analyze_indices, _analyze_chunk)

        chunk_analyses: list[ConflictAnalysis] = []
        failed_indices: list[int] = []
        failure_reasons: list[str] = []
        for idx in sorted(results.keys()):
            value = results[idx]
            if isinstance(value, BaseException):
                failed_indices.append(idx)
                failure_reasons.append(type(value).__name__.lower())
                self.logger.error(
                    "Chunked analysis failed for %s chunk %d: %s",
                    file_path,
                    idx,
                    value,
                )
            else:
                chunk_analyses.append(value)

        if failed_indices:
            reason = ", ".join(
                f"chunk {i}: {failure_reasons[k]}" for k, i in enumerate(failed_indices)
            )
            return ConflictAnalysis(
                file_path=file_path,
                conflict_points=[],
                overall_confidence=HARD_CAP_CONFIDENCE,
                recommended_strategy=MergeDecision.ESCALATE_HUMAN,
                conflict_type=ConflictType.UNKNOWN,
                rationale=(
                    f"Chunked analysis fell back to ESCALATE_HUMAN due to "
                    f"failure in {len(failed_indices)} of {len(analyze_indices)} "
                    f"analyzed chunks ({reason})"
                ),
                confidence=HARD_CAP_CONFIDENCE,
                is_chunked=True,
                chunk_count=len(analyze_indices),
            )

        return _aggregate_chunked_analyses(
            chunk_analyses,
            file_path=file_path,
            min_confidence=min_chunked_confidence,
        )

    async def analyze_conflict_point(
        self,
        file_diff: FileDiff,
        hunk_content: str,
        project_context: str = "",
    ) -> ConflictAnalysis:
        return await self.analyze_file(
            file_diff,
            base_content=None,
            current_content=hunk_content,
            target_content=None,
            project_context=project_context,
        )

    def compute_confidence(
        self, analysis: ConflictAnalysis, has_base_version: bool
    ) -> float:
        base_confidence = analysis.confidence

        type_adjustment = {
            ConflictType.SEMANTIC_EQUIVALENT: 0.20,
            ConflictType.DEPENDENCY_UPDATE: 0.15,
            ConflictType.CONFIGURATION: 0.10,
            ConflictType.CONCURRENT_MODIFICATION: 0.0,
            ConflictType.REFACTOR_VS_FEATURE: -0.10,
            ConflictType.DELETION_VS_MODIFICATION: -0.15,
            ConflictType.INTERFACE_CHANGE: -0.20,
            ConflictType.LOGIC_CONTRADICTION: -0.30,
            ConflictType.UNKNOWN: -0.25,
        }

        base_bonus = 0.10 if has_base_version else 0.0
        calibrated = base_confidence * 0.85
        adjustment = type_adjustment.get(analysis.conflict_type, 0.0)
        final = calibrated + adjustment + base_bonus
        return max(0.10, min(0.95, round(final, 3)))

    async def analyze_commit_round(
        self,
        round_commits: list[dict[str, Any]],
        file_three_way: dict[str, tuple[str | None, str | None, str | None]],
        file_languages: dict[str, str],
        project_context: str = "",
        per_file_instructions: dict[str, str] | None = None,
    ) -> dict[str, "ConflictAnalysis"]:
        if not file_three_way:
            return {}

        prompt = build_commit_round_prompt(
            round_commits, file_three_way, file_languages, project_context
        )
        file_paths = list(file_three_way.keys())

        # Reviewer-supplied free-text instructions (one per file) — when
        # the user picks the ``llm_with_instruction`` decision option and
        # writes guidance like "keep fork's audit logging and upstream's
        # validation order", that text lands here and gets prepended to
        # the LLM payload so the merge respects the reviewer's intent.
        if per_file_instructions:
            relevant = {
                fp: per_file_instructions[fp]
                for fp in file_paths
                if per_file_instructions.get(fp)
            }
            if relevant:
                lines = ["# Reviewer Instructions (per file)"]
                for fp, inst in relevant.items():
                    lines.append(f"- `{fp}`: {inst}")
                prompt = f"{prompt}\n\n" + "\n".join(lines)

        memory_text = self.get_memory_context(self._current_phase, file_paths)
        if memory_text:
            prompt = f"{prompt}\n\n# Prior Knowledge\n{memory_text}"
        try:
            raw = await self._call_llm_with_retry(
                [{"role": "user", "content": prompt}], system=ANALYST_SYSTEM
            )
        except Exception as e:
            self.logger.error(
                "Commit-round analysis failed (%d files, %d commits): %s",
                len(file_paths),
                len(round_commits),
                e,
            )
            return {}

        analyses = parse_commit_round_analyses(str(raw), file_paths)
        parsed_count = len(analyses)
        requested_count = len(file_paths)

        if requested_count > 0 and parsed_count == 0:
            self._consecutive_failures += 1
            self._sliding_window.append(False)
            self.logger.warning(
                "Commit-round parsed 0/%d analyses (likely truncated JSON or "
                "schema break); response_chars=%d, consecutive_failures=%d",
                requested_count,
                len(str(raw)),
                self._consecutive_failures,
            )
        elif parsed_count < requested_count:
            missing = [fp for fp in file_paths if fp not in analyses][:5]
            self.logger.warning(
                "Commit-round partial parse: %d/%d files; missing sample=%s",
                parsed_count,
                requested_count,
                missing,
            )

        return analyses

    async def propose_decision_options(
        self,
        file_path: str,
        base_content: str | None,
        fork_content: str | None,
        upstream_content: str | None,
        language: str = "",
        project_context: str = "",
        max_options: int = 3,
    ) -> list[dict[str, str]]:
        """Ask the LLM for 1–``max_options`` file-specific decision
        proposals on a HUMAN_REQUIRED file. Returns a list of dicts
        with keys ``key`` / ``label`` / ``description`` / ``preview``.

        Graceful degrade: any LLM failure, parse failure, or empty
        response yields ``[]`` — callers fall back to the base decision
        ladder. This method never raises.
        """
        prompt = build_decision_proposal_prompt(
            file_path,
            base_content,
            fork_content,
            upstream_content,
            language=language,
            project_context=project_context,
            max_options=max_options,
        )
        try:
            raw = await self._call_llm_with_retry(
                [{"role": "user", "content": prompt}], system=ANALYST_SYSTEM
            )
        except Exception as exc:
            self.logger.warning(
                "propose_decision_options failed for %s: %s", file_path, exc
            )
            return []
        return parse_decision_proposals(str(raw))

    def can_handle(self, state: MergeState) -> bool:
        from src.models.state import SystemStatus

        return state.status == SystemStatus.ANALYZING_CONFLICTS


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


def _aggregate_chunked_analyses(
    chunk_analyses: list[ConflictAnalysis],
    *,
    file_path: str,
    min_confidence: float,
) -> ConflictAnalysis:
    """Deterministic reducer for chunked ConflictAnalyst output.

    Three-tier path (doc §5.1.1):
      1. Hard cap → ESCALATE_HUMAN when chunks > 8 OR total bytes > 10 MB.
      2. Fast path → unanimous strategy, min(conf) >= ``min_confidence``,
         and no chunk is security-sensitive. Returns first chunk's strategy
         with min(conf) and no penalty.
      3. Slow path → strategy precedence ``ESCALATE > SEMANTIC > TAKE_*``,
         confidence = min(conf) * ``PENALTY_FACTOR`` (0.8).
    """
    chunk_count = len(chunk_analyses)
    total_bytes = sum(len(c.rationale or "") for c in chunk_analyses)
    is_security = any(c.is_security_sensitive for c in chunk_analyses)

    if chunk_count > HARD_CAP_CHUNKS or total_bytes > HARD_CAP_BYTES:
        return ConflictAnalysis(
            file_path=file_path,
            conflict_points=[],
            overall_confidence=HARD_CAP_CONFIDENCE,
            recommended_strategy=MergeDecision.ESCALATE_HUMAN,
            conflict_type=ConflictType.UNKNOWN,
            is_security_sensitive=is_security,
            rationale=(
                f"Chunked analysis escalated: {chunk_count} chunks "
                f"({total_bytes} bytes of rationale) too large for safe "
                f"chunked analysis."
            ),
            confidence=HARD_CAP_CONFIDENCE,
            is_chunked=True,
            chunk_count=chunk_count,
        )

    strategies = {c.recommended_strategy for c in chunk_analyses}
    min_conf = min(c.confidence for c in chunk_analyses)

    if len(strategies) == 1 and min_conf >= min_confidence and not is_security:
        unanimous_strategy = chunk_analyses[0].recommended_strategy
        return ConflictAnalysis(
            file_path=file_path,
            conflict_points=[],
            overall_confidence=min_conf,
            recommended_strategy=unanimous_strategy,
            conflict_type=chunk_analyses[0].conflict_type,
            is_security_sensitive=False,
            rationale=(
                f"Chunked analysis: {chunk_count} chunks unanimous on "
                f"{unanimous_strategy.value}; min_conf={min_conf:.2f}"
            ),
            confidence=min_conf,
            is_chunked=True,
            chunk_count=chunk_count,
        )

    chosen_strategy = next(
        (s for s in _STRATEGY_PRECEDENCE if s in strategies),
        MergeDecision.ESCALATE_HUMAN,
    )
    return ConflictAnalysis(
        file_path=file_path,
        conflict_points=[],
        overall_confidence=min_conf * PENALTY_FACTOR,
        recommended_strategy=chosen_strategy,
        conflict_type=ConflictType.UNKNOWN,
        is_security_sensitive=is_security,
        rationale=(
            f"Chunked analysis: {chunk_count} chunks disagreement "
            f"(strategies={sorted(s.value for s in strategies)}); "
            f"precedence-derived={chosen_strategy.value}; "
            f"penalty={PENALTY_FACTOR}"
        ),
        confidence=min_conf * PENALTY_FACTOR,
        is_chunked=True,
        chunk_count=chunk_count,
    )


from src.agents.registry import AgentRegistry  # noqa: E402

AgentRegistry.register(
    "conflict_analyst", ConflictAnalystAgent, extra_kwargs=["git_tool"]
)
