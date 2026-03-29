from datetime import datetime
from uuid import uuid4
from src.agents.base_agent import BaseAgent
from src.models.config import AgentLLMConfig, MergeConfig
from src.models.message import AgentType, AgentMessage, MessageType
from src.models.plan import MergePlan, MergePhase, PhaseFileBatch, RiskSummary
from src.models.diff import FileDiff, RiskLevel
from src.models.plan_judge import PlanIssue
from src.models.state import MergeState
from src.llm.prompts.planner_prompts import (
    PLANNER_SYSTEM,
    build_classification_prompt,
    build_revision_prompt,
    build_context_summary_prompt,
)
from src.llm.response_parser import ParseError
from src.tools.file_classifier import compute_risk_score, classify_file, is_security_sensitive
import json


class PlannerAgent(BaseAgent):
    agent_type = AgentType.PLANNER

    def __init__(self, llm_config: AgentLLMConfig):
        super().__init__(llm_config)

    async def run(self, state: MergeState) -> AgentMessage:
        plan = await self._generate_plan(state)
        state.merge_plan = plan
        state.file_classifications = {
            fp: batch.risk_level
            for batch in plan.phases
            for fp in batch.file_paths
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
        file_diffs = list(state.conflict_analyses.values()) if state.conflict_analyses else []
        all_file_diffs: list[FileDiff] = []

        if hasattr(state, "_file_diffs") and state._file_diffs:
            all_file_diffs = state._file_diffs
        else:
            all_file_diffs = []

        project_context = state.config.project_context

        prompt = build_classification_prompt(all_file_diffs, project_context)
        messages = [{"role": "user", "content": prompt}]

        try:
            raw_response = await self._call_llm_with_retry(messages, system=PLANNER_SYSTEM)
            raw_str = str(raw_response)

            raw_str_clean = raw_str.strip()
            if raw_str_clean.startswith("```"):
                lines = raw_str_clean.splitlines()
                raw_str_clean = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
            plan_data = json.loads(raw_str_clean)

        except Exception:
            plan_data = self._create_fallback_plan_data(all_file_diffs)

        return self._build_merge_plan(plan_data, state, all_file_diffs)

    def _create_fallback_plan_data(self, file_diffs: list[FileDiff]) -> dict:
        phases = []
        auto_safe = []
        auto_risky = []
        human_required = []
        deleted_only = []
        binary = []
        excluded = []

        for fd in file_diffs:
            rl = fd.risk_level.value if hasattr(fd.risk_level, "value") else fd.risk_level
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
            phases.append({
                "batch_id": str(uuid4()),
                "phase": "auto_merge",
                "file_paths": auto_safe,
                "risk_level": "auto_safe",
                "can_parallelize": True,
            })
        if auto_risky:
            phases.append({
                "batch_id": str(uuid4()),
                "phase": "auto_merge",
                "file_paths": auto_risky,
                "risk_level": "auto_risky",
                "can_parallelize": True,
            })
        if human_required:
            phases.append({
                "batch_id": str(uuid4()),
                "phase": "human_review",
                "file_paths": human_required,
                "risk_level": "human_required",
                "can_parallelize": False,
            })
        if deleted_only:
            phases.append({
                "batch_id": str(uuid4()),
                "phase": "auto_merge",
                "file_paths": deleted_only,
                "risk_level": "deleted_only",
                "can_parallelize": True,
            })

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
        self, plan_data: dict, state: MergeState, file_diffs: list[FileDiff]
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
                    estimated_duration_minutes=batch_data.get("estimated_duration_minutes"),
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
            estimated_auto_merge_rate=float(rs_data.get("estimated_auto_merge_rate", 0.0)),
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
            raw_response = await self._call_llm_with_retry(messages, system=PLANNER_SYSTEM)
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
    ) -> dict:
        reclassify: dict[str, str] = {
            issue.file_path: issue.suggested_classification.value
            for issue in judge_issues
        }

        phases_data = []
        for batch in original_plan.phases:
            new_paths = [p for p in batch.file_paths if p not in reclassify]
            if new_paths:
                phases_data.append({
                    "batch_id": batch.batch_id,
                    "phase": batch.phase.value,
                    "file_paths": new_paths,
                    "risk_level": batch.risk_level.value,
                    "can_parallelize": batch.can_parallelize,
                })

        escalated = [fp for fp in reclassify if reclassify[fp] == "human_required"]
        if escalated:
            phases_data.append({
                "batch_id": str(uuid4()),
                "phase": "human_review",
                "file_paths": escalated,
                "risk_level": "human_required",
                "can_parallelize": False,
            })

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
        dispute,
    ) -> MergePlan:
        from src.models.plan_judge import PlanIssue
        from src.models.diff import RiskLevel

        issues = [
            PlanIssue(
                file_path=fp,
                current_classification=state.file_classifications.get(fp, RiskLevel.AUTO_SAFE),
                suggested_classification=new_level,
                reason=dispute.dispute_reason,
                issue_type="risk_underestimated",
            )
            for fp, new_level in dispute.suggested_reclassification.items()
        ]

        return await self.revise_plan(state, issues)

    def can_handle(self, state: MergeState) -> bool:
        from src.models.state import SystemStatus
        return state.status in (SystemStatus.PLANNING, SystemStatus.PLAN_REVISING)

    def _classify_file(self, file_diff: FileDiff, config: MergeConfig) -> RiskLevel:
        score = compute_risk_score(file_diff, config.file_classifier)
        updated = file_diff.model_copy(update={"risk_score": score})
        return classify_file(updated, config.file_classifier)
