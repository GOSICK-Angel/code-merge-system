import fnmatch
import re
from datetime import datetime
from src.agents.base_agent import BaseAgent
from src.core.parallel_file_runner import ParallelFileRunner
from src.models.config import AgentLLMConfig
from src.models.message import AgentType, AgentMessage, MessageType
from src.models.plan import MergePhase
from src.models.diff import FileDiff, RiskLevel
from src.models.decision import FileDecisionRecord, MergeDecision
from src.models.judge import (
    BatchVerdict,
    DisputePoint,
    ExecutorRebuttal,
    JudgeCheckStrategy,
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
from src.llm.response_parser import (
    parse_batch_file_review_issues,
    parse_file_review_issues,
    parse_judge_verdict,
)
from src.tools.git_tool import GitTool
from src.tools.three_way_diff import ThreeWayDiff, _safe_read_text
from src.tools.syntax_checker import check_syntax as check_file_syntax


class JudgeAgent(BaseAgent):
    agent_type = AgentType.JUDGE
    contract_name = "judge"

    def __init__(self, llm_config: AgentLLMConfig, git_tool: GitTool | None = None):
        super().__init__(llm_config)
        self.git_tool = git_tool

    async def run(self, state: ReadOnlyStateView) -> AgentMessage:
        state = self.restricted_view(state)
        all_issues: list[JudgeIssue] = []
        reviewed_files: list[str] = []

        file_diffs_map: dict[str, FileDiff] = {}
        for fd in state.file_diffs:
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

        # O-J1: skip per-file LLM review for high-confidence records whose
        # merged content parses cleanly. Security-sensitive files always stay
        # in the LLM path regardless of confidence.
        skip_enabled = getattr(state.config, "judge_skip_high_confidence", False)
        skip_threshold = getattr(state.config, "judge_skip_confidence_threshold", 0.9)
        if skip_enabled and high_risk_records:
            skipped: list[str] = []
            for fp in list(high_risk_records.keys()):
                record = high_risk_records[fp]
                fd = file_diffs_map.get(fp)
                if fd and fd.is_security_sensitive:
                    continue
                if record.confidence is None or record.confidence < skip_threshold:
                    continue
                if not self._local_syntax_ok(fp):
                    continue
                skipped.append(fp)
                del high_risk_records[fp]
                reviewed_files.append(fp)
            if skipped:
                self.logger.info(
                    "Judge skipped %d high-confidence file(s) (threshold=%.2f, local syntax OK)",
                    len(skipped),
                    skip_threshold,
                )

        # O-M1: on dispute rounds, group previous round's issues by file so
        # the Judge prompt can include them as a "<prior_review>" block.
        prior_issues_by_file: dict[str, list[JudgeIssue]] = {}
        _prior_verdict = getattr(state, "judge_verdict", None)
        if (getattr(state, "judge_repair_rounds", 0) or 0) > 0 and _prior_verdict:
            for _pi in _prior_verdict.issues:
                prior_issues_by_file.setdefault(_pi.file_path, []).append(_pi)

        async def _review_one(file_path: str) -> list[JudgeIssue]:
            record = high_risk_records[file_path]
            fd = file_diffs_map.get(file_path)
            if fd is None:
                return []
            merged_content = ""
            if self.git_tool is not None:
                abs_path = self.git_tool.repo_path / file_path
                if abs_path.exists():
                    merged_content = _safe_read_text(abs_path) or ""
            check_strategy = _resolve_check_strategy(
                file_path,
                record,
                state.config.customization_path_patterns,
            )
            return await self.review_file(
                file_path,
                merged_content,
                record,
                fd,
                project_context=state.config.project_context,
                check_strategy=check_strategy,
                prior_round_issues=prior_issues_by_file.get(file_path, []),
            )

        runner = ParallelFileRunner.from_api_key_env_list(
            self.llm_config.api_key_env_list,
            override=state.config.parallel_file_concurrency,
        )
        file_issues = await runner.run_files(
            list(high_risk_records.keys()), _review_one
        )
        for fp, result in file_issues.items():
            if isinstance(result, BaseException):
                self.logger.error("Parallel judge review failed for %s: %s", fp, result)
                continue
            all_issues.extend(result)
            reviewed_files.append(fp)

        reviewed_files.extend(deterministic_veto_files)

        # O-J2: in dispute rounds, narrow the Judge's scope to "did the
        # Executor close the previously-reported issues?". New issues that
        # surface on re-review are logged but do not gate the verdict —
        # they roll up to meta-review as out-of-scope observations.
        freeze_enabled = getattr(state.config, "judge_freeze_prior_issues", False)
        dispute_round = getattr(state, "judge_repair_rounds", 0) or 0
        prior_verdict = getattr(state, "judge_verdict", None)
        if (
            freeze_enabled
            and dispute_round > 0
            and prior_verdict is not None
            and prior_verdict.issues
        ):
            all_issues = self._freeze_to_prior_issues(all_issues, prior_verdict.issues)

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
        check_strategy: JudgeCheckStrategy = JudgeCheckStrategy.UPSTREAM_MATCH,
        prior_round_issues: list[JudgeIssue] | None = None,
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

        # O-M1: dispute-round prior review block. Append before LLM call so
        # the Judge knows which issues were already reported and can focus on
        # whether the Executor's repair closed them.
        if prior_round_issues:
            prior_lines = [
                f"- [{pi.issue_level.value}] {pi.issue_type}: {pi.description}"
                for pi in prior_round_issues[:10]
            ]
            prior_block = (
                "\n\n<prior_review>\n"
                "Previous round flagged the following issues on this file. "
                "Evaluate whether the current file content closes each of them; "
                "do not re-report wording differences that amount to the same "
                "finding.\n" + "\n".join(prior_lines) + "\n</prior_review>"
            )
            memory_context = memory_context + prior_block

        prompt = build_file_review_prompt(
            file_path,
            merged_content,
            decision_record,
            original_diff,
            project_context,
            max_content_chars=max_content_chars,
            memory_context=memory_context,
            check_strategy=check_strategy,
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

    @staticmethod
    def _issue_fingerprint(issue: JudgeIssue) -> tuple[str, str]:
        """O-J2: stable key for comparing issues across dispute rounds.

        Two issues are considered "the same" if they target the same file
        and the same issue_type. Description wording can shift slightly
        between model calls, so we deliberately avoid hashing it.
        """
        return (issue.file_path or "", issue.issue_type or "")

    def _freeze_to_prior_issues(
        self,
        current_issues: list[JudgeIssue],
        prior_issues: list[JudgeIssue],
    ) -> list[JudgeIssue]:
        """Keep only issues that were already present in the prior round.

        Reuse the prior ``issue_id`` so Executor ↔ Judge negotiation can
        track issue lifecycles across rounds. Out-of-scope new issues are
        dropped from the verdict and logged at WARNING level so a human
        operator can inspect them in the log trail.
        """
        prior_map: dict[tuple[str, str], JudgeIssue] = {}
        for prior_issue in prior_issues:
            prior_map.setdefault(self._issue_fingerprint(prior_issue), prior_issue)

        kept: list[JudgeIssue] = []
        dropped_new = 0
        seen_keys: set[tuple[str, str]] = set()
        for issue in current_issues:
            key = self._issue_fingerprint(issue)
            matched = prior_map.get(key)
            if matched is None:
                dropped_new += 1
                continue
            if key in seen_keys:
                continue
            seen_keys.add(key)
            kept.append(issue.model_copy(update={"issue_id": matched.issue_id}))

        if dropped_new:
            self.logger.warning(
                "O-J2 freeze: dropped %d new issue(s) introduced in dispute round "
                "(out-of-scope; roll up to meta-review)",
                dropped_new,
            )
        return kept

    def _local_syntax_ok(self, file_path: str) -> bool:
        """O-J1 pre-filter: re-use the syntax checker to decide whether a
        file can skip the LLM review path. Returns True only when the checker
        definitively validates the merged content; unreadable or untestable
        files fall through to the full LLM review.
        """
        if self.git_tool is None:
            return False
        abs_path = self.git_tool.repo_path / file_path
        if not abs_path.exists():
            return False
        content = _safe_read_text(abs_path)
        if not content:
            return False
        result = check_file_syntax(file_path, content)
        return bool(result.valid and not result.errors)

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
                    if fp not in state.file_decision_records:
                        issues.append(
                            JudgeIssue(
                                file_path=fp,
                                issue_level=IssueSeverity.CRITICAL,
                                issue_type="d_missing_not_processed",
                                description=(
                                    "D-missing file was never processed by auto_merge "
                                    "(likely blocked by unmet layer dependencies)"
                                ),
                                must_fix_before_merge=True,
                                veto_condition="D-missing file not processed by auto_merge",
                            )
                        )
                    else:
                        issues.append(
                            JudgeIssue(
                                file_path=fp,
                                issue_level=IssueSeverity.CRITICAL,
                                issue_type="d_missing_absent",
                                description=(
                                    "D-missing file was processed but is not present "
                                    "in HEAD after merge (apply_with_snapshot may have failed)"
                                ),
                                must_fix_before_merge=True,
                                veto_condition="D-missing file not present in HEAD after merge",
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

        for sc in getattr(state, "shadow_conflicts", []) or []:
            issues.append(
                JudgeIssue(
                    file_path=sc.path_a,
                    issue_level=IssueSeverity.CRITICAL,
                    issue_type="shadow_conflict_unresolved",
                    description=(
                        f"Shadow-path conflict: {sc.path_a} vs {sc.path_b} "
                        f"({sc.rule_description})"
                    ),
                    must_fix_before_merge=True,
                    veto_condition="Shadow-path conflict unresolved",
                )
            )

        issues.extend(self._check_top_level_invocations(state, categories))
        issues.extend(self._check_cross_layer_assertions(state))
        issues.extend(self._check_reverse_impacts(state))
        issues.extend(self._check_sentinel_hits(state))
        issues.extend(self._check_config_retention(state))

        return issues

    def _check_reverse_impacts(self, state: ReadOnlyStateView) -> list[JudgeIssue]:
        """P1-1: emit VETO for every fork-only file still referencing a symbol
        whose upstream interface changed."""
        reverse_impacts = getattr(state, "reverse_impacts", {}) or {}
        if not reverse_impacts:
            return []

        interface_changes = getattr(state, "interface_changes", []) or []
        symbol_to_change: dict[str, str] = {}
        for change in interface_changes:
            symbol_to_change.setdefault(
                change.symbol,
                f"{change.change_kind}: '{change.before}' -> '{change.after}'",
            )

        issues: list[JudgeIssue] = []
        for symbol, files in reverse_impacts.items():
            if not files:
                continue
            detail = symbol_to_change.get(symbol, "interface changed upstream")
            issues.append(
                JudgeIssue(
                    file_path=files[0],
                    issue_level=IssueSeverity.CRITICAL,
                    issue_type="reverse_impact_unhandled",
                    description=(
                        f"Upstream changed '{symbol}' ({detail}); fork-only files "
                        f"still reference it: {', '.join(files[:5])}"
                        f"{'...' if len(files) > 5 else ''}"
                    ),
                    must_fix_before_merge=True,
                    veto_condition=(
                        "Reverse-impact unhandled for upstream interface change"
                    ),
                )
            )
        return issues

    def _check_top_level_invocations(
        self,
        state: ReadOnlyStateView,
        categories: dict[str, FileChangeCategory],
    ) -> list[JudgeIssue]:
        if self.git_tool is None:
            return []
        merge_base = state.merge_base_commit or ""
        upstream_ref = state.config.upstream_ref
        if not merge_base or not upstream_ref:
            return []

        three_way = ThreeWayDiff(self.git_tool)
        issues: list[JudgeIssue] = []
        for fp, cat in categories.items():
            if cat not in (FileChangeCategory.B, FileChangeCategory.C):
                continue
            missing = three_way.extract_missing_top_level_invocations(
                fp, merge_base, upstream_ref
            )
            if missing:
                issues.append(
                    JudgeIssue(
                        file_path=fp,
                        issue_level=IssueSeverity.CRITICAL,
                        issue_type="top_level_invocation_lost",
                        description=(
                            "Top-level invocations/decorators missing after merge: "
                            f"{', '.join(missing[:10])}"
                            f"{'...' if len(missing) > 10 else ''}"
                        ),
                        must_fix_before_merge=True,
                        veto_condition="Top-level invocation/decorator lost after merge",
                    )
                )
        return issues

    def _check_sentinel_hits(self, state: ReadOnlyStateView) -> list[JudgeIssue]:
        """P2-2: emit VETO for every AUTO_SAFE file where the Executor found sentinels."""
        sentinel_hits = getattr(state, "sentinel_hits", {}) or {}
        if not sentinel_hits:
            return []

        issues: list[JudgeIssue] = []
        for file_path, hits in sentinel_hits.items():
            if not hits:
                continue
            sample = "; ".join(
                f"line {h.line_number}: {h.matched_text[:60]}" for h in hits[:3]
            )
            issues.append(
                JudgeIssue(
                    file_path=file_path,
                    issue_level=IssueSeverity.CRITICAL,
                    issue_type="sentinel_hit_unacknowledged",
                    description=(
                        f"Fork-customization sentinel marker(s) found in "
                        f"AUTO_SAFE file '{file_path}': {sample}"
                    ),
                    must_fix_before_merge=True,
                    veto_condition="Sentinel hit in AUTO_SAFE file unacknowledged",
                )
            )
        return issues

    def _check_config_retention(self, state: ReadOnlyStateView) -> list[JudgeIssue]:
        """P2-3: verify required lines still present in CI/env/docker files."""
        if self.git_tool is None:
            return []

        config_retention = getattr(state.config, "config_retention", None)
        if config_retention is None or not getattr(config_retention, "enabled", True):
            return []

        rules = getattr(config_retention, "rules", []) or []
        if not rules:
            return []

        from src.tools.config_line_retention_checker import ConfigLineRetentionChecker

        checker = ConfigLineRetentionChecker(self.git_tool.repo_path)
        violations = checker.check(rules)

        issues: list[JudgeIssue] = []
        for v in violations:
            issues.append(
                JudgeIssue(
                    file_path=v.file_path,
                    issue_level=IssueSeverity.CRITICAL,
                    issue_type="config_retention_violation",
                    description=(
                        f"Config retention violation in '{v.file_path}' "
                        f"(rule glob: '{v.rule_file_glob}'): "
                        f"missing required patterns: "
                        f"{', '.join(v.missing_patterns[:5])}"
                        f"{'...' if len(v.missing_patterns) > 5 else ''}"
                    ),
                    must_fix_before_merge=True,
                    veto_condition="Config retention required line missing",
                )
            )
        return issues

    def _check_cross_layer_assertions(
        self, state: ReadOnlyStateView
    ) -> list[JudgeIssue]:
        if self.git_tool is None:
            return []
        assertions = getattr(state.config, "cross_layer_assertions", []) or []
        if not assertions:
            return []

        from src.tools.cross_layer_checker import CrossLayerChecker

        checker = CrossLayerChecker(self.git_tool.repo_path)
        results = checker.check(assertions)
        issues: list[JudgeIssue] = []
        for r in results:
            if not r.missing_keys:
                continue
            issues.append(
                JudgeIssue(
                    file_path=r.source_file or "(cross_layer)",
                    issue_level=IssueSeverity.CRITICAL,
                    issue_type="cross_layer_assertion_missing",
                    description=(
                        f"Assertion '{r.assertion_name}': keys missing in "
                        f"{', '.join(r.target_files)}: "
                        f"{', '.join(sorted(r.missing_keys)[:10])}"
                        f"{'...' if len(r.missing_keys) > 10 else ''}"
                    ),
                    must_fix_before_merge=True,
                    veto_condition="Cross-layer assertion keys missing",
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
        merge_base: str = "",
    ) -> list[CustomizationViolation]:
        if not self.git_tool or not customizations:
            return []

        violations: list[CustomizationViolation] = []

        for entry in customizations:
            for verif in entry.verification:
                violation: CustomizationViolation | None = None
                if verif.type == "grep":
                    violation = self._verify_grep(entry.name, verif)
                elif verif.type == "grep_count_min":
                    violation = self._verify_grep_count_min(entry.name, verif)
                elif verif.type == "grep_count_baseline":
                    violation = self._verify_grep_count_baseline(
                        entry.name, verif, merge_base
                    )
                elif verif.type == "file_exists":
                    violation = self._verify_file_exists(entry.name, verif)
                elif verif.type == "function_exists":
                    violation = self._verify_function_exists(entry.name, verif)
                elif verif.type == "line_retention":
                    violation = self._verify_line_retention(
                        entry.name, verif, merge_base
                    )
                if violation:
                    violations.append(violation)

        return violations

    def _verify_grep_count_min(
        self,
        customization_name: str,
        verif: CustomizationVerification,
    ) -> CustomizationViolation | None:
        if not self.git_tool or not verif.pattern or verif.min_count is None:
            return None

        results = self.git_tool.grep_in_files(verif.pattern, verif.files)
        total_matches = sum(len(m) for m in results.values())
        checked = list(results.keys())

        if total_matches < verif.min_count:
            return CustomizationViolation(
                customization_name=customization_name,
                verification_type="grep_count_min",
                expected_pattern=(
                    f"{verif.pattern} (>= {verif.min_count} matches, "
                    f"got {total_matches})"
                ),
                checked_files=checked,
                match_count=total_matches,
            )
        return None

    def _verify_grep_count_baseline(
        self,
        customization_name: str,
        verif: CustomizationVerification,
        merge_base: str,
    ) -> CustomizationViolation | None:
        if not self.git_tool or not verif.pattern:
            return None

        baseline_ref = verif.baseline_ref or merge_base
        if not baseline_ref:
            return None

        baseline_total = self._count_matches_at_ref(
            verif.pattern, verif.files, baseline_ref
        )
        if baseline_total == 0:
            return None

        results = self.git_tool.grep_in_files(verif.pattern, verif.files)
        current_total = sum(len(m) for m in results.values())

        if current_total < baseline_total:
            return CustomizationViolation(
                customization_name=customization_name,
                verification_type="grep_count_baseline",
                expected_pattern=(
                    f"{verif.pattern} (baseline={baseline_total}, "
                    f"current={current_total})"
                ),
                checked_files=list(results.keys()),
                match_count=current_total,
            )
        return None

    def _verify_line_retention(
        self,
        customization_name: str,
        verif: CustomizationVerification,
        merge_base: str,
    ) -> CustomizationViolation | None:
        if not self.git_tool or verif.retention_ratio is None or not verif.files:
            return None

        baseline_ref = verif.baseline_ref or merge_base
        if not baseline_ref:
            return None

        all_files = [
            str(p.relative_to(self.git_tool.repo_path))
            for p in self.git_tool.repo_path.rglob("*")
            if p.is_file()
        ]
        target_files: list[str] = []
        for glob_pat in verif.files:
            for fp in all_files:
                if fnmatch.fnmatch(fp, glob_pat):
                    target_files.append(fp)

        for fp in target_files:
            baseline_content = self.git_tool.get_file_content(baseline_ref, fp)
            if baseline_content is None:
                continue
            baseline_lines = {
                ln.strip() for ln in baseline_content.splitlines() if ln.strip()
            }
            if not baseline_lines:
                continue

            abs_path = self.git_tool.repo_path / fp
            if not abs_path.exists():
                return CustomizationViolation(
                    customization_name=customization_name,
                    verification_type="line_retention",
                    expected_pattern=(
                        f"{fp}: file missing after merge "
                        f"(required retention {verif.retention_ratio:.2f})"
                    ),
                    checked_files=[fp],
                    match_count=0,
                )

            current_content = _safe_read_text(abs_path) or ""
            current_lines = {
                ln.strip() for ln in current_content.splitlines() if ln.strip()
            }
            retained = len(baseline_lines & current_lines)
            ratio = retained / len(baseline_lines)
            if ratio < verif.retention_ratio:
                return CustomizationViolation(
                    customization_name=customization_name,
                    verification_type="line_retention",
                    expected_pattern=(
                        f"{fp}: retention {ratio:.2f} < required "
                        f"{verif.retention_ratio:.2f} "
                        f"(kept {retained}/{len(baseline_lines)} lines)"
                    ),
                    checked_files=[fp],
                    match_count=retained,
                )
        return None

    def _count_matches_at_ref(
        self, pattern: str, file_globs: list[str], ref: str
    ) -> int:
        if not self.git_tool:
            return 0
        files_at_ref = self.git_tool.list_files(ref)
        compiled = re.compile(pattern)
        total = 0
        for fp in files_at_ref:
            if not any(fnmatch.fnmatch(fp, gp) for gp in file_globs):
                continue
            content = self.git_tool.get_file_content(ref, fp)
            if content is None:
                continue
            total += len(compiled.findall(content))
        return total

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

    _BATCH_SIZE = 8

    def _review_file_deterministic(
        self,
        file_path: str,
        merged_content: str,
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

        for marker in ("<<<<<<<", "=======", ">>>>>>>"):
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

    async def _review_files_batch_llm(
        self,
        chunk: list[tuple[str, str, "FileDecisionRecord", FileDiff]],
        state: ReadOnlyStateView,
    ) -> list[JudgeIssue]:
        from src.llm.prompts.judge_prompts import build_batch_file_review_prompt

        all_issues: list[JudgeIssue] = []

        for file_path, merged_content, _record, _fd in chunk:
            all_issues.extend(
                self._review_file_deterministic(file_path, merged_content)
            )

        file_reviews = [
            {
                "file_path": fp,
                "merged_content": content,
                "decision_record": record,
                "original_diff": fd,
            }
            for fp, content, record, fd in chunk
        ]
        prompt = build_batch_file_review_prompt(
            file_reviews,
            project_context=state.config.project_context,
        )
        try:
            raw = await self._call_llm_with_retry(
                [{"role": "user", "content": prompt}], system=JUDGE_SYSTEM
            )
            file_paths = [fp for fp, _, _, _ in chunk]
            per_file = parse_batch_file_review_issues(str(raw), file_paths)
            for issues_list in per_file.values():
                all_issues.extend(issues_list)
        except Exception as e:
            self.logger.error(
                "Batch LLM review failed for chunk of %d: %s", len(chunk), e
            )

        return all_issues

    async def review_batch(
        self,
        layer_id: int | None,
        file_paths: list[str],
        state: ReadOnlyStateView,
    ) -> "BatchVerdict":
        from src.models.judge import BatchVerdict

        file_diffs_map = {fd.file_path: fd for fd in state.file_diffs}
        all_issues: list[JudgeIssue] = []

        safe_files: list[tuple[str, str, FileDecisionRecord, FileDiff]] = []
        risky_files: list[tuple[str, str, FileDecisionRecord, FileDiff]] = []

        for file_path in file_paths:
            fd = file_diffs_map.get(file_path)
            record = state.file_decision_records.get(file_path)
            if fd is None or record is None:
                continue

            merged_content = ""
            if self.git_tool is not None:
                abs_path = self.git_tool.repo_path / file_path
                if abs_path.exists():
                    merged_content = _safe_read_text(abs_path) or ""

            if fd.risk_level == RiskLevel.AUTO_SAFE:
                safe_files.append((file_path, merged_content, record, fd))
            else:
                risky_files.append((file_path, merged_content, record, fd))

        for file_path, merged_content, _record, _fd in safe_files:
            all_issues.extend(
                self._review_file_deterministic(file_path, merged_content)
            )

        chunks = [
            risky_files[i : i + self._BATCH_SIZE]
            for i in range(0, len(risky_files), self._BATCH_SIZE)
        ]

        async def _process_chunk(idx: int) -> list[JudgeIssue]:
            return await self._review_files_batch_llm(chunks[idx], state)

        chunk_runner = ParallelFileRunner.from_api_key_env_list(
            self.llm_config.api_key_env_list,
            override=state.config.parallel_file_concurrency,
        )
        chunk_results = await chunk_runner.run_files(
            list(range(len(chunks))), _process_chunk
        )
        for idx in range(len(chunks)):
            result = chunk_results.get(idx)
            if isinstance(result, BaseException):
                self.logger.error(
                    "Parallel batch LLM review failed for chunk %d: %s", idx, result
                )
                continue
            if result is not None:
                all_issues.extend(result)

        self.logger.info(
            "review_batch layer=%s: %d safe (deterministic), %d risky → %d LLM calls",
            layer_id,
            len(safe_files),
            len(risky_files),
            (len(risky_files) + self._BATCH_SIZE - 1) // self._BATCH_SIZE
            if risky_files
            else 0,
        )

        blocking = [i for i in all_issues if i.must_fix_before_merge]
        approved = len(blocking) == 0
        repair_instructions = (
            self.build_repair_instructions(all_issues) if not approved else []
        )

        return BatchVerdict(
            layer_id=layer_id,
            approved=approved,
            needs_repair=not approved
            and any(r.is_repairable for r in repair_instructions),
            issues=all_issues,
            repair_instructions=repair_instructions,
            reviewed_files=list(file_paths),
        )

    async def re_evaluate(
        self,
        rebuttal: "ExecutorRebuttal",
        current_verdict: "BatchVerdict",
        state: ReadOnlyStateView,
    ) -> "BatchVerdict":
        from src.models.judge import BatchVerdict
        from src.llm.prompts.judge_prompts import build_re_evaluate_prompt
        import json as _json

        issues_summary = "\n".join(
            f"- [{i.issue_id}] {i.issue_level.value}: {i.description}"
            for i in current_verdict.issues
        )
        rebuttal_summary = (
            rebuttal.overall_rationale
            + "\n"
            + "\n".join(
                f"- issue {dp.issue_id}: {'DISPUTE' if not dp.accepts else 'ACCEPT'} "
                f"— {dp.counter_evidence}"
                for dp in rebuttal.dispute_points
            )
        )
        prompt = build_re_evaluate_prompt(rebuttal_summary, issues_summary)

        try:
            raw = await self._call_llm_with_retry(
                [{"role": "user", "content": prompt}], system=JUDGE_SYSTEM
            )
            raw_str = str(raw).strip()
            if raw_str.startswith("```"):
                lines = raw_str.splitlines()
                raw_str = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
            data = _json.loads(raw_str)
        except Exception as exc:
            self.logger.warning("re_evaluate LLM failed: %s", exc)
            return current_verdict

        issue_map = {i.issue_id: i for i in current_verdict.issues}
        remaining_issues: list[JudgeIssue] = []
        for entry in data.get("remaining_issues", []):
            issue_id = entry.get("issue_id", "")
            status = entry.get("status", "maintained")
            if status == "maintained" and issue_id in issue_map:
                remaining_issues.append(issue_map[issue_id])

        approved: bool = bool(data.get("overall_approved", len(remaining_issues) == 0))
        repair_instructions = (
            self.build_repair_instructions(remaining_issues) if not approved else []
        )

        return BatchVerdict(
            layer_id=current_verdict.layer_id,
            approved=approved,
            needs_repair=not approved
            and any(r.is_repairable for r in repair_instructions),
            issues=remaining_issues,
            repair_instructions=repair_instructions,
            reviewed_files=current_verdict.reviewed_files,
            round_num=current_verdict.round_num + 1,
        )

    async def meta_review(self, state: MergeState) -> dict[str, str]:
        """Meta-review: big-picture assessment of a failed judge review cycle.

        Returns a dict with 'assessment' and 'recommendation' keys.
        Uses META-JUDGE-* gates so the call is contract-compliant.
        """
        from src.llm.prompts.gate_registry import get_gate

        view = self.restricted_view(state)
        system = get_gate("META-JUDGE-SYSTEM").render()
        prompt = get_gate("META-JUDGE-REVIEW").render(
            list(view.judge_verdicts_log),
            view.judge_repair_rounds,
        )
        raw = await self._call_llm_with_retry(
            [{"role": "user", "content": prompt}],
            system=system,
        )
        return _parse_meta_review_json(str(raw))

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


def _resolve_check_strategy(
    file_path: str,
    record: FileDecisionRecord,
    customization_patterns: list[str],
) -> JudgeCheckStrategy:
    for pattern in customization_patterns:
        if fnmatch.fnmatch(file_path, pattern):
            return JudgeCheckStrategy.CUSTOMIZATION_PRESERVED
    if record.decision == MergeDecision.SEMANTIC_MERGE:
        return JudgeCheckStrategy.CUSTOMIZATION_PRESERVED
    return JudgeCheckStrategy.UPSTREAM_MATCH


def _parse_meta_review_json(raw: str) -> dict[str, str]:
    import json as _json2

    raw = raw.strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1:
        return {"assessment": raw[:200], "recommendation": ""}
    try:
        data = _json2.loads(raw[start : end + 1])
        return {
            "assessment": str(data.get("assessment", ""))[:200],
            "recommendation": str(data.get("recommendation", ""))[:200],
        }
    except Exception:
        return {"assessment": raw[:200], "recommendation": ""}


from src.agents.registry import AgentRegistry  # noqa: E402

AgentRegistry.register("judge", JudgeAgent, extra_kwargs=["git_tool"])
