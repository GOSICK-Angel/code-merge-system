from datetime import datetime
from typing import Any, cast
from uuid import uuid4
from src.agents.base_agent import BaseAgent
from src.models.config import AgentLLMConfig, MergeConfig
from src.models.message import AgentType, AgentMessage, MessageType
from src.models.plan import (
    MergePlan,
    MergePhase,
    PhaseFileBatch,
    PlanIntegrityError,
    RiskSummary,
    CategorySummary,
    MergeLayer,
    DEFAULT_LAYERS,
    topological_sort_layers,
)
from src.models.diff import FileDiff, FileChangeCategory, RiskLevel
from src.models.plan_judge import PlanIssue
from src.models.state import MergeState
from src.llm.prompts.planner_prompts import (
    PLANNER_EVALUATION_SYSTEM,
    get_planner_system,
    build_classification_prompt,
    build_evaluation_prompt,
)
from src.models.plan_review import (
    PlannerIssueResponse,
    IssueResponseAction,
    PlanDiffEntry,
)
from src.tools.file_classifier import compute_risk_score, classify_file
from src.core.parallel_file_runner import (
    ParallelFileRunner,
    assert_disjoint_file_shards,
)
import fnmatch
import json
import json as json_lib


# Sub-chunk size for one classification LLM call. ``max_files_per_run``
# (config, default 500) is the OUTER batch size; this constant slices each
# outer batch further so a single LLM call never carries more than ~100
# file lines. Calibrated against the forgejo case where one 500-file
# classification prompt serialised to ~125KB input + ~25KB JSON output
# and skirted the model's long-request envelope. 100 keeps a single call
# at ~25KB / ~5KB respectively with comfortable headroom.
_CLASSIFY_FILE_CHUNK_SIZE = 100


class PlannerAgent(BaseAgent):
    agent_type = AgentType.PLANNER
    contract_name = "planner"

    def __init__(self, llm_config: AgentLLMConfig):
        super().__init__(llm_config)

    async def run(self, state: MergeState) -> AgentMessage:
        self.restricted_view(
            state
        )  # contract side-effect: asserts inputs whitelist loads
        plan, rescored_diffs = await self._generate_plan(state)
        state.merge_plan = plan
        # All state writes live in run(); _generate_plan stays pure.
        # rescored_diffs may be the same list object as state.file_diffs
        # when LLM rescoring is disabled — the identity check avoids a
        # redundant assignment in that case.
        if rescored_diffs is not state.file_diffs:
            state.file_diffs = rescored_diffs
        state.file_classifications = {
            fp: batch.risk_level for batch in plan.phases for fp in batch.file_paths
        }

        return AgentMessage(
            sender=AgentType.PLANNER,
            receiver=AgentType.ORCHESTRATOR,
            phase=MergePhase.ANALYSIS,
            message_type=MessageType.PHASE_COMPLETED,
            subject="Merge plan generated",
            payload={"plan_id": plan.plan_id},
        )

    async def _generate_plan(
        self, state: MergeState
    ) -> tuple[MergePlan, list[FileDiff]]:
        """Build the merge plan and return it together with the
        (possibly rescored) diff list. This method is pure with respect
        to ``state``: it never executes ``state.<field> = ...``. The
        caller (``run``) owns persistence so all state writes are
        collocated and discoverable.
        """
        all_file_diffs: list[FileDiff] = state.file_diffs

        # LLM gray-zone rescore is applied in BOTH paths (layered and
        # legacy). Running it before the layered branch decision means
        # the updated risk_level flows into _build_layered_plan's
        # _split_by_risk_level grouping.
        if state.config.llm_risk_scoring.enabled:
            all_file_diffs = await self._enhance_risk_scores(
                all_file_diffs, state.config
            )

        if state.file_categories:
            return self._build_layered_plan(all_file_diffs, state), all_file_diffs

        batch_size = state.config.max_files_per_run
        batches = [
            all_file_diffs[i : i + batch_size]
            for i in range(0, len(all_file_diffs), batch_size)
        ] or [[]]
        total_batches = len(batches)

        language = state.config.output.language
        system_prompt = get_planner_system(language)
        project_context = state.config.project_context

        self.logger.info(
            "Planner (legacy): %d files, batch_size=%d, total_batches=%d",
            len(all_file_diffs),
            batch_size,
            total_batches,
        )

        all_plan_data: list[dict[str, Any]] = []
        for idx, batch in enumerate(batches):
            self.logger.info(
                "Classifying batch %d/%d (%d files)", idx + 1, total_batches, len(batch)
            )
            plan_data = await self._classify_batch(
                batch,
                project_context,
                system_prompt,
                idx,
                total_batches,
                rename_pairs=state.rename_pairs or None,
            )
            all_plan_data.append(plan_data)

        merged_data = self._merge_batch_plans(all_plan_data, all_file_diffs)
        return (
            self._build_merge_plan(merged_data, state, all_file_diffs),
            all_file_diffs,
        )

    def _build_layered_plan(
        self, file_diffs: list[FileDiff], state: MergeState
    ) -> MergePlan:
        from src.tools.shadow_conflict_detector import ShadowConflictDetector

        layers = self._resolve_layers(state.config)
        categories = state.file_categories
        diffs_by_path = {fd.file_path: fd for fd in file_diffs}

        decided_paths: set[str] = set(
            getattr(state, "file_decision_records", None) or {}
        )

        actionable = {
            FileChangeCategory.B,
            FileChangeCategory.C,
            FileChangeCategory.D_MISSING,
        }
        actionable_files = {
            fp: cat
            for fp, cat in categories.items()
            if cat in actionable and fp not in decided_paths
        }

        detector = ShadowConflictDetector.from_config(state.config.shadow_rules_extra)
        shadow_conflicts = detector.detect(list(categories.keys()))
        state.shadow_conflicts = shadow_conflicts
        shadow_paths: set[str] = set()
        for sc in shadow_conflicts:
            shadow_paths.add(sc.path_a)
            shadow_paths.add(sc.path_b)

        layer_assignable = [
            fp
            for fp, cat in actionable_files.items()
            if cat in (FileChangeCategory.B, FileChangeCategory.C)
        ]
        file_layer_map = self._assign_files_to_layers(layer_assignable, layers)
        real_layer_ids = {ly.layer_id for ly in layers}
        fallback_paths: list[str] = []
        for lid in list(file_layer_map.keys()):
            if lid not in real_layer_ids:
                fallback_paths.extend(file_layer_map.pop(lid))

        phases: list[PhaseFileBatch] = []

        category_fallback = {
            FileChangeCategory.B: RiskLevel.AUTO_SAFE,
            FileChangeCategory.D_MISSING: RiskLevel.AUTO_SAFE,
            FileChangeCategory.C: RiskLevel.AUTO_RISKY,
        }

        d_missing_all = [
            fp
            for fp, cat in actionable_files.items()
            if cat == FileChangeCategory.D_MISSING
        ]
        if d_missing_all:
            d_safe, d_risky, d_human = self._split_by_risk_level(
                d_missing_all,
                diffs_by_path,
                shadow_paths,
                fallback_risk=category_fallback[FileChangeCategory.D_MISSING],
            )
            self._emit_risk_split_batches(
                phases,
                d_safe,
                d_risky,
                d_human,
                layer_id=None,
                change_category=FileChangeCategory.D_MISSING,
            )

        if fallback_paths:
            fb_by_cat: dict[FileChangeCategory, list[str]] = {}
            for fp in fallback_paths:
                fb_by_cat.setdefault(actionable_files[fp], []).append(fp)
            for cat in (FileChangeCategory.B, FileChangeCategory.C):
                paths = fb_by_cat.get(cat, [])
                if not paths:
                    continue
                safe, risky, human = self._split_by_risk_level(
                    paths,
                    diffs_by_path,
                    shadow_paths,
                    fallback_risk=category_fallback[cat],
                )
                self._emit_risk_split_batches(
                    phases,
                    safe,
                    risky,
                    human,
                    layer_id=None,
                    change_category=cat,
                )

        for layer in layers:
            layer_files = file_layer_map.get(layer.layer_id, [])
            if not layer_files:
                continue

            by_category: dict[FileChangeCategory, list[str]] = {}
            for fp in layer_files:
                cat = actionable_files[fp]
                by_category.setdefault(cat, []).append(fp)

            for change_cat in (FileChangeCategory.B, FileChangeCategory.C):
                cat_paths = by_category.get(change_cat, [])
                if not cat_paths:
                    continue
                safe, risky, human = self._split_by_risk_level(
                    cat_paths,
                    diffs_by_path,
                    shadow_paths,
                    fallback_risk=category_fallback[change_cat],
                )
                self._emit_risk_split_batches(
                    phases,
                    safe,
                    risky,
                    human,
                    layer_id=layer.layer_id,
                    change_category=change_cat,
                )

        cat_summary = self._build_category_summary(categories)
        risk_summary = self._build_risk_summary(file_diffs, actionable_files)

        self._assert_plan_integrity(phases, actionable_files, decided_paths)

        merge_base = state.merge_base_commit
        if not merge_base and hasattr(state, "_merge_base"):
            merge_base = state._merge_base or ""

        self.logger.info(
            "Layered plan: %d layers, %d phases, %d actionable files (B=%d C=%d D=%d)",
            len(layers),
            len(phases),
            len(actionable_files),
            cat_summary.b_upstream_only,
            cat_summary.c_both_changed,
            cat_summary.d_missing,
        )

        special_instructions: list[str] = []

        if state.pollution_audit and state.pollution_audit.has_pollution:
            pa = state.pollution_audit
            special_instructions.append(
                f"Pollution audit: {pa.reclassified_count} files reclassified "
                f"from {len(pa.prior_merge_commits)} prior merge commits. "
                f"Classifications have been corrected automatically."
            )

        if state.config_drifts and state.config_drifts.has_drifts:
            cd = state.config_drifts
            drift_keys = [d.key for d in cd.drifts]
            special_instructions.append(
                f"Config drift detected: {cd.drift_count} keys with divergent defaults "
                f"across sources: {', '.join(drift_keys[:10])}. "
                f"Review config files in Layer 1 (dependencies) carefully."
            )

        if state.rename_pairs:
            rename_lines = "; ".join(
                f"{old} -> {new}" for old, new in state.rename_pairs[:20]
            )
            special_instructions.append(
                f"Detected {len(state.rename_pairs)} file rename(s) "
                f"(treat old/new paths as related): {rename_lines}"
            )

        return MergePlan(
            created_at=datetime.now(),
            upstream_ref=state.config.upstream_ref,
            fork_ref=state.config.fork_ref,
            merge_base_commit=merge_base,
            phases=phases,
            risk_summary=risk_summary,
            category_summary=cat_summary,
            layers=layers,
            project_context_summary=state.config.project_context or "",
            special_instructions=special_instructions,
        )

    def _resolve_layers(self, config: MergeConfig) -> list[MergeLayer]:
        raw_layers = config.layer_config.custom_layers or DEFAULT_LAYERS
        layers = [MergeLayer(**layer_data) for layer_data in raw_layers]
        return topological_sort_layers(layers)

    def _assign_files_to_layers(
        self, file_paths: list[str], layers: list[MergeLayer]
    ) -> dict[int, list[str]]:
        result: dict[int, list[str]] = {}
        assigned: set[str] = set()

        ordered = sorted(layers, key=self._layer_specificity_key)

        for layer in ordered:
            for fp in file_paths:
                if fp in assigned:
                    continue
                if self._matches_layer(fp, layer.path_patterns):
                    result.setdefault(layer.layer_id, []).append(fp)
                    assigned.add(fp)

        unassigned = [fp for fp in file_paths if fp not in assigned]
        if unassigned:
            max_layer = max(ly.layer_id for ly in layers) if layers else 0
            fallback_id = max_layer + 1
            result[fallback_id] = unassigned

        return result

    @staticmethod
    def _layer_specificity_key(layer: MergeLayer) -> tuple[int, int]:
        has_catchall = any(p == "**" or p == "*" for p in layer.path_patterns)
        return (1 if has_catchall else 0, layer.layer_id)

    def _matches_layer(self, file_path: str, patterns: list[str]) -> bool:
        for pattern in patterns:
            if fnmatch.fnmatch(file_path, pattern):
                return True
            parts = file_path.split("/")
            for i in range(len(parts)):
                partial = "/".join(parts[: i + 1])
                if fnmatch.fnmatch(partial, pattern.rstrip("/**")):
                    return True
                if fnmatch.fnmatch(partial + "/", pattern.rstrip("*")):
                    return True
        return False

    @staticmethod
    def _split_by_risk_level(
        paths: list[str],
        diffs_by_path: dict[str, FileDiff],
        shadow_paths: set[str],
        *,
        fallback_risk: RiskLevel = RiskLevel.AUTO_SAFE,
    ) -> tuple[list[str], list[str], list[str]]:
        safe: list[str] = []
        risky: list[str] = []
        human: list[str] = []
        bucket = {
            RiskLevel.AUTO_SAFE: safe,
            RiskLevel.AUTO_RISKY: risky,
            RiskLevel.HUMAN_REQUIRED: human,
        }
        for fp in paths:
            if fp in shadow_paths:
                human.append(fp)
                continue
            fd = diffs_by_path.get(fp)
            if fd is None:
                bucket.get(fallback_risk, safe).append(fp)
                continue
            rl = fd.risk_level
            if rl == RiskLevel.HUMAN_REQUIRED:
                human.append(fp)
            elif rl == RiskLevel.AUTO_RISKY:
                risky.append(fp)
            else:
                safe.append(fp)

        # P1-6: order each bucket by ascending risk_score so the
        # Executor processes the safest files first; if a later file
        # blows up the rollback cost is bounded to the riskier tail.
        # Path is the secondary key for deterministic ordering.
        def _score_key(fp: str) -> tuple[float, str]:
            fd = diffs_by_path.get(fp)
            score = fd.risk_score if fd is not None else 0.0
            return (score, fp)

        safe.sort(key=_score_key)
        risky.sort(key=_score_key)
        human.sort(key=_score_key)
        return safe, risky, human

    @staticmethod
    def _assert_plan_integrity(
        phases: list[PhaseFileBatch],
        actionable_files: dict[str, FileChangeCategory],
        decided_paths: set[str],
    ) -> None:
        expected = set(actionable_files.keys())
        got: set[str] = set()
        duplicates: list[str] = []
        for batch in phases:
            for fp in batch.file_paths:
                if fp in got:
                    duplicates.append(fp)
                got.add(fp)

        missing = expected - got
        ghosts = got - expected
        decided_in_plan = got & decided_paths

        if missing or ghosts or duplicates or decided_in_plan:
            problems: list[str] = []
            if missing:
                sample = sorted(missing)[:10]
                problems.append(
                    f"missing {len(missing)} actionable files (sample: {sample})"
                )
            if ghosts:
                sample = sorted(ghosts)[:10]
                problems.append(
                    f"ghost {len(ghosts)} non-actionable files in plan (sample: {sample})"
                )
            if duplicates:
                sample = sorted(set(duplicates))[:10]
                problems.append(
                    f"duplicate {len(duplicates)} file batchings (sample: {sample})"
                )
            if decided_in_plan:
                sample = sorted(decided_in_plan)[:10]
                problems.append(
                    f"already-decided {len(decided_in_plan)} files re-entered plan "
                    f"(sample: {sample})"
                )
            raise PlanIntegrityError("; ".join(problems))

    @staticmethod
    def _emit_risk_split_batches(
        phases: list[PhaseFileBatch],
        safe: list[str],
        risky: list[str],
        human: list[str],
        *,
        layer_id: int | None,
        change_category: FileChangeCategory,
    ) -> None:
        # P1-6: respect the (risk_score asc, path asc) ordering produced
        # by ``_split_by_risk_level``. Calling ``sorted()`` here would
        # collapse it back to alphabetical and undo the safest-first
        # rollout.
        if safe:
            phases.append(
                PhaseFileBatch(
                    batch_id=str(uuid4()),
                    phase=MergePhase.AUTO_MERGE,
                    file_paths=list(safe),
                    risk_level=RiskLevel.AUTO_SAFE,
                    layer_id=layer_id,
                    change_category=change_category,
                    can_parallelize=True,
                )
            )
        if risky:
            phases.append(
                PhaseFileBatch(
                    batch_id=str(uuid4()),
                    phase=MergePhase.CONFLICT_ANALYSIS,
                    file_paths=list(risky),
                    risk_level=RiskLevel.AUTO_RISKY,
                    layer_id=layer_id,
                    change_category=change_category,
                    can_parallelize=True,
                )
            )
        if human:
            phases.append(
                PhaseFileBatch(
                    batch_id=str(uuid4()),
                    phase=MergePhase.HUMAN_REVIEW,
                    file_paths=list(human),
                    risk_level=RiskLevel.HUMAN_REQUIRED,
                    layer_id=layer_id,
                    change_category=change_category,
                    can_parallelize=False,
                )
            )

    def _build_category_summary(
        self, categories: dict[str, FileChangeCategory]
    ) -> CategorySummary:
        counts: dict[FileChangeCategory, int] = {}
        for cat in FileChangeCategory:
            counts[cat] = 0
        for cat in categories.values():
            counts[cat] = counts.get(cat, 0) + 1
        return CategorySummary(
            total_files=len(categories),
            a_unchanged=counts.get(FileChangeCategory.A, 0),
            b_upstream_only=counts.get(FileChangeCategory.B, 0),
            c_both_changed=counts.get(FileChangeCategory.C, 0),
            d_missing=counts.get(FileChangeCategory.D_MISSING, 0),
            d_extra=counts.get(FileChangeCategory.D_EXTRA, 0),
            e_current_only=counts.get(FileChangeCategory.E, 0),
        )

    def _build_risk_summary(
        self,
        file_diffs: list[FileDiff],
        actionable: dict[str, FileChangeCategory],
    ) -> RiskSummary:
        auto_safe = 0
        auto_risky = 0
        human_required = 0
        deleted_only = 0
        binary = 0
        excluded = 0
        top_risk: list[str] = []

        diffs_map = {fd.file_path: fd for fd in file_diffs}

        for fp, cat in actionable.items():
            fd = diffs_map.get(fp)
            if fd is None:
                if cat == FileChangeCategory.C:
                    auto_risky += 1
                else:
                    auto_safe += 1
                continue
            rl = fd.risk_level
            if rl == RiskLevel.AUTO_SAFE:
                auto_safe += 1
            elif rl == RiskLevel.AUTO_RISKY:
                auto_risky += 1
            elif rl == RiskLevel.HUMAN_REQUIRED:
                human_required += 1
                top_risk.append(fp)
            elif rl == RiskLevel.DELETED_ONLY:
                deleted_only += 1
            elif rl == RiskLevel.BINARY:
                binary += 1
            else:
                excluded += 1

        total = len(actionable)
        auto_count = auto_safe + deleted_only
        rate = auto_count / total if total > 0 else 0.0

        return RiskSummary(
            total_files=total,
            auto_safe_count=auto_safe,
            auto_risky_count=auto_risky,
            human_required_count=human_required,
            deleted_only_count=deleted_only,
            binary_count=binary,
            excluded_count=excluded,
            estimated_auto_merge_rate=round(rate, 3),
            top_risk_files=top_risk[:10],
        )

    async def _classify_batch(
        self,
        file_diffs: list[FileDiff],
        project_context: str,
        system_prompt: str,
        batch_index: int,
        total_batches: int,
        rename_pairs: list[tuple[str, str]] | None = None,
    ) -> dict[str, Any]:
        if len(file_diffs) <= _CLASSIFY_FILE_CHUNK_SIZE:
            return await self._run_single_classify(
                file_diffs,
                project_context,
                system_prompt,
                batch_index,
                total_batches,
                rename_pairs,
            )

        # Sub-chunked path: outer batch is too big for one LLM call. Slice
        # into ``_CLASSIFY_FILE_CHUNK_SIZE`` groups, run them concurrently,
        # then re-use ``_merge_batch_plans`` (already capable of stitching
        # multiple per-file classification JSONs without loss).
        chunks = [
            file_diffs[i : i + _CLASSIFY_FILE_CHUNK_SIZE]
            for i in range(0, len(file_diffs), _CLASSIFY_FILE_CHUNK_SIZE)
        ]
        self.logger.info(
            "Classify batch %d/%d: %d files → sub-chunking into %d × ≤%d",
            batch_index + 1,
            total_batches,
            len(file_diffs),
            len(chunks),
            _CLASSIFY_FILE_CHUNK_SIZE,
        )

        async def _process(idx: int) -> dict[str, Any]:
            chunk = chunks[idx]
            chunk_paths = {fd.file_path for fd in chunk}
            scoped_renames = (
                [
                    (old, new)
                    for old, new in rename_pairs
                    if old in chunk_paths or new in chunk_paths
                ]
                if rename_pairs
                else None
            )
            return await self._run_single_classify(
                chunk,
                project_context,
                system_prompt,
                batch_index,
                total_batches,
                scoped_renames,
            )

        # U5: sub-chunks of ``_classify_batch`` partition the file list into
        # disjoint groups; assert it explicitly so a future rewrite of the
        # chunking heuristic can't silently produce overlap.
        assert_disjoint_file_shards(
            [[fd.file_path for fd in chunk] for chunk in chunks]
        )
        runner = ParallelFileRunner.from_api_key_env_list(
            self.llm_config.api_key_env_list,
            override=None,
        )
        results = await runner.run_files(list(range(len(chunks))), _process)

        sub_plans: list[dict[str, Any]] = []
        for idx in range(len(chunks)):
            result = results.get(idx)
            if isinstance(result, BaseException):
                self.logger.warning(
                    "Classify sub-chunk %d/%d crashed in runner: %s — using fallback",
                    idx + 1,
                    len(chunks),
                    result,
                )
                sub_plans.append(self._create_fallback_plan_data(chunks[idx]))
            else:
                assert isinstance(result, dict)
                sub_plans.append(result)

        return self._merge_batch_plans(sub_plans, file_diffs)

    async def _run_single_classify(
        self,
        file_diffs: list[FileDiff],
        project_context: str,
        system_prompt: str,
        batch_index: int,
        total_batches: int,
        rename_pairs: list[tuple[str, str]] | None = None,
    ) -> dict[str, Any]:
        prompt = build_classification_prompt(
            file_diffs, project_context, batch_index, total_batches, rename_pairs
        )
        file_paths = [fd.file_path for fd in file_diffs]
        memory_text = self.get_memory_context(self._current_phase, file_paths)
        if memory_text:
            prompt = f"{prompt}\n\n# Prior Knowledge\n{memory_text}"
        messages = [{"role": "user", "content": prompt}]

        try:
            raw_response = await self._call_llm_with_retry(
                messages, system=system_prompt
            )
            raw_str = str(raw_response).strip()
            if raw_str.startswith("```"):
                lines = raw_str.splitlines()
                raw_str = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
            result: dict[str, Any] = json.loads(raw_str)
            return result
        except Exception as e:
            self.logger.warning(
                "Batch %d/%d LLM/parse failed, using fallback: %s",
                batch_index + 1,
                total_batches,
                e,
            )
            return self._create_fallback_plan_data(file_diffs)

    def _merge_batch_plans(
        self,
        batch_plans: list[dict[str, Any]],
        all_file_diffs: list[FileDiff],
    ) -> dict[str, Any]:
        if len(batch_plans) == 1:
            return batch_plans[0]

        merged_phases: list[dict[str, Any]] = []
        phase_groups: dict[str, list[str]] = {}

        for plan_data in batch_plans:
            for phase in plan_data.get("phases", []):
                risk_level = phase.get("risk_level", "auto_safe")
                if risk_level not in phase_groups:
                    phase_groups[risk_level] = []
                phase_groups[risk_level].extend(phase.get("file_paths", []))

        phase_map = {
            "auto_safe": "auto_merge",
            "auto_risky": "auto_merge",
            "human_required": "human_review",
            "deleted_only": "auto_merge",
            "binary": "auto_merge",
            "excluded": "auto_merge",
        }

        for risk_level, file_paths in phase_groups.items():
            if not file_paths:
                continue
            merged_phases.append(
                {
                    "batch_id": str(uuid4()),
                    "phase": phase_map.get(risk_level, "auto_merge"),
                    "file_paths": file_paths,
                    "risk_level": risk_level,
                    "can_parallelize": risk_level != "human_required",
                }
            )

        auto_safe = len(phase_groups.get("auto_safe", []))
        auto_risky = len(phase_groups.get("auto_risky", []))
        human_required = len(phase_groups.get("human_required", []))
        deleted_only = len(phase_groups.get("deleted_only", []))
        binary = len(phase_groups.get("binary", []))
        excluded = len(phase_groups.get("excluded", []))
        total = len(all_file_diffs)
        auto_count = auto_safe + deleted_only
        rate = auto_count / total if total > 0 else 0.0

        context_summaries = [
            p.get("project_context_summary", "")
            for p in batch_plans
            if p.get("project_context_summary")
        ]
        instructions: list[str] = []
        for p in batch_plans:
            instructions.extend(p.get("special_instructions", []))

        top_risk: list[str] = []
        for p in batch_plans:
            top_risk.extend(p.get("risk_summary", {}).get("top_risk_files", []))

        return {
            "phases": merged_phases,
            "risk_summary": {
                "total_files": total,
                "auto_safe_count": auto_safe,
                "auto_risky_count": auto_risky,
                "human_required_count": human_required,
                "deleted_only_count": deleted_only,
                "binary_count": binary,
                "excluded_count": excluded,
                "estimated_auto_merge_rate": round(rate, 3),
                "top_risk_files": top_risk[:10],
            },
            "project_context_summary": context_summaries[0]
            if context_summaries
            else "",
            "special_instructions": instructions,
        }

    def _create_fallback_plan_data(self, file_diffs: list[FileDiff]) -> dict[str, Any]:
        phases = []
        auto_safe = []
        auto_risky = []
        human_required = []
        deleted_only = []
        binary = []
        excluded = []

        for fd in file_diffs:
            rl = (
                fd.risk_level.value
                if hasattr(fd.risk_level, "value")
                else fd.risk_level
            )
            if rl == "auto_safe":
                auto_safe.append(fd.file_path)
            elif rl == "auto_risky":
                auto_risky.append(fd.file_path)
            elif rl == "human_required":
                human_required.append(fd.file_path)
            elif rl == "deleted_only":
                deleted_only.append(fd.file_path)
            elif rl == "binary":
                binary.append(fd.file_path)
            else:
                excluded.append(fd.file_path)

        if auto_safe:
            phases.append(
                {
                    "batch_id": str(uuid4()),
                    "phase": "auto_merge",
                    "file_paths": auto_safe,
                    "risk_level": "auto_safe",
                    "can_parallelize": True,
                }
            )
        if auto_risky:
            phases.append(
                {
                    "batch_id": str(uuid4()),
                    "phase": "auto_merge",
                    "file_paths": auto_risky,
                    "risk_level": "auto_risky",
                    "can_parallelize": True,
                }
            )
        if human_required:
            phases.append(
                {
                    "batch_id": str(uuid4()),
                    "phase": "human_review",
                    "file_paths": human_required,
                    "risk_level": "human_required",
                    "can_parallelize": False,
                }
            )
        if deleted_only:
            phases.append(
                {
                    "batch_id": str(uuid4()),
                    "phase": "auto_merge",
                    "file_paths": deleted_only,
                    "risk_level": "deleted_only",
                    "can_parallelize": True,
                }
            )

        total = len(file_diffs)
        auto_count = len(auto_safe) + len(deleted_only)
        rate = auto_count / total if total > 0 else 0.0

        return {
            "phases": phases,
            "risk_summary": {
                "total_files": total,
                "auto_safe_count": len(auto_safe),
                "auto_risky_count": len(auto_risky),
                "human_required_count": len(human_required),
                "deleted_only_count": len(deleted_only),
                "binary_count": len(binary),
                "excluded_count": len(excluded),
                "estimated_auto_merge_rate": rate,
                "top_risk_files": human_required[:10],
            },
            "project_context_summary": "Automated fallback plan",
            "special_instructions": [],
        }

    def _build_merge_plan(
        self, plan_data: dict[str, Any], state: MergeState, file_diffs: list[FileDiff]
    ) -> MergePlan:
        phases: list[PhaseFileBatch] = []
        for batch_data in plan_data.get("phases", []):
            risk_raw = batch_data.get("risk_level", "auto_safe")
            phase_raw = batch_data.get("phase", "auto_merge")
            try:
                risk_level = RiskLevel(risk_raw)
            except ValueError:
                risk_level = RiskLevel.AUTO_SAFE
            try:
                phase = MergePhase(phase_raw)
            except ValueError:
                phase = MergePhase.AUTO_MERGE

            phases.append(
                PhaseFileBatch(
                    batch_id=batch_data.get("batch_id", str(uuid4())),
                    phase=phase,
                    file_paths=batch_data.get("file_paths", []),
                    risk_level=risk_level,
                    estimated_duration_minutes=batch_data.get(
                        "estimated_duration_minutes"
                    ),
                    can_parallelize=batch_data.get("can_parallelize", True),
                )
            )

        rs_data = plan_data.get("risk_summary", {})
        risk_summary = RiskSummary(
            total_files=int(rs_data.get("total_files", len(file_diffs))),
            auto_safe_count=int(rs_data.get("auto_safe_count", 0)),
            auto_risky_count=int(rs_data.get("auto_risky_count", 0)),
            human_required_count=int(rs_data.get("human_required_count", 0)),
            deleted_only_count=int(rs_data.get("deleted_only_count", 0)),
            binary_count=int(rs_data.get("binary_count", 0)),
            excluded_count=int(rs_data.get("excluded_count", 0)),
            estimated_auto_merge_rate=float(
                rs_data.get("estimated_auto_merge_rate", 0.0)
            ),
            top_risk_files=rs_data.get("top_risk_files", []),
        )

        merge_base = ""
        if hasattr(state, "_merge_base"):
            merge_base = state._merge_base or ""

        return MergePlan(
            created_at=datetime.now(),
            upstream_ref=state.config.upstream_ref,
            fork_ref=state.config.fork_ref,
            merge_base_commit=merge_base,
            phases=phases,
            risk_summary=risk_summary,
            project_context_summary=plan_data.get("project_context_summary", ""),
            special_instructions=plan_data.get("special_instructions", []),
        )

    async def revise_plan(
        self,
        state: MergeState,
        judge_issues: list[PlanIssue],
        lang: str = "en",
    ) -> tuple[MergePlan, list[PlannerIssueResponse], list[PlanDiffEntry]]:
        if state.merge_plan is None:
            raise ValueError("No existing plan to revise")

        old_classifications = {
            fp: batch.risk_level
            for batch in state.merge_plan.phases
            for fp in batch.file_paths
        }

        responses = await self._evaluate_judge_issues(
            state.merge_plan, judge_issues, lang
        )

        accepted_issues = [
            issue
            for issue, resp in zip(judge_issues, responses)
            if resp.action == IssueResponseAction.ACCEPT
        ]

        if accepted_issues:
            plan_data = self._apply_judge_issues_to_plan(
                state.merge_plan, accepted_issues
            )
            # Keep the classifier view in lockstep with the batch
            # placement. Otherwise the next round's deterministic
            # integrity precheck reads a stale ``fd.risk_level`` and
            # treats the just-applied escalation as a silent demotion,
            # producing an R(N) escalate / R(N+1) demote oscillation
            # that only stops at ``max_plan_revision_rounds``. Pydantic
            # ``model_copy`` keeps FileDiff immutable.
            accepted_map = {
                issue.file_path: issue.suggested_classification
                for issue in accepted_issues
            }
            state.file_diffs = [
                fd.model_copy(update={"risk_level": accepted_map[fd.file_path]})
                if (
                    fd.file_path in accepted_map
                    and fd.risk_level != accepted_map[fd.file_path]
                )
                else fd
                for fd in state.file_diffs
            ]
        else:
            plan_data = self._plan_to_data(state.merge_plan)

        file_diffs: list[FileDiff] = state.file_diffs
        plan = self._build_merge_plan(plan_data, state, file_diffs)

        new_classifications = {
            fp: batch.risk_level for batch in plan.phases for fp in batch.file_paths
        }

        diff_entries: list[PlanDiffEntry] = []
        all_fps = set(old_classifications) | set(new_classifications)
        for fp in sorted(all_fps):
            old_r = old_classifications.get(fp)
            new_r = new_classifications.get(fp)
            if old_r != new_r:
                diff_entries.append(
                    PlanDiffEntry(
                        file_path=fp,
                        old_risk=old_r.value if old_r else "removed",
                        new_risk=new_r.value if new_r else "removed",
                    )
                )

        return plan, responses, diff_entries

    async def _evaluate_judge_issues(
        self,
        plan: MergePlan,
        judge_issues: list[PlanIssue],
        lang: str = "en",
    ) -> list[PlannerIssueResponse]:
        if not judge_issues:
            return []

        prompt = build_evaluation_prompt(plan, judge_issues, lang)
        messages = [{"role": "user", "content": prompt}]

        try:
            raw = await self._call_llm_with_retry(
                messages, system=PLANNER_EVALUATION_SYSTEM
            )
            raw_str = str(raw).strip()
            if raw_str.startswith("```"):
                lines = raw_str.splitlines()
                raw_str = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
            data = json.loads(raw_str)
            raw_responses = data.get("responses", [])
        except Exception as e:
            self.logger.warning("Evaluation LLM failed, auto-accepting: %s", e)
            return [
                PlannerIssueResponse(
                    issue_id=issue.issue_id,
                    file_path=issue.file_path,
                    action=IssueResponseAction.ACCEPT,
                    reason=f"Auto-accepted (evaluation failed: {e})",
                )
                for issue in judge_issues
            ]

        issue_map = {issue.issue_id: issue for issue in judge_issues}
        responses: list[PlannerIssueResponse] = []
        seen_ids: set[str] = set()

        for resp_data in raw_responses:
            iid = resp_data.get("issue_id", "")
            if iid in seen_ids or iid not in issue_map:
                continue
            seen_ids.add(iid)

            try:
                action = IssueResponseAction(resp_data.get("action", "accept"))
            except ValueError:
                action = IssueResponseAction.ACCEPT

            responses.append(
                PlannerIssueResponse(
                    issue_id=iid,
                    file_path=resp_data.get("file_path", issue_map[iid].file_path),
                    action=action,
                    reason=resp_data.get("reason", ""),
                    counter_proposal=resp_data.get("counter_proposal"),
                )
            )

        for issue in judge_issues:
            if issue.issue_id not in seen_ids:
                responses.append(
                    PlannerIssueResponse(
                        issue_id=issue.issue_id,
                        file_path=issue.file_path,
                        action=IssueResponseAction.ACCEPT,
                        reason="No explicit response from planner — defaulting to accept",
                    )
                )

        return responses

    def _plan_to_data(self, plan: MergePlan) -> dict[str, Any]:
        return {
            "phases": [
                {
                    "batch_id": b.batch_id,
                    "phase": b.phase.value,
                    "file_paths": b.file_paths,
                    "risk_level": b.risk_level.value,
                    "can_parallelize": b.can_parallelize,
                }
                for b in plan.phases
            ],
            "risk_summary": plan.risk_summary.model_dump(mode="json"),
            "project_context_summary": plan.project_context_summary,
            "special_instructions": plan.special_instructions,
        }

    _RISK_TO_PHASE: dict[str, str] = {
        "auto_safe": "auto_merge",
        "auto_risky": "conflict_analysis",
        "human_required": "human_review",
        "deleted_only": "auto_merge",
        "binary": "auto_merge",
        "excluded": "auto_merge",
    }

    def _apply_judge_issues_to_plan(
        self, original_plan: MergePlan, judge_issues: list[PlanIssue]
    ) -> dict[str, Any]:
        reclassify: dict[str, str] = {
            issue.file_path: issue.suggested_classification.value
            for issue in judge_issues
        }

        phases_data = []
        for batch in original_plan.phases:
            new_paths = [p for p in batch.file_paths if p not in reclassify]
            if new_paths:
                phases_data.append(
                    {
                        "batch_id": batch.batch_id,
                        "phase": batch.phase.value,
                        "file_paths": new_paths,
                        "risk_level": batch.risk_level.value,
                        "can_parallelize": batch.can_parallelize,
                    }
                )

        grouped: dict[str, list[str]] = {}
        for fp, target_risk in reclassify.items():
            grouped.setdefault(target_risk, []).append(fp)

        for target_risk, fps in grouped.items():
            target_phase = self._RISK_TO_PHASE.get(target_risk, "human_review")
            can_parallel = target_risk not in ("human_required",)
            merged = False
            for batch_data in phases_data:
                if batch_data["risk_level"] == target_risk:
                    cast(list[str], batch_data["file_paths"]).extend(fps)
                    merged = True
                    break
            if not merged:
                phases_data.append(
                    {
                        "batch_id": str(uuid4()),
                        "phase": target_phase,
                        "file_paths": fps,
                        "risk_level": target_risk,
                        "can_parallelize": can_parallel,
                    }
                )

        counts: dict[str, int] = {
            "auto_safe": 0,
            "auto_risky": 0,
            "human_required": 0,
            "deleted_only": 0,
            "binary": 0,
            "excluded": 0,
        }
        total_files = 0
        top_risk: list[str] = []
        for batch_data in phases_data:
            paths = cast(list[str], batch_data["file_paths"])
            n = len(paths)
            total_files += n
            rl = cast(str, batch_data["risk_level"])
            if rl in counts:
                counts[rl] += n
            if rl == "human_required":
                top_risk.extend(paths)

        auto_count = counts["auto_safe"] + counts["deleted_only"]
        rate = auto_count / total_files if total_files > 0 else 0.0

        return {
            "phases": phases_data,
            "risk_summary": {
                "total_files": total_files,
                "auto_safe_count": counts["auto_safe"],
                "auto_risky_count": counts["auto_risky"],
                "human_required_count": counts["human_required"],
                "deleted_only_count": counts["deleted_only"],
                "binary_count": counts["binary"],
                "excluded_count": counts["excluded"],
                "estimated_auto_merge_rate": rate,
                "top_risk_files": top_risk[:20],
            },
            "project_context_summary": original_plan.project_context_summary,
            "special_instructions": original_plan.special_instructions,
        }

    async def handle_dispute(
        self,
        state: MergeState,
        dispute: Any,
    ) -> MergePlan:
        from src.models.plan_judge import PlanIssue
        from src.models.diff import RiskLevel

        issues = [
            PlanIssue(
                file_path=fp,
                current_classification=state.file_classifications.get(
                    fp, RiskLevel.AUTO_SAFE
                ),
                suggested_classification=new_level,
                reason=dispute.dispute_reason,
                issue_type="risk_underestimated",
            )
            for fp, new_level in dispute.suggested_reclassification.items()
        ]

        plan, _responses, _diff = await self.revise_plan(state, issues)
        return plan

    async def _enhance_risk_scores(
        self, file_diffs: list[FileDiff], config: MergeConfig
    ) -> list[FileDiff]:
        from src.llm.prompts.risk_scoring_prompts import (
            build_risk_scoring_prompt,
            RISK_SCORING_SYSTEM,
        )

        gray_low = config.llm_risk_scoring.gray_zone_low
        gray_high = config.llm_risk_scoring.gray_zone_high
        rule_weight = config.llm_risk_scoring.rule_weight

        enhanced_diffs = list(file_diffs)

        gray_paths = [
            fd.file_path
            for fd in enhanced_diffs
            if gray_low <= fd.risk_score <= gray_high
        ]
        memory_text = (
            self.get_memory_context(self._current_phase, gray_paths)
            if gray_paths
            else ""
        )

        for i, fd in enumerate(enhanced_diffs):
            if not (gray_low <= fd.risk_score <= gray_high):
                continue

            prompt = build_risk_scoring_prompt(fd, fd.risk_score)
            if memory_text:
                prompt = f"{prompt}\n\n# Prior Knowledge\n{memory_text}"
            messages = [{"role": "user", "content": prompt}]

            try:
                raw = await self._call_llm_with_retry(
                    messages, system=RISK_SCORING_SYSTEM
                )
                raw_str = str(raw).strip()
                if raw_str.startswith("```"):
                    lines = raw_str.splitlines()
                    raw_str = "\n".join(
                        lines[1:-1] if lines[-1] == "```" else lines[1:]
                    )
                data = json_lib.loads(raw_str)
                llm_score = max(
                    0.0,
                    min(1.0, float(data.get("llm_risk_score", fd.risk_score))),
                )
            except Exception:
                continue

            blended = rule_weight * fd.risk_score + (1.0 - rule_weight) * llm_score
            blended = round(max(0.0, min(1.0, blended)), 3)
            new_fd = fd.model_copy(update={"risk_score": blended})
            new_level = classify_file(new_fd, config.file_classifier)
            enhanced_diffs[i] = new_fd.model_copy(update={"risk_level": new_level})

        return enhanced_diffs

    def can_handle(self, state: MergeState) -> bool:
        from src.models.state import SystemStatus

        return state.status in (SystemStatus.PLANNING, SystemStatus.PLAN_REVISING)

    async def meta_review(self, state: MergeState) -> dict[str, str]:
        """Meta-review: big-picture assessment of a failed plan negotiation.

        Returns a dict with 'assessment' and 'recommendation' keys.
        Uses META-PLAN-* gates so the call is contract-compliant.
        """
        from src.llm.prompts.gate_registry import get_gate

        view = self.restricted_view(state)
        disputes_raw = [
            (d.model_dump() if hasattr(d, "model_dump") else dict(d))
            for d in (view.plan_disputes or [])
        ]
        review_log_raw = [
            (r.model_dump() if hasattr(r, "model_dump") else dict(r))
            for r in (view.plan_review_log or [])
        ]
        system = get_gate("META-PLAN-SYSTEM").render()
        prompt = get_gate("META-PLAN-REVIEW").render(
            review_log_raw,
            disputes_raw,
            len(review_log_raw),
        )
        raw = await self._call_llm_with_retry(
            [{"role": "user", "content": prompt}],
            system=system,
        )
        return _parse_meta_review_json(str(raw))

    def _classify_file(self, file_diff: FileDiff, config: MergeConfig) -> RiskLevel:
        score = compute_risk_score(file_diff, config.file_classifier)
        updated = file_diff.model_copy(update={"risk_score": score})
        return classify_file(updated, config.file_classifier)


def _parse_meta_review_json(raw: str) -> dict[str, str]:
    import json

    raw = raw.strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1:
        return {"assessment": raw[:200], "recommendation": ""}
    try:
        data = json.loads(raw[start : end + 1])
        return {
            "assessment": str(data.get("assessment", ""))[:200],
            "recommendation": str(data.get("recommendation", ""))[:200],
        }
    except Exception:
        return {"assessment": raw[:200], "recommendation": ""}


from src.agents.registry import AgentRegistry  # noqa: E402

AgentRegistry.register("planner", PlannerAgent)
