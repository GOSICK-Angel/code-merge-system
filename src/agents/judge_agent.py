import fnmatch
import re
from datetime import datetime
from src.agents.base_agent import BaseAgent
from src.models.config import AgentLLMConfig
from src.models.message import AgentType, AgentMessage, MessageType
from src.models.plan import MergePhase
from src.models.diff import FileDiff, RiskLevel
from src.models.decision import FileDecisionRecord
from src.models.judge import (
    JudgeVerdict,
    JudgeIssue,
    RepairInstruction,
    CustomizationViolation,
    VerdictType,
    IssueSeverity,
)
from src.models.config import CustomizationEntry, CustomizationVerification
from src.models.diff import FileChangeCategory
from src.models.state import MergeState
from src.llm.prompt_builders import AgentPromptBuilder
from src.core.read_only_state_view import ReadOnlyStateView
from src.llm.prompts.judge_prompts import (
    JUDGE_SYSTEM,
    build_file_review_prompt,
    build_verdict_prompt,
)
from src.llm.response_parser import parse_file_review_issues, parse_judge_verdict
from src.tools.git_tool import GitTool
from src.tools.three_way_diff import ThreeWayDiff
from src.tools.syntax_checker import check_syntax as check_file_syntax


class JudgeAgent(BaseAgent):
    agent_type = AgentType.JUDGE

    def __init__(self, llm_config: AgentLLMConfig, git_tool: GitTool | None = None):
        super().__init__(llm_config)
        self.git_tool = git_tool

    async def run(self, state: ReadOnlyStateView) -> AgentMessage:
        all_issues: list[JudgeIssue] = []
        reviewed_files: list[str] = []

        file_diffs_map: dict[str, FileDiff] = {}
        if hasattr(state, "_file_diffs"):
            for fd in state._file_diffs or []:
                file_diffs_map[fd.file_path] = fd

        deterministic_issues = self._run_deterministic_pipeline(state, file_diffs_map)
        all_issues.extend(deterministic_issues)

        deterministic_veto_files = {
            i.file_path for i in deterministic_issues if i.veto_condition
        }

        high_risk_records: dict[str, FileDecisionRecord] = {}
        for fp, record in state.file_decision_records.items():
            if fp in deterministic_veto_files:
                continue
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
                abs_path = self.git_tool.repo_path / file_path
                if abs_path.exists():
                    merged_content = abs_path.read_text(encoding="utf-8")

            issues = await self.review_file(
                file_path,
                merged_content,
                record,
                fd,
                project_context=state.config.project_context,
            )
            all_issues.extend(issues)
            reviewed_files.append(file_path)

        reviewed_files.extend(deterministic_veto_files)
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
        issues: list[JudgeIssue] = []

        syntax_result = check_file_syntax(file_path, merged_content)
        if not syntax_result.valid:
            for syn_err in syntax_result.errors:
                issues.append(
                    JudgeIssue(
                        file_path=file_path,
                        issue_level=IssueSeverity.CRITICAL,
                        issue_type="syntax_error",
                        description=(
                            f"Syntax error at line {syn_err.line}, "
                            f"col {syn_err.column}: {syn_err.message}"
                        ),
                        affected_lines=[syn_err.line] if syn_err.line > 0 else [],
                        must_fix_before_merge=True,
                    )
                )

        memory_context = ""
        max_content_chars: int | None = None
        if self._memory_store:
            builder = AgentPromptBuilder(self.llm_config, self._memory_store)
            memory_context = builder.build_memory_context_text([file_path])
            max_content_chars = builder.compute_content_budget(
                JUDGE_SYSTEM + memory_context
            )

            diff_ranges = _extract_diff_ranges(original_diff)
            budget_tokens = max_content_chars // 4 if max_content_chars else 2000
            if merged_content:
                merged_content = builder.build_staged_content(
                    merged_content,
                    file_path,
                    diff_ranges,
                    budget_tokens,
                )

        prompt = build_file_review_prompt(
            file_path,
            merged_content,
            decision_record,
            original_diff,
            project_context,
            max_content_chars=max_content_chars,
            memory_context=memory_context,
        )
        messages = [{"role": "user", "content": prompt}]

        try:
            raw = await self._call_llm_with_retry(messages, system=JUDGE_SYSTEM)
            llm_issues = parse_file_review_issues(str(raw), file_path)
            issues.extend(llm_issues)
        except Exception as e:
            self.logger.error(f"File review failed for {file_path}: {e}")

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

    def _run_deterministic_pipeline(
        self,
        state: ReadOnlyStateView,
        file_diffs_map: dict[str, FileDiff],
    ) -> list[JudgeIssue]:
        if self.git_tool is None:
            return []

        issues: list[JudgeIssue] = []
        three_way = ThreeWayDiff(self.git_tool)

        categories: dict[str, FileChangeCategory] = state.file_categories or {}

        merge_base = state.merge_base_commit or ""
        upstream_ref = state.config.upstream_ref

        if not merge_base or not upstream_ref:
            return []

        todo_merge_total = 0

        for fp, cat in categories.items():
            if cat == FileChangeCategory.B:
                if not three_way.verify_b_class(fp, upstream_ref):
                    issues.append(
                        JudgeIssue(
                            file_path=fp,
                            issue_level=IssueSeverity.CRITICAL,
                            issue_type="b_class_mismatch",
                            description=(
                                "B-class file differs from upstream after merge"
                            ),
                            must_fix_before_merge=True,
                            veto_condition="B-class file differs from upstream",
                        )
                    )

            elif cat == FileChangeCategory.D_MISSING:
                if not three_way.verify_d_missing_present(fp):
                    issues.append(
                        JudgeIssue(
                            file_path=fp,
                            issue_level=IssueSeverity.CRITICAL,
                            issue_type="d_missing_absent",
                            description="D-missing file not present in HEAD after merge",
                            must_fix_before_merge=True,
                            veto_condition="D-missing file not present in HEAD",
                        )
                    )

            elif cat == FileChangeCategory.C:
                additions = three_way.extract_upstream_additions(
                    fp, merge_base, upstream_ref
                )
                if additions:
                    missing = three_way.verify_additions_present(fp, additions)
                    if missing:
                        issues.append(
                            JudgeIssue(
                                file_path=fp,
                                issue_level=IssueSeverity.HIGH,
                                issue_type="missing_upstream_addition",
                                description=(
                                    f"Upstream additions missing in merged: "
                                    f"{', '.join(missing[:5])}"
                                    f"{'...' if len(missing) > 5 else ''}"
                                ),
                                must_fix_before_merge=True,
                                veto_condition=(
                                    "Upstream function block missing in merged"
                                    if any(
                                        self._is_large_addition(
                                            fp, name, merge_base, upstream_ref
                                        )
                                        for name in missing
                                    )
                                    else None
                                ),
                            )
                        )

            todo_check_lines = three_way.find_todo_check(fp)
            if todo_check_lines:
                issues.append(
                    JudgeIssue(
                        file_path=fp,
                        issue_level=IssueSeverity.CRITICAL,
                        issue_type="prohibited_todo_check",
                        description=(
                            f"Unannotated TODO [check] at lines: "
                            f"{todo_check_lines[:10]}"
                        ),
                        affected_lines=todo_check_lines[:10],
                        must_fix_before_merge=True,
                        veto_condition="Unannotated TODO [check] exists",
                    )
                )

            todo_merge_total += three_way.count_todo_merge(fp)

        if todo_merge_total > 30:
            issues.append(
                JudgeIssue(
                    file_path="(global)",
                    issue_level=IssueSeverity.CRITICAL,
                    issue_type="todo_merge_exceeded",
                    description=(
                        f"TODO [merge] count ({todo_merge_total}) "
                        f"exceeds phase limit (30)"
                    ),
                    must_fix_before_merge=True,
                    veto_condition="TODO [merge] count exceeds phase limit",
                )
            )

        return issues

    def _is_large_addition(
        self,
        file_path: str,
        symbol_name: str,
        merge_base: str,
        upstream_ref: str,
    ) -> bool:
        if self.git_tool is None:
            return False
        base_content = self.git_tool.get_file_content(merge_base, file_path) or ""
        upstream_content = self.git_tool.get_file_content(upstream_ref, file_path) or ""

        if symbol_name in base_content:
            return False

        pattern = re.compile(
            rf"(?:def|class|function)\s+{re.escape(symbol_name)}\b",
            re.MULTILINE,
        )
        match = pattern.search(upstream_content)
        if not match:
            return False

        start = match.start()
        remaining = upstream_content[start:]
        lines = remaining.split("\n")
        return len(lines) > 20

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
        critical_count = sum(
            1 for i in all_issues if i.issue_level == IssueSeverity.CRITICAL
        )
        high_count = sum(1 for i in all_issues if i.issue_level == IssueSeverity.HIGH)

        issues_summary = "\n".join(
            f"- [{i.issue_level.value}] {i.file_path}: {i.description}"
            for i in all_issues
        )

        prompt = build_verdict_prompt(
            reviewed_files, issues_summary, critical_count, high_count
        )
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
                blocking_issues=[
                    i.issue_id for i in all_issues if i.must_fix_before_merge
                ],
                timestamp=datetime.now(),
                judge_model=self.llm_config.model,
            )

        return verdict

    def verify_customizations(
        self,
        customizations: list[CustomizationEntry],
    ) -> list[CustomizationViolation]:
        if not self.git_tool or not customizations:
            return []

        violations: list[CustomizationViolation] = []

        for entry in customizations:
            for verif in entry.verification:
                violation: CustomizationViolation | None = None
                if verif.type == "grep":
                    violation = self._verify_grep(entry.name, verif)
                elif verif.type == "file_exists":
                    violation = self._verify_file_exists(entry.name, verif)
                elif verif.type == "function_exists":
                    violation = self._verify_function_exists(entry.name, verif)
                if violation:
                    violations.append(violation)

        return violations

    def _verify_grep(
        self,
        customization_name: str,
        verif: CustomizationVerification,
    ) -> CustomizationViolation | None:
        if not self.git_tool or not verif.pattern:
            return None

        results = self.git_tool.grep_in_files(verif.pattern, verif.files)
        total_matches = sum(len(m) for m in results.values())
        checked = list(results.keys()) if results else []

        if not checked and verif.files:
            all_files = [
                str(p.relative_to(self.git_tool.repo_path))
                for p in self.git_tool.repo_path.rglob("*")
                if p.is_file()
            ]
            for pat in verif.files:
                for fp in all_files:
                    if fnmatch.fnmatch(fp, pat):
                        checked.append(fp)

        if total_matches == 0:
            return CustomizationViolation(
                customization_name=customization_name,
                verification_type="grep",
                expected_pattern=verif.pattern,
                checked_files=checked,
                match_count=0,
            )
        return None

    def _verify_file_exists(
        self,
        customization_name: str,
        verif: CustomizationVerification,
    ) -> CustomizationViolation | None:
        if not self.git_tool:
            return None

        for fp in verif.files:
            abs_path = self.git_tool.repo_path / fp
            if not abs_path.exists():
                return CustomizationViolation(
                    customization_name=customization_name,
                    verification_type="file_exists",
                    expected_pattern=fp,
                    checked_files=[fp],
                    match_count=0,
                )
        return None

    def _verify_function_exists(
        self,
        customization_name: str,
        verif: CustomizationVerification,
    ) -> CustomizationViolation | None:
        if not self.git_tool or not verif.pattern:
            return None

        func_pattern = rf"(def|function|class|const|let|var)\s+{verif.pattern}"
        results = self.git_tool.grep_in_files(func_pattern, verif.files)
        total_matches = sum(len(m) for m in results.values())

        checked: list[str] = []
        if verif.files:
            all_files = [
                str(p.relative_to(self.git_tool.repo_path))
                for p in self.git_tool.repo_path.rglob("*")
                if p.is_file()
            ]
            for pat in verif.files:
                for fp in all_files:
                    if fnmatch.fnmatch(fp, pat):
                        checked.append(fp)

        if total_matches == 0:
            return CustomizationViolation(
                customization_name=customization_name,
                verification_type="function_exists",
                expected_pattern=verif.pattern,
                checked_files=checked,
                match_count=0,
            )
        return None

    def build_repair_instructions(
        self, issues: list[JudgeIssue]
    ) -> list[RepairInstruction]:
        instructions: list[RepairInstruction] = []
        for issue in issues:
            if not issue.must_fix_before_merge:
                continue

            repairable = issue.issue_type in (
                "syntax_error",
                "unresolved_conflict",
                "missing_upstream_addition",
            )

            instructions.append(
                RepairInstruction(
                    file_path=issue.file_path,
                    instruction=issue.suggested_fix or issue.description,
                    severity=issue.issue_level,
                    is_repairable=repairable,
                    source_issue_id=issue.issue_id,
                )
            )
        return instructions

    def can_handle(self, state: MergeState) -> bool:
        from src.models.state import SystemStatus

        return state.status == SystemStatus.JUDGE_REVIEWING


def _extract_diff_ranges(original_diff: FileDiff) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    if original_diff.hunks:
        for hunk in original_diff.hunks:
            ranges.append((hunk.start_line_current, hunk.end_line_current))
    elif original_diff.lines_added > 0 or original_diff.lines_deleted > 0:
        ranges.append(
            (1, original_diff.lines_added + original_diff.lines_deleted + 100)
        )
    return ranges


from src.agents.registry import AgentRegistry  # noqa: E402

AgentRegistry.register("judge", JudgeAgent, extra_kwargs=["git_tool"])
