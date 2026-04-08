from datetime import datetime
from typing import Any
from uuid import uuid4
from src.agents.base_agent import BaseAgent
from src.models.config import AgentLLMConfig, MergeConfig
from src.models.message import AgentType, AgentMessage, MessageType
from src.models.plan import (
    MergePlan,
    MergePhase,
    PhaseFileBatch,
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
    PLANNER_SYSTEM,
    get_planner_system,
    build_classification_prompt,
    build_revision_prompt,
)
from src.tools.file_classifier import compute_risk_score, classify_file
import fnmatch
import json
import json as json_lib


class PlannerAgent(BaseAgent):
    agent_type = AgentType.PLANNER

    def __init__(self, llm_config: AgentLLMConfig):
        super().__init__(llm_config)

    async def run(self, state: MergeState) -> AgentMessage:
        plan = await self._generate_plan(state)
        state.merge_plan = plan
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

    async def _generate_plan(self, state: MergeState) -> MergePlan:
        all_file_diffs: list[FileDiff] = []

        if hasattr(state, "_file_diffs") and state._file_diffs:
            all_file_diffs = state._file_diffs
        else:
            all_file_diffs = []

        if state.file_categories:
            return self._build_layered_plan(all_file_diffs, state)

        if state.config.llm_risk_scoring.enabled:
            all_file_diffs = await self._enhance_risk_scores(
                all_file_diffs, state.config
            )

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
                batch, project_context, system_prompt, idx, total_batches
            )
            all_plan_data.append(plan_data)

        merged_data = self._merge_batch_plans(all_plan_data, all_file_diffs)
        return self._build_merge_plan(merged_data, state, all_file_diffs)

    def _build_layered_plan(
        self, file_diffs: list[FileDiff], state: MergeState
    ) -> MergePlan:
        layers = self._resolve_layers(state.config)
        categories = state.file_categories
        diffs_by_path = {fd.file_path: fd for fd in file_diffs}

        actionable = {
            FileChangeCategory.B,
            FileChangeCategory.C,
            FileChangeCategory.D_MISSING,
        }
        actionable_files = {
            fp: cat for fp, cat in categories.items() if cat in actionable
        }

        file_layer_map = self._assign_files_to_layers(
            list(actionable_files.keys()), layers
        )

        phases: list[PhaseFileBatch] = []

        for layer in layers:
            layer_files = file_layer_map.get(layer.layer_id, [])
            if not layer_files:
                continue

            by_category: dict[FileChangeCategory, list[str]] = {}
            for fp in layer_files:
                cat = actionable_files[fp]
                by_category.setdefault(cat, []).append(fp)

            b_files = by_category.get(FileChangeCategory.B, [])
            if b_files:
                phases.append(
                    PhaseFileBatch(
                        batch_id=str(uuid4()),
                        phase=MergePhase.AUTO_MERGE,
                        file_paths=sorted(b_files),
                        risk_level=RiskLevel.AUTO_SAFE,
                        layer_id=layer.layer_id,
                        change_category=FileChangeCategory.B,
                        can_parallelize=True,
                    )
                )

            d_files = by_category.get(FileChangeCategory.D_MISSING, [])
            if d_files:
                phases.append(
                    PhaseFileBatch(
                        batch_id=str(uuid4()),
                        phase=MergePhase.AUTO_MERGE,
                        file_paths=sorted(d_files),
                        risk_level=RiskLevel.AUTO_SAFE,
                        layer_id=layer.layer_id,
                        change_category=FileChangeCategory.D_MISSING,
                        can_parallelize=True,
                    )
                )

            c_files = by_category.get(FileChangeCategory.C, [])
            if c_files:
                c_safe = []
                c_risky = []
                c_human = []
                for fp in c_files:
                    fd = diffs_by_path.get(fp)
                    if fd is None:
                        c_risky.append(fp)
                        continue
                    if fd.risk_level == RiskLevel.HUMAN_REQUIRED:
                        c_human.append(fp)
                    elif fd.risk_level == RiskLevel.AUTO_RISKY:
                        c_risky.append(fp)
                    else:
                        c_safe.append(fp)

                if c_safe:
                    phases.append(
                        PhaseFileBatch(
                            batch_id=str(uuid4()),
                            phase=MergePhase.AUTO_MERGE,
                            file_paths=sorted(c_safe),
                            risk_level=RiskLevel.AUTO_SAFE,
                            layer_id=layer.layer_id,
                            change_category=FileChangeCategory.C,
                            can_parallelize=True,
                        )
                    )
                if c_risky:
                    phases.append(
                        PhaseFileBatch(
                            batch_id=str(uuid4()),
                            phase=MergePhase.CONFLICT_ANALYSIS,
                            file_paths=sorted(c_risky),
                            risk_level=RiskLevel.AUTO_RISKY,
                            layer_id=layer.layer_id,
                            change_category=FileChangeCategory.C,
                            can_parallelize=True,
                        )
                    )
                if c_human:
                    phases.append(
                        PhaseFileBatch(
                            batch_id=str(uuid4()),
                            phase=MergePhase.HUMAN_REVIEW,
                            file_paths=sorted(c_human),
                            risk_level=RiskLevel.HUMAN_REQUIRED,
                            layer_id=layer.layer_id,
                            change_category=FileChangeCategory.C,
                            can_parallelize=False,
                        )
                    )

        cat_summary = self._build_category_summary(categories)
        risk_summary = self._build_risk_summary(file_diffs, actionable_files)

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

        for layer in layers:
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
            if cat == FileChangeCategory.B or cat == FileChangeCategory.D_MISSING:
                auto_safe += 1
                continue
            fd = diffs_map.get(fp)
            if fd is None:
                auto_risky += 1
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
    ) -> dict[str, Any]:
        prompt = build_classification_prompt(
            file_diffs, project_context, batch_index, total_batches
        )
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
    ) -> MergePlan:
        if state.merge_plan is None:
            raise ValueError("No existing plan to revise")

        prompt = build_revision_prompt(state.merge_plan, judge_issues)
        messages = [{"role": "user", "content": prompt}]

        try:
            raw_response = await self._call_llm_with_retry(
                messages, system=PLANNER_SYSTEM
            )
            raw_str = str(raw_response).strip()
            if raw_str.startswith("```"):
                lines = raw_str.splitlines()
                raw_str = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
            plan_data = json.loads(raw_str)
        except Exception:
            plan_data = self._apply_judge_issues_to_plan(state.merge_plan, judge_issues)

        return self._build_merge_plan(plan_data, state, [])

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

        escalated = [fp for fp in reclassify if reclassify[fp] == "human_required"]
        if escalated:
            phases_data.append(
                {
                    "batch_id": str(uuid4()),
                    "phase": "human_review",
                    "file_paths": escalated,
                    "risk_level": "human_required",
                    "can_parallelize": False,
                }
            )

        rs = original_plan.risk_summary
        return {
            "phases": phases_data,
            "risk_summary": {
                "total_files": rs.total_files,
                "auto_safe_count": rs.auto_safe_count,
                "auto_risky_count": rs.auto_risky_count,
                "human_required_count": rs.human_required_count + len(escalated),
                "deleted_only_count": rs.deleted_only_count,
                "binary_count": rs.binary_count,
                "excluded_count": rs.excluded_count,
                "estimated_auto_merge_rate": rs.estimated_auto_merge_rate,
                "top_risk_files": rs.top_risk_files,
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

        return await self.revise_plan(state, issues)

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

        for i, fd in enumerate(enhanced_diffs):
            if not (gray_low <= fd.risk_score <= gray_high):
                continue

            prompt = build_risk_scoring_prompt(fd, fd.risk_score)
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

    def _classify_file(self, file_diff: FileDiff, config: MergeConfig) -> RiskLevel:
        score = compute_risk_score(file_diff, config.file_classifier)
        updated = file_diff.model_copy(update={"risk_score": score})
        return classify_file(updated, config.file_classifier)
