from datetime import datetime
from typing import Any, cast
from uuid import uuid4
from src.agents.base_agent import BaseAgent
from src.models.config import AgentLLMConfig, MergeConfig, ModuleConfig
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
from src.tools.file_classifier import (
    compute_complexity,
    compute_risk_score,
    classify_file,
)
from src.tools.module_inference import infer_modules
from src.llm.prompts.gate_registry import get_gate
from src.core.parallel_file_runner import (
    ParallelFileRunner,
    assert_disjoint_file_shards,
)
import asyncio
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
        if state.config.llm_assist.mode != "off":
            all_file_diffs = await self._enhance_risk_scores(
                all_file_diffs,
                state.config,
                rename_pairs=state.rename_pairs or None,
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

        fallback_set = set(fallback_paths)
        path_layer: dict[str, int] = {}
        for lid, paths in file_layer_map.items():
            for fp in paths:
                path_layer[fp] = lid

        module_map, ordered_modules = self._assign_modules(
            list(actionable_files.keys()), state
        )
        files_by_module: dict[str | None, list[str]] = {}
        for fp in actionable_files:
            files_by_module.setdefault(module_map.get(fp), []).append(fp)

        def _emit(
            paths: list[str],
            cat: FileChangeCategory,
            module: str | None,
            layer_id: int | None,
        ) -> None:
            if not paths:
                return
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
                layer_id=layer_id,
                change_category=cat,
                module=module,
            )

        for module in ordered_modules:
            mod_paths = files_by_module.get(module, [])
            if not mod_paths:
                continue

            # D_MISSING first: upstream-new files (including migrations)
            # merge before the conflicted model files in the same module,
            # turning the migration-ordering hint into real batch order.
            _emit(
                [
                    fp
                    for fp in mod_paths
                    if actionable_files[fp] == FileChangeCategory.D_MISSING
                ],
                FileChangeCategory.D_MISSING,
                module,
                None,
            )

            # B/C files unassigned to any real layer (fallback bucket).
            for cat in (FileChangeCategory.B, FileChangeCategory.C):
                _emit(
                    [
                        fp
                        for fp in mod_paths
                        if fp in fallback_set and actionable_files[fp] == cat
                    ],
                    cat,
                    module,
                    None,
                )

            # B/C files per real layer, in topological layer order.
            for layer in layers:
                for cat in (FileChangeCategory.B, FileChangeCategory.C):
                    _emit(
                        [
                            fp
                            for fp in mod_paths
                            if path_layer.get(fp) == layer.layer_id
                            and actionable_files[fp] == cat
                        ],
                        cat,
                        module,
                        layer.layer_id,
                    )

        module_summary: dict[str, int] = {}
        for fp, mod in module_map.items():
            if mod is not None:
                module_summary[mod] = module_summary.get(mod, 0) + 1

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

        migration_dep = _build_migration_dependency_hint(
            file_diffs,
            migration_dir_patterns=state.config.file_classifier.migration_dir_patterns,
        )
        if migration_dep:
            special_instructions.append(migration_dep)

        return MergePlan(
            created_at=datetime.now(),
            upstream_ref=state.config.upstream_ref,
            fork_ref=state.config.fork_ref,
            merge_base_commit=merge_base,
            phases=phases,
            risk_summary=risk_summary,
            category_summary=cat_summary,
            layers=layers,
            project_context_summary=state.user_project_context or "",
            special_instructions=special_instructions,
            module_summary=module_summary,
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

    def _assign_modules(
        self, paths: list[str], state: MergeState
    ) -> tuple[dict[str, str | None], list[str | None]]:
        """Map actionable paths to module names and return the modules in
        the order their batches should be emitted. When module grouping is
        disabled every path maps to ``None`` (untagged) and the single
        ``[None]`` pass reproduces the pre-module layered plan exactly.
        """
        cfg = state.config.module_config
        if not cfg.enabled or cfg.mode == "off":
            return {fp: None for fp in paths}, [None]

        rewritten = [
            rm.path
            for rm in (
                state.forks_profile.rewritten_modules if state.forks_profile else []
            )
        ]
        module_map: dict[str, str | None] = dict(
            infer_modules(paths, cfg, rewritten or None)
        )
        ordered = self._order_modules(
            {m for m in module_map.values() if m is not None}, cfg
        )
        return module_map, list(ordered)

    @staticmethod
    def _order_modules(modules: set[str], config: ModuleConfig) -> list[str]:
        """Topologically order modules so a module's declared dependencies
        merge first; ties broken alphabetically for determinism. A
        dependency cycle falls back to alphabetical order rather than
        aborting the plan."""
        mods = sorted(modules)
        present = set(mods)
        deps = config.module_depends_on
        in_degree = {m: 0 for m in mods}
        dependents: dict[str, list[str]] = {m: [] for m in mods}
        for m in mods:
            for d in deps.get(m, []):
                if d in present:
                    in_degree[m] += 1
                    dependents[d].append(m)

        queue = sorted(m for m in mods if in_degree[m] == 0)
        out: list[str] = []
        while queue:
            m = queue.pop(0)
            out.append(m)
            for child in sorted(dependents[m]):
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        if len(out) != len(mods):
            return mods
        return out

    def _matches_layer(self, file_path: str, patterns: list[str]) -> bool:
        for pattern in patterns:
            if fnmatch.fnmatch(file_path, pattern):
                return True
            # `**/foo` is meant to mean "foo at any depth, including
            # the repo root". Python's stdlib fnmatch treats `**` as a
            # plain `*` and additionally requires the literal `/` to
            # match — so `**/go.mod` matches `sub/go.mod` but NOT the
            # root-level `go.mod`. Re-try the pattern's trailing tail
            # (after the leading `**/`) directly against the file path
            # so root-level lockfiles / manifests land in L1 instead of
            # falling through to the L2 catch-all.
            if pattern.startswith("**/") and fnmatch.fnmatch(file_path, pattern[3:]):
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
        module: str | None = None,
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
                    module=module,
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
                    module=module,
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
                    module=module,
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
            state.merge_plan, judge_issues, lang, file_diffs=state.file_diffs
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
        file_diffs: list[FileDiff] | None = None,
    ) -> list[PlannerIssueResponse]:
        if not judge_issues:
            return []

        prompt = build_evaluation_prompt(
            plan, judge_issues, lang, file_diffs=file_diffs
        )
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
        self,
        file_diffs: list[FileDiff],
        config: MergeConfig,
        rename_pairs: list[tuple[str, str]] | None = None,
    ) -> list[FileDiff]:
        """Spend LLM calls to refine the deterministic plan, complexity-driven.

        Tier selection is governed by ``compute_complexity`` rather than a
        per-file flag: files in the uncertainty band get a single-file
        rescore (tier 2), files above it get a full batch re-classification
        (tier 3, the strong-judgment layer). ``budget_max_files`` caps the
        combined set by descending complexity so the most uncertain files
        are served first and tier 3 is never starved by the cut.
        """
        assist = config.llm_assist
        if assist.mode == "off":
            return list(file_diffs)

        enhanced_diffs = list(file_diffs)
        low, high = assist.uncertainty_low, assist.uncertainty_high
        complexity = [
            compute_complexity(fd, config.complexity) for fd in enhanced_diffs
        ]

        tier3 = {i for i, c in enumerate(complexity) if c > high}
        if assist.mode == "always":
            tier2 = {i for i in range(len(enhanced_diffs)) if i not in tier3}
        else:  # auto
            tier2 = {i for i, c in enumerate(complexity) if low <= c <= high}

        selected = tier2 | tier3
        if not selected:
            return enhanced_diffs

        if len(selected) > assist.budget_max_files:
            selected = set(
                sorted(selected, key=lambda i: complexity[i], reverse=True)[
                    : assist.budget_max_files
                ]
            )
            tier3 = {i for i in selected if complexity[i] > high}
            tier2 = selected - tier3

        self.logger.info(
            "LLM assist (%s): %d files (tier2 rescore=%d, tier3 reclassify=%d), "
            "budget=%d",
            assist.mode,
            len(selected),
            len(tier2),
            len(tier3),
            assist.budget_max_files,
        )

        if tier2:
            enhanced_diffs = await self._rescore_files(
                enhanced_diffs, sorted(tier2), config
            )
        if tier3:
            enhanced_diffs = await self._reclassify_files(
                enhanced_diffs, sorted(tier3), config, rename_pairs
            )
        return enhanced_diffs

    async def _rescore_files(
        self,
        enhanced_diffs: list[FileDiff],
        indices: list[int],
        config: MergeConfig,
    ) -> list[FileDiff]:
        """Tier 2: blend a single-file LLM risk score into the rule score."""
        rule_weight = config.llm_assist.rule_weight
        result = list(enhanced_diffs)
        paths = [result[i].file_path for i in indices]
        memory_text = self.get_memory_context(self._current_phase, paths)
        total = len(indices)
        progress_lock = asyncio.Lock()

        async def _rescore_one(idx: int) -> float | None:
            fd = result[idx]
            prompt = get_gate("P-RISK-SCORE").render(fd, fd.risk_score)
            if memory_text:
                prompt = f"{prompt}\n\n# Prior Knowledge\n{memory_text}"
            messages = [{"role": "user", "content": prompt}]
            llm_score: float | None
            try:
                raw = await self._call_llm_with_retry(
                    messages, system=get_gate("P-RISK-SCORE-SYSTEM").render()
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
            except Exception as exc:
                self.logger.debug("risk rescore failed for %s: %s", fd.file_path, exc)
                llm_score = None
            await self._emit_rescore_progress(
                progress_lock, fd.file_path, total, llm_score is not None
            )
            return llm_score

        runner = ParallelFileRunner.from_api_key_env_list(
            self.llm_config.api_key_env_list,
            override=None,
        )
        results = await runner.run_files(indices, _rescore_one)

        for idx in indices:
            scored = results.get(idx)
            if isinstance(scored, BaseException) or scored is None:
                continue
            fd = result[idx]
            blended = rule_weight * fd.risk_score + (1.0 - rule_weight) * scored
            blended = round(max(0.0, min(1.0, blended)), 3)
            new_fd = fd.model_copy(update={"risk_score": blended})
            new_level = classify_file(new_fd, config.file_classifier)
            result[idx] = new_fd.model_copy(update={"risk_level": new_level})

        return result

    async def _reclassify_files(
        self,
        enhanced_diffs: list[FileDiff],
        indices: list[int],
        config: MergeConfig,
        rename_pairs: list[tuple[str, str]] | None,
    ) -> list[FileDiff]:
        """Tier 3 (strong judgment): run the batch classification prompt on
        the most complex files and let its categorical risk_level override
        the rule classification — no blend, the LLM decision wins."""
        tier3_diffs = [enhanced_diffs[i] for i in indices]
        system_prompt = get_planner_system(config.output.language)
        tier3_paths = {fd.file_path for fd in tier3_diffs}
        scoped_renames = (
            [
                (old, new)
                for old, new in rename_pairs
                if old in tier3_paths or new in tier3_paths
            ]
            if rename_pairs
            else None
        )
        plan_data = await self._classify_batch(
            tier3_diffs,
            config.project_context,
            system_prompt,
            0,
            1,
            scoped_renames,
        )

        risk_by_path: dict[str, RiskLevel] = {}
        for phase in plan_data.get("phases", []):
            try:
                rl = RiskLevel(phase.get("risk_level", "auto_safe"))
            except ValueError:
                rl = RiskLevel.AUTO_SAFE
            for fp in phase.get("file_paths", []):
                risk_by_path[fp] = rl

        result = list(enhanced_diffs)
        for i in indices:
            fd = result[i]
            new_level = risk_by_path.get(fd.file_path)
            if new_level is not None and new_level != fd.risk_level:
                result[i] = fd.model_copy(update={"risk_level": new_level})
        return result

    async def _emit_rescore_progress(
        self,
        lock: "asyncio.Lock",
        file_path: str,
        total: int,
        ok: bool,
    ) -> None:
        """Notify any registered activity callback that one more gray-zone
        rescore completed. Counter is incremented under a lock so the
        ``completed`` field is monotonic in the face of the parallel
        runner's concurrent callbacks.
        """
        if self._on_activity is None:
            return
        async with lock:
            self._rescore_completed = getattr(self, "_rescore_completed", 0) + 1
            done = self._rescore_completed
            if done >= total:
                self._rescore_completed = 0
        from src.core.phases.base import ActivityEvent

        self._on_activity(
            ActivityEvent(
                agent=self.agent_type.value,
                action="llm_risk_rescore",
                phase=self._current_phase,
                event_type="progress",
                extra={
                    "completed": done,
                    "total": total,
                    "file_path": file_path,
                    "ok": ok,
                },
            )
        )

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


def _build_migration_dependency_hint(
    file_diffs: list[FileDiff],
    migration_dir_patterns: list[str] | None = None,
) -> str:
    """Return a special_instruction when migration files and both_changed
    model files share a common directory prefix.

    DB migrations must be applied before the model code that references the
    new schema columns.  When the plan contains upstream-new migration files
    (D-missing) alongside fork-conflicted model files (C-class) in the same
    top-level package directory, an explicit ordering reminder is emitted so
    the Executor and human reviewers know to merge migrations first.

    ``migration_dir_patterns`` is a list of path substrings (case-insensitive)
    that identify migration directories.  Defaults come from
    ``FileClassifierConfig.migration_dir_patterns``; callers pass
    ``state.config.file_classifier.migration_dir_patterns`` so projects can
    extend the list via config.yaml without touching production code.
    """
    patterns = [
        p.lower() for p in (migration_dir_patterns or ["migrations/", "alembic/"])
    ]

    migration_dirs: set[str] = set()
    conflict_dirs: set[str] = set()

    for fd in file_diffs:
        parts = fd.file_path.split("/")
        top_dir = parts[0] if len(parts) > 1 else ""
        path_lower = fd.file_path.lower()
        if fd.change_category == FileChangeCategory.D_MISSING and any(
            pat in path_lower for pat in patterns
        ):
            migration_dirs.add(top_dir)
        elif fd.change_category == FileChangeCategory.C:
            conflict_dirs.add(top_dir)

    overlapping = migration_dirs & conflict_dirs
    if not overlapping:
        return ""

    dirs_str = ", ".join(sorted(overlapping))
    return (
        f"ORDERING DEPENDENCY: upstream-new migration file(s) and fork-conflicted "
        f"model file(s) share the same top-level package(s): [{dirs_str}]. "
        "Apply migration batches BEFORE merging the conflicted model files — "
        "the migrations introduce DB schema changes that the model code depends on."
    )


from src.agents.registry import AgentRegistry  # noqa: E402

AgentRegistry.register("planner", PlannerAgent)
