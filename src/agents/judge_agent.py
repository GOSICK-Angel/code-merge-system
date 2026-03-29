from datetime import datetime
from src.agents.base_agent import BaseAgent
from src.models.config import AgentLLMConfig
from src.models.message import AgentType, AgentMessage, MessageType
from src.models.plan import MergePhase
from src.models.diff import FileDiff, RiskLevel
from src.models.decision import FileDecisionRecord
from src.models.judge import JudgeVerdict, JudgeIssue, VerdictType, IssueSeverity
from src.models.state import MergeState
from src.llm.prompts.judge_prompts import JUDGE_SYSTEM, build_file_review_prompt, build_verdict_prompt
from src.llm.response_parser import parse_file_review_issues, parse_judge_verdict
from src.tools.git_tool import GitTool


class JudgeAgent(BaseAgent):
    agent_type = AgentType.JUDGE

    def __init__(self, llm_config: AgentLLMConfig, git_tool: GitTool | None = None):
        super().__init__(llm_config)
        self.git_tool = git_tool

    async def run(self, state) -> AgentMessage:
        all_issues: list[JudgeIssue] = []
        reviewed_files: list[str] = []

        file_diffs_map: dict[str, FileDiff] = {}
        if hasattr(state, "_file_diffs"):
            for fd in (state._file_diffs or []):
                file_diffs_map[fd.file_path] = fd

        high_risk_records: dict[str, FileDecisionRecord] = {}
        for fp, record in state.file_decision_records.items():
            fd = file_diffs_map.get(fp)
            if fd and fd.risk_level in (RiskLevel.HUMAN_REQUIRED, RiskLevel.AUTO_RISKY):
                high_risk_records[fp] = record
            elif fd and fd.is_security_sensitive:
                high_risk_records[fp] = record

        for file_path, record in high_risk_records.items():
            fd = file_diffs_map.get(file_path)
            if fd is None:
                continue

            merged_content = ""
            if self.git_tool is not None:
                from pathlib import Path
                abs_path = self.git_tool.repo_path / file_path
                if abs_path.exists():
                    merged_content = abs_path.read_text(encoding="utf-8")

            issues = await self.review_file(
                file_path,
                merged_content,
                record,
                fd,
                project_context=state.config.project_context if hasattr(state, "config") else "",
            )
            all_issues.extend(issues)
            reviewed_files.append(file_path)

        verdict = await self._compute_final_verdict(reviewed_files, all_issues)

        return AgentMessage(
            sender=AgentType.JUDGE,
            receiver=AgentType.ORCHESTRATOR,
            phase=MergePhase.JUDGE_REVIEW,
            message_type=MessageType.PHASE_COMPLETED,
            subject=f"Judge review completed: {verdict.verdict.value}",
            payload={"verdict": verdict.model_dump(mode="json")},
        )

    async def review_file(
        self,
        file_path: str,
        merged_content: str,
        decision_record: FileDecisionRecord,
        original_diff: FileDiff,
        project_context: str = "",
    ) -> list[JudgeIssue]:
        prompt = build_file_review_prompt(
            file_path,
            merged_content,
            decision_record,
            original_diff,
            project_context,
        )
        messages = [{"role": "user", "content": prompt}]

        try:
            raw = await self._call_llm_with_retry(messages, system=JUDGE_SYSTEM)
            issues = parse_file_review_issues(str(raw), file_path)
        except Exception as e:
            self.logger.error(f"File review failed for {file_path}: {e}")
            issues = []

        conflict_markers = ["<<<<<<<", "=======", ">>>>>>>"]
        for marker in conflict_markers:
            if marker in merged_content:
                issues.append(
                    JudgeIssue(
                        file_path=file_path,
                        issue_level=IssueSeverity.CRITICAL,
                        issue_type="unresolved_conflict",
                        description=f"Conflict marker '{marker}' found in merged content",
                        must_fix_before_merge=True,
                    )
                )
                break

        return issues

    def compute_verdict(self, all_issues: list[JudgeIssue]) -> VerdictType:
        has_critical = any(i.issue_level == IssueSeverity.CRITICAL for i in all_issues)
        has_high = any(i.issue_level == IssueSeverity.HIGH for i in all_issues)

        if has_critical or has_high:
            return VerdictType.FAIL
        if all_issues:
            return VerdictType.CONDITIONAL
        return VerdictType.PASS

    async def _compute_final_verdict(
        self, reviewed_files: list[str], all_issues: list[JudgeIssue]
    ) -> JudgeVerdict:
        critical_count = sum(1 for i in all_issues if i.issue_level == IssueSeverity.CRITICAL)
        high_count = sum(1 for i in all_issues if i.issue_level == IssueSeverity.HIGH)

        issues_summary = "\n".join(
            f"- [{i.issue_level.value}] {i.file_path}: {i.description}"
            for i in all_issues
        )

        prompt = build_verdict_prompt(reviewed_files, issues_summary, critical_count, high_count)
        messages = [{"role": "user", "content": prompt}]

        try:
            raw = await self._call_llm_with_retry(messages, system=JUDGE_SYSTEM)
            verdict = parse_judge_verdict(
                str(raw),
                reviewed_files,
                self.llm_config.model,
                all_issues,
            )
        except Exception as e:
            self.logger.error(f"Final verdict computation failed: {e}")
            verdict_type = self.compute_verdict(all_issues)
            verdict = JudgeVerdict(
                verdict=verdict_type,
                reviewed_files_count=len(reviewed_files),
                passed_files=[],
                failed_files=[],
                conditional_files=reviewed_files,
                issues=all_issues,
                critical_issues_count=critical_count,
                high_issues_count=high_count,
                overall_confidence=0.5,
                summary=f"Verdict computed with errors: {e}",
                blocking_issues=[i.issue_id for i in all_issues if i.must_fix_before_merge],
                timestamp=datetime.now(),
                judge_model=self.llm_config.model,
            )

        return verdict

    def can_handle(self, state: MergeState) -> bool:
        from src.models.state import SystemStatus
        return state.status == SystemStatus.JUDGE_REVIEWING
