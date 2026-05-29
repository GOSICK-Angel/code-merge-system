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
from src.models.dependency import DependencyImpactHint
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
from src.tools.diff_facts import DiffFacts, compute_diff_facts
from src.tools.diff_facts_grounding import check_rationale_against_facts
from src.tools.native_3way import NativeMergeOutcome, predict_native_3way_outcome
from src.tools.hallucinated_symbol_guard import scan_rationale_for_hallucinations
from src.tools.import_symbol_harvester import harvest_imports_for_file
from src.tools.required_new_apis import extract_required_new_apis


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


def _format_blast_radius_block(hint: DependencyImpactHint | None) -> str:
    """Render a dependency blast-radius caution block, or ``""`` when there is
    no signal. Phase B step 7: a non-empty graph makes the analyst more
    conservative for hub files (risk monotonicity, plan §5 — caution only goes
    up). Injected into ``enriched_context`` so both the chunked and the
    single-shot analysis paths see it."""
    if hint is None or not hint.has_signal:
        return ""
    lines = [
        "## Dependency Impact",
        f"- Direct dependents (files importing this one): {hint.direct_dependents}",
        f"- Transitive impact radius (files affected by a break): {hint.impact_radius}",
    ]
    if hint.is_god_node:
        lines.append(
            "- GOD NODE: this file is a dependency hub. A regression here "
            "ripples widely — strongly prefer preserving its public interface; "
            "avoid take_target when it would drop fork-side API the dependents "
            "rely on, and lean toward semantic_merge or escalate_human over a "
            "blind side-pick."
        )
    else:
        lines.append(
            "- Be conservative about changing this file's public interface; "
            "the dependents above may break if exported symbols are removed."
        )
    return "\n".join(lines)


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
            if self.git_tool and view.merge_base_commit:
                base_content, current_content, target_content = (
                    self.git_tool.get_three_way_diff(
                        view.merge_base_commit,
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
        referenced_names: frozenset[str] = frozenset(),
        impact_hint: DependencyImpactHint | None = None,
        fork_ref: str | None = None,
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

        # Phase B step 7: dependency blast-radius / God Node caution. Empty
        # graph -> empty block -> no behavior change (safe degrade).
        blast_block = _format_blast_radius_block(impact_hint)
        if blast_block:
            enriched_context = (
                f"{blast_block}\n\n{enriched_context}"
                if enriched_context
                else blast_block
            )

        # PR-A: capture original (unstaged) fork/upstream content for the
        # post-LLM rationale grounding scan. ``build_staged_content`` below
        # rewrites these names, so scanning against the staged versions
        # would false-positive on symbols that exist in trimmed-out lines.
        original_current = current_content or ""
        original_target = target_content or ""

        # PR-C: deterministic per-side verb counts derived from the full
        # untrimmed three-way content. Injected into the prompt so the LLM
        # sees ground truth and used post-hoc to flag rationale verbs that
        # contradict those facts.
        diff_facts = compute_diff_facts(base_content, original_current, original_target)

        # Native-3way outcome: the LLM previously read ``conflict_count=0``
        # (computed against the original refs, which are clean) as
        # "no conflict" and gave up on specifics. Compute the actual
        # ``git merge-file`` outcome on the raw content and pass it
        # explicitly to the prompt so it sees ground truth.
        native_3way_outcome: NativeMergeOutcome = predict_native_3way_outcome(
            base_content, original_current, original_target
        )

        # U1: chunked path when either side exceeds chunk_size_chars * 2
        # (default 40KB). Reuses src/tools/chunk_processor.split_by_semantic_boundary
        # (facts.md D2 / plan P1-2).
        chunk_size = chunk_size_chars if chunk_size_chars is not None else 20000
        max_len = max(len(current_content or ""), len(target_content or ""))
        if max_len > chunk_size * 2:
            chunked = await self._chunked_analyze_file(
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
                imported_symbols=_safe_harvest(
                    file_diff.file_path,
                    original_current,
                    fork_ref,
                    self.git_tool,
                ),
            )
            return _with_grounding_warnings(
                chunked,
                original_current,
                original_target,
                file_diff.file_path,
                diff_facts=diff_facts,
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
                is_security_sensitive=file_diff.is_security_sensitive,
                referenced_names=referenced_names,
            )
        if target_content:
            target_content = builder.build_staged_content(
                target_content,
                file_diff.file_path,
                target_ranges,
                content_budget_tokens // 2,
                is_security_sensitive=file_diff.is_security_sensitive,
                referenced_names=referenced_names,
            )
        if base_content:
            base_content = builder.build_staged_content(
                base_content,
                file_diff.file_path,
                diff_ranges,
                content_budget_tokens // 4,
                is_security_sensitive=file_diff.is_security_sensitive,
                referenced_names=referenced_names,
            )

        imported_symbols = _safe_harvest(
            file_diff.file_path, original_current, fork_ref, self.git_tool
        )

        prompt = build_conflict_analysis_prompt(
            file_diff,
            base_content,
            current_content,
            target_content,
            enriched_context,
            imported_symbols=imported_symbols,
            diff_facts=diff_facts,
            native_3way_outcome=native_3way_outcome,
        )
        messages = [{"role": "user", "content": prompt}]

        try:
            raw = await self._call_llm_with_retry(messages, system=ANALYST_SYSTEM)
            parsed = parse_conflict_analysis(
                str(raw), file_diff.file_path, self.llm_config.model
            )
            return _with_grounding_warnings(
                parsed,
                original_current,
                original_target,
                file_diff.file_path,
                diff_facts=diff_facts,
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
        imported_symbols: dict[str, list[str]] | None = None,
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
                imported_symbols=imported_symbols,
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
        fork_ref: str | None = None,
    ) -> dict[str, "ConflictAnalysis"]:
        if not file_three_way:
            return {}

        imported_symbols_by_file: dict[str, dict[str, list[str]]] = {}
        for fp, (_, fork_c, _) in file_three_way.items():
            harvested = _safe_harvest(fp, fork_c, fork_ref, self.git_tool)
            if harvested:
                imported_symbols_by_file[fp] = harvested

        diff_facts_by_file: dict[str, DiffFacts] = {
            fp: compute_diff_facts(base, fork, upstream)
            for fp, (base, fork, upstream) in file_three_way.items()
        }

        native_3way_outcome_by_file: dict[str, NativeMergeOutcome] = {
            fp: predict_native_3way_outcome(base, fork, upstream)
            for fp, (base, fork, upstream) in file_three_way.items()
        }

        prompt = build_commit_round_prompt(
            round_commits,
            file_three_way,
            file_languages,
            project_context,
            imported_symbols_by_file=imported_symbols_by_file or None,
            diff_facts_by_file=diff_facts_by_file,
            native_3way_outcome_by_file=native_3way_outcome_by_file,
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

        # PR-A: the production conflict_analysis phase routes through this
        # batched entry point, so the rationale grounding scan must run
        # here too (analyze_file's wrapper would otherwise stay dormant in
        # real runs). file_three_way carries (base, fork, upstream) per file.
        for fp, analysis in list(analyses.items()):
            _, fork_c, upstream_c = file_three_way.get(fp, (None, None, None))
            analyses[fp] = _with_grounding_warnings(
                analysis,
                fork_c or "",
                upstream_c or "",
                fp,
                diff_facts=diff_facts_by_file.get(fp),
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


def _safe_harvest(
    file_path: str,
    source_content: str | None,
    ref: str | None,
    git_tool: Any,
) -> dict[str, list[str]]:
    """PR-D-B: prompt-time view of which symbols each namespace import
    exposes. Any failure (missing ref/tool, parse glitch, git read
    error) degrades to ``{}`` — analysis must never fail because of
    best-effort prompt context.
    """
    if not source_content or not ref or git_tool is None:
        return {}
    try:
        return harvest_imports_for_file(file_path, source_content, ref, git_tool)
    except Exception:
        return {}


def _with_grounding_warnings(
    analysis: ConflictAnalysis,
    fork_content: str,
    upstream_content: str,
    file_path: str,
    diff_facts: DiffFacts | None = None,
) -> ConflictAnalysis:
    """PR-A + PR-D-A.2: classify symbols the rationale references but
    that aren't in either source.

    Two channels:

    - ``required_new_apis`` — symbols the LLM declared via the
      ``REQUIRES NEW API:`` sentinel (PR-D-A.1). The LLM has explicitly
      flagged these as missing, so they are informational, not warnings.
    - ``grounding_warnings`` — symbols that appear nowhere in fork or
      upstream AND were not declared via the sentinel. Genuine sneaky
      fabrication; reviewer must not act on them blindly.

    Returns a new ``ConflictAnalysis`` (immutable update) so callers can
    treat the return value as the canonical, ground-checked result.
    """
    rationale = analysis.rationale or ""
    if not rationale:
        return analysis

    declared = extract_required_new_apis(rationale)
    declared_set = set(declared)

    all_invented = scan_rationale_for_hallucinations(
        rationale, [fork_content, upstream_content], file_path
    )
    fabricated = [s for s in all_invented if s not in declared_set]

    verb_warnings = (
        check_rationale_against_facts(rationale, diff_facts) if diff_facts else []
    )

    merged_warnings = fabricated + verb_warnings

    if not declared and not merged_warnings:
        return analysis
    return analysis.model_copy(
        update={
            "grounding_warnings": list(analysis.grounding_warnings) + merged_warnings,
            "required_new_apis": declared,
        }
    )


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
