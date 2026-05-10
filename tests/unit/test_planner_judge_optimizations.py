"""Tests for the eight Planner / PlannerJudge optimizations landed in
2026-05-10. Covers env-template downgrade, deterministic precheck,
safelist short-circuit, segment cache, and short-circuit auto-approve.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from src.models.config import FileClassifierConfig, SecuritySensitiveConfig
from src.models.diff import FileChangeCategory, FileDiff, FileStatus, RiskLevel
from src.models.plan import MergePhase, MergePlan, PhaseFileBatch, RiskSummary
from src.models.plan_judge import PlanIssue
from src.tools.file_classifier import (
    compute_risk_score,
    is_security_sensitive,
)
from src.llm.prompts.planner_judge_prompts import (
    compute_segment_signature,
    is_segment_obviously_safe,
    precheck_plan_integrity,
)

if TYPE_CHECKING:
    from src.models.plan_judge import PlanJudgeResult, PlanJudgeVerdict


def _make_fd(
    file_path: str,
    *,
    risk_level: RiskLevel = RiskLevel.AUTO_SAFE,
    lines_added: int = 5,
    lines_deleted: int = 2,
    conflict_count: int = 0,
    is_security_sensitive: bool = False,
    file_status: FileStatus = FileStatus.MODIFIED,
    change_category: FileChangeCategory | None = None,
) -> FileDiff:
    return FileDiff(
        file_path=file_path,
        file_status=file_status,
        risk_level=risk_level,
        risk_score=0.0,
        lines_added=lines_added,
        lines_deleted=lines_deleted,
        lines_changed=lines_added + lines_deleted,
        conflict_count=conflict_count,
        hunks=[],
        is_security_sensitive=is_security_sensitive,
        change_category=change_category,
    )


def _make_plan(batches: list[tuple[RiskLevel, list[str]]]) -> MergePlan:
    phases = [
        PhaseFileBatch(
            batch_id=f"b{i}",
            phase=MergePhase.AUTO_MERGE,
            file_paths=files,
            risk_level=risk,
        )
        for i, (risk, files) in enumerate(batches)
    ]
    total = sum(len(f) for _, f in batches)
    return MergePlan(
        created_at=datetime.now(),
        upstream_ref="upstream",
        fork_ref="fork",
        merge_base_commit="base",
        phases=phases,
        risk_summary=RiskSummary(
            total_files=total,
            auto_safe_count=0,
            auto_risky_count=0,
            human_required_count=0,
            deleted_only_count=0,
            binary_count=0,
            excluded_count=0,
            estimated_auto_merge_rate=0.0,
        ),
        project_context_summary="",
    )


# =========================================================================
# Optimization 1: extended risk_hint patterns + bump 0.25
# =========================================================================


def test_auth_py_single_file_now_hits_risk_hint() -> None:
    config = FileClassifierConfig()
    fd = _make_fd("tools/jira/tools/auth.py", lines_added=20, lines_deleted=5)
    score = compute_risk_score(fd, config)
    assert score >= 0.30, f"auth.py should clear auto_safe threshold, got {score}"


def test_otp_verify_paths_hit_risk_hint() -> None:
    config = FileClassifierConfig()
    for path in [
        "tools/plivo_verify/provider/plivo_verify.py",
        "tools/plivo_verify/tools/send_otp.py",
        "tools/plivo_verify/tools/verify_otp.py",
        "src/oauth_handler.py",
        "src/totp_generator.py",
    ]:
        fd = _make_fd(path, lines_added=10, lines_deleted=5)
        score = compute_risk_score(fd, config)
        assert score >= 0.30, f"{path} should hit risk_hint, got {score}"


def test_risk_hint_bump_default_is_0_25() -> None:
    cfg = SecuritySensitiveConfig()
    assert cfg.risk_hint_bump == 0.25


# =========================================================================
# Optimization 4: .env.example downgrade
# =========================================================================


def test_env_example_is_not_security_sensitive() -> None:
    config = FileClassifierConfig()
    assert is_security_sensitive("models/mimo/.env.example", config) is False
    assert is_security_sensitive("plugin/.env.sample", config) is False
    assert is_security_sensitive("svc/.env.template", config) is False
    # Real .env still flagged
    assert is_security_sensitive(".env", config) is True
    assert is_security_sensitive("svc/.env.production", config) is True


def test_env_example_score_below_strong_floor() -> None:
    config = FileClassifierConfig()
    fd = _make_fd("models/mimo/.env.example", lines_added=15, lines_deleted=0)
    score = compute_risk_score(fd, config)
    assert score < 0.8, f".env.example must NOT hit strong floor, got {score}"
    assert score >= 0.20, f".env.example should still get hint bump, got {score}"


# =========================================================================
# Optimization 3: deterministic precheck
# =========================================================================


def test_precheck_detects_mismatch() -> None:
    fd = _make_fd("src/foo.py", risk_level=RiskLevel.HUMAN_REQUIRED)
    plan = _make_plan([(RiskLevel.AUTO_SAFE, ["src/foo.py"])])
    issues = precheck_plan_integrity(plan, [fd])
    assert len(issues) == 1
    assert issues[0].file_path == "src/foo.py"
    assert issues[0].suggested_classification == RiskLevel.HUMAN_REQUIRED
    assert issues[0].issue_type == "risk_underestimated"


def test_precheck_detects_not_batched() -> None:
    fd = _make_fd("src/lost.py", risk_level=RiskLevel.AUTO_RISKY)
    plan = _make_plan([(RiskLevel.AUTO_SAFE, ["src/other.py"])])
    issues = precheck_plan_integrity(plan, [fd])
    assert len(issues) == 1
    assert issues[0].file_path == "src/lost.py"
    assert issues[0].issue_type == "wrong_batch"


def test_precheck_clean_plan_no_issues() -> None:
    fd1 = _make_fd("src/a.py", risk_level=RiskLevel.AUTO_SAFE)
    fd2 = _make_fd("src/b.py", risk_level=RiskLevel.AUTO_RISKY)
    plan = _make_plan(
        [
            (RiskLevel.AUTO_SAFE, ["src/a.py"]),
            (RiskLevel.AUTO_RISKY, ["src/b.py"]),
        ]
    )
    issues = precheck_plan_integrity(plan, [fd1, fd2])
    assert issues == []


def test_precheck_ignores_sentinel_levels() -> None:
    fd = _make_fd("img.png", risk_level=RiskLevel.BINARY)
    plan = _make_plan([(RiskLevel.AUTO_SAFE, ["img.png"])])
    issues = precheck_plan_integrity(plan, [fd])
    assert issues == []


# =========================================================================
# Optimization 6: segment safelist pre-filter
# =========================================================================


def test_safelist_segment_with_lockfiles_skips_llm() -> None:
    files = [
        _make_fd("plugin/uv.lock", risk_level=RiskLevel.AUTO_SAFE),
        _make_fd("plugin/pyproject.toml", risk_level=RiskLevel.AUTO_SAFE),
        _make_fd("plugin/requirements.txt", risk_level=RiskLevel.AUTO_SAFE),
    ]
    batch_risk_map = {fd.file_path: "auto_safe" for fd in files}
    assert is_segment_obviously_safe(files, batch_risk_map) is True


def test_safelist_segment_with_security_file_falls_through() -> None:
    files = [
        _make_fd("plugin/uv.lock", risk_level=RiskLevel.AUTO_SAFE),
        _make_fd(
            "plugin/auth.py",
            risk_level=RiskLevel.AUTO_RISKY,
            is_security_sensitive=True,
        ),
    ]
    batch_risk_map = {
        "plugin/uv.lock": "auto_safe",
        "plugin/auth.py": "auto_risky",
    }
    assert is_segment_obviously_safe(files, batch_risk_map) is False


def test_safelist_rejects_files_with_conflicts() -> None:
    files = [
        _make_fd("plugin/uv.lock", risk_level=RiskLevel.AUTO_SAFE),
        _make_fd(
            "plugin/manifest.yaml",
            risk_level=RiskLevel.AUTO_RISKY,
            conflict_count=2,
        ),
    ]
    batch_risk_map = {fd.file_path: fd.risk_level.value for fd in files}
    assert is_segment_obviously_safe(files, batch_risk_map) is False


def test_safelist_rejects_mismatched_files() -> None:
    files = [_make_fd("plugin/uv.lock", risk_level=RiskLevel.AUTO_RISKY)]
    batch_risk_map = {"plugin/uv.lock": "auto_safe"}
    assert is_segment_obviously_safe(files, batch_risk_map) is False


def test_safelist_accepts_small_yaml_with_no_risk_keywords() -> None:
    files = [
        _make_fd(
            "models/mimo/_position.yaml",
            risk_level=RiskLevel.AUTO_SAFE,
            lines_added=3,
        ),
        _make_fd(
            "models/mimo/llm/gpt-4.yaml",
            risk_level=RiskLevel.AUTO_SAFE,
            lines_added=20,
        ),
    ]
    batch_risk_map = {fd.file_path: "auto_safe" for fd in files}
    assert is_segment_obviously_safe(files, batch_risk_map) is True


def test_safelist_rejects_large_yaml() -> None:
    files = [
        _make_fd(
            "models/mimo/llm/gpt-4.yaml",
            risk_level=RiskLevel.AUTO_SAFE,
            lines_added=200,
            lines_deleted=100,
        ),
    ]
    batch_risk_map = {"models/mimo/llm/gpt-4.yaml": "auto_safe"}
    assert is_segment_obviously_safe(files, batch_risk_map) is False


def test_safelist_rejects_python_files() -> None:
    files = [_make_fd("plugin/handler.py", risk_level=RiskLevel.AUTO_SAFE)]
    batch_risk_map = {"plugin/handler.py": "auto_safe"}
    assert is_segment_obviously_safe(files, batch_risk_map) is False


def test_default_safelist_excludes_repo_specific_patterns() -> None:
    """Phase A: repo-specific patterns must not live in the built-in
    safelist. They have to be injected via extra_safelist_patterns."""
    from src.llm.prompts.planner_judge_prompts import SAFELIST_PATTERNS

    forbidden = {"**/_position.yaml", "**/.difyignore", "**/_assets/**"}
    leaked = forbidden.intersection(SAFELIST_PATTERNS)
    assert not leaked, f"repo-specific patterns leaked into default safelist: {leaked}"


def test_extra_safelist_patterns_lets_repo_specific_segment_skip_llm() -> None:
    files = [
        _make_fd(
            "models/mimo/_position.yaml",
            risk_level=RiskLevel.AUTO_SAFE,
            lines_added=200,
        ),
    ]
    batch_risk_map = {"models/mimo/_position.yaml": "auto_safe"}
    assert is_segment_obviously_safe(files, batch_risk_map) is False
    assert (
        is_segment_obviously_safe(
            files,
            batch_risk_map,
            extra_safelist_patterns=["**/_position.yaml"],
        )
        is True
    )


def test_extra_safelist_does_not_override_security_signal() -> None:
    """Even if extra patterns match, security_sensitive=True still
    forces fall-through to LLM."""
    files = [
        _make_fd(
            "models/mimo/_position.yaml",
            risk_level=RiskLevel.AUTO_RISKY,
            is_security_sensitive=True,
        ),
    ]
    batch_risk_map = {"models/mimo/_position.yaml": "auto_risky"}
    assert (
        is_segment_obviously_safe(
            files,
            batch_risk_map,
            extra_safelist_patterns=["**/_position.yaml"],
        )
        is False
    )


def test_plan_review_config_default_safelist_is_empty() -> None:
    """Default config must NOT pre-populate any per-repo patterns —
    that's the project's responsibility to opt in."""
    from src.models.config import PlanReviewConfig

    cfg = PlanReviewConfig()
    assert cfg.segment_safelist_patterns == []


# =========================================================================
# Phase B: PlannerAgent._generate_plan must be pure w.r.t. state
# =========================================================================


def test_generate_plan_method_does_not_mutate_state() -> None:
    """Static scan: the body of PlannerAgent._generate_plan must not
    contain ``state.<field> = ...`` assignments. All state writes have
    to live in ``run`` so persistence is collocated and discoverable.
    """
    import ast
    import inspect
    import re

    from src.agents.planner_agent import PlannerAgent

    source = inspect.getsource(PlannerAgent._generate_plan)
    # ast-parse the *method* in isolation by wrapping it in a dedent.
    tree = ast.parse(inspect.cleandoc(source))
    method_node = tree.body[0]
    body_src = ast.unparse(method_node)

    # Match `state.<name> = <rhs>` but not comparisons (==).
    write_re = re.compile(r"\bstate\.\w+\s*=(?!=)")
    hits = write_re.findall(body_src)
    assert not hits, (
        "PlannerAgent._generate_plan must be pure with respect to "
        f"state — found state writes: {hits}. Move the assignment "
        "into PlannerAgent.run."
    )


# =========================================================================
# Phase C: Lockfile safelist max-lines ceiling (P0-3)
# =========================================================================


def test_small_lockfile_still_in_safelist() -> None:
    files = [
        _make_fd(
            "plugin/uv.lock",
            risk_level=RiskLevel.AUTO_SAFE,
            lines_added=200,
            lines_deleted=100,
        ),
    ]
    batch_risk_map = {"plugin/uv.lock": "auto_safe"}
    assert (
        is_segment_obviously_safe(files, batch_risk_map, lockfile_max_lines=1000)
        is True
    )


def test_huge_lockfile_breaks_safelist() -> None:
    """Supply-chain risk: 5000-line uv.lock rewrite must NOT skip LLM."""
    files = [
        _make_fd(
            "plugin/uv.lock",
            risk_level=RiskLevel.AUTO_SAFE,
            lines_added=3000,
            lines_deleted=2000,
        ),
    ]
    batch_risk_map = {"plugin/uv.lock": "auto_safe"}
    assert (
        is_segment_obviously_safe(files, batch_risk_map, lockfile_max_lines=1000)
        is False
    )


def test_lockfile_threshold_default_is_1000() -> None:
    from src.models.config import PlanReviewConfig

    cfg = PlanReviewConfig()
    assert cfg.safelist_lockfile_max_lines == 1000


# =========================================================================
# Phase C: risk_hint pattern narrowing (P1-1)
# =========================================================================


def test_tokenizer_files_no_longer_match_risk_hint_patterns() -> None:
    """ML/NLP false-positive class: bare ``*token*`` used to match
    these. After narrowing, they must NOT match risk_hint_patterns
    (so risk_hint_bump is not applied). Raw rule-based score may still
    push them past auto_safe due to file-type / change-ratio signals,
    but that's content-driven, not path-heuristic-driven."""
    from src.tools.file_classifier import matches_any_pattern

    cfg = SecuritySensitiveConfig()
    for path in [
        "src/nlp/tokenizer.py",
        "vendor/bpe_tokens.json",
        "lib/tokenize.py",
        "models/token_counter.py",
        "src/llm/tokens.py",
    ]:
        assert not matches_any_pattern(path, cfg.risk_hint_patterns), (
            f"{path} should no longer match risk_hint_patterns after "
            "narrowing the bare *token* glob"
        )


def test_auth_token_variants_still_match_risk_hint_patterns() -> None:
    from src.tools.file_classifier import matches_any_pattern

    cfg = SecuritySensitiveConfig()
    for path in [
        "src/api/auth_token.py",
        "src/oauth/access_token_store.py",
        "lib/api_token_validator.go",
        "src/sec/refresh_token.ts",
        "src/auth/bearer_token.py",
        "src/middleware/token_auth.py",
    ]:
        assert matches_any_pattern(path, cfg.risk_hint_patterns), (
            f"{path} must still match a risk_hint_pattern after narrowing"
        )


# =========================================================================
# Phase C: Short-circuit issue_type whitelist (P1-2)
# =========================================================================


def test_shortcircuit_safe_issue_types_set() -> None:
    from src.core.phases.plan_review import SHORTCIRCUIT_SAFE_ISSUE_TYPES

    assert SHORTCIRCUIT_SAFE_ISSUE_TYPES == frozenset(
        {"risk_underestimated", "wrong_batch"}
    )


def test_shortcircuit_blocked_for_unrecognized_issue_types() -> None:
    """Build the same predicate the phase uses; assert that an
    unfamiliar issue_type (e.g. batch_ordering) prevents short-circuit."""
    from src.core.phases.plan_review import SHORTCIRCUIT_SAFE_ISSUE_TYPES

    safe_issues = [
        PlanIssue(
            file_path="a.py",
            current_classification=RiskLevel.AUTO_SAFE,
            suggested_classification=RiskLevel.AUTO_RISKY,
            reason="upgrade",
            issue_type="risk_underestimated",
        ),
        PlanIssue(
            file_path="b.py",
            current_classification=RiskLevel.AUTO_SAFE,
            suggested_classification=RiskLevel.HUMAN_REQUIRED,
            reason="security",
            issue_type="wrong_batch",
        ),
    ]
    assert all(iss.issue_type in SHORTCIRCUIT_SAFE_ISSUE_TYPES for iss in safe_issues)

    mixed_issues = safe_issues + [
        PlanIssue(
            file_path="c.py",
            current_classification=RiskLevel.AUTO_SAFE,
            suggested_classification=RiskLevel.AUTO_SAFE,
            reason="ordering broken",
            issue_type="batch_ordering",
        ),
    ]
    assert not all(
        iss.issue_type in SHORTCIRCUIT_SAFE_ISSUE_TYPES for iss in mixed_issues
    )


def test_generate_plan_returns_tuple_of_plan_and_diffs() -> None:
    """Behavior contract: _generate_plan returns (MergePlan,
    list[FileDiff]). When LLM rescoring is disabled, the diff list
    returned is the same object as state.file_diffs (no copy)."""
    import asyncio
    from unittest.mock import MagicMock

    from src.agents.planner_agent import PlannerAgent
    from src.models.config import AgentLLMConfig, MergeConfig
    from src.models.state import MergeState

    config = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/fork")
    config.llm_risk_scoring.enabled = False
    state = MergeState(config=config)
    state.file_diffs = [_make_fd("src/a.py"), _make_fd("src/b.py")]

    llm_cfg = AgentLLMConfig(
        provider="anthropic",
        model="test-model",
        api_key_env="ANTHROPIC_API_KEY",
        max_retries=1,
    )
    agent = PlannerAgent(llm_cfg)
    agent.llm = MagicMock()

    result = asyncio.get_event_loop().run_until_complete(agent._generate_plan(state))

    assert isinstance(result, tuple) and len(result) == 2
    plan, diffs = result
    assert plan.__class__.__name__ == "MergePlan"
    # Identity check: when rescoring is disabled, no copy is made.
    assert diffs is state.file_diffs


# =========================================================================
# Optimization 2: segment signature stability
# =========================================================================


def test_segment_signature_stable_across_file_order() -> None:
    files_a = [_make_fd("a.py"), _make_fd("b.py")]
    files_b = [_make_fd("b.py"), _make_fd("a.py")]
    batch_map = {"a.py": "auto_safe", "b.py": "auto_safe"}
    assert compute_segment_signature(files_a, batch_map) == compute_segment_signature(
        files_b, batch_map
    )


def test_segment_signature_changes_with_batch_risk() -> None:
    files = [_make_fd("a.py")]
    sig1 = compute_segment_signature(files, {"a.py": "auto_safe"})
    sig2 = compute_segment_signature(files, {"a.py": "auto_risky"})
    assert sig1 != sig2


# =========================================================================
# Optimization 8: deterministic short-circuit auto-approve
# =========================================================================


def test_all_issues_applied_returns_true_when_classifications_match() -> None:
    from src.core.phases.plan_review import _all_issues_applied

    plan = _make_plan(
        [
            (RiskLevel.AUTO_SAFE, ["a.py"]),
            (RiskLevel.AUTO_RISKY, ["b.py"]),
        ]
    )
    issues = [
        PlanIssue(
            file_path="b.py",
            current_classification=RiskLevel.AUTO_SAFE,
            suggested_classification=RiskLevel.AUTO_RISKY,
            reason="upgrade",
            issue_type="risk_underestimated",
        ),
    ]
    assert _all_issues_applied(issues, plan) is True


def test_all_issues_applied_returns_false_when_unchanged() -> None:
    from src.core.phases.plan_review import _all_issues_applied

    plan = _make_plan([(RiskLevel.AUTO_SAFE, ["b.py"])])
    issues = [
        PlanIssue(
            file_path="b.py",
            current_classification=RiskLevel.AUTO_SAFE,
            suggested_classification=RiskLevel.AUTO_RISKY,
            reason="upgrade",
            issue_type="risk_underestimated",
        ),
    ]
    assert _all_issues_applied(issues, plan) is False


# =========================================================================
# Phase D: P2-1 — _MISMATCH_TRACKED_LEVELS defined before its callers
# =========================================================================


def test_mismatch_tracked_levels_defined_before_first_use() -> None:
    """Source-order audit: the constant must come before the helpers
    that read it, so reader doesn't have to mental-trace late binding."""
    import inspect

    from src.llm.prompts import planner_judge_prompts as mod

    source = inspect.getsource(mod)
    def_idx = source.index("_MISMATCH_TRACKED_LEVELS = frozenset")
    first_use = source.index("_MISMATCH_TRACKED_LEVELS", def_idx + 1)
    safelist_use_check = "fd.risk_level in _MISMATCH_TRACKED_LEVELS"
    assert safelist_use_check in source
    safelist_use_idx = source.index(safelist_use_check)
    assert def_idx < safelist_use_idx, (
        f"_MISMATCH_TRACKED_LEVELS definition (idx {def_idx}) must "
        f"precede its use in is_segment_obviously_safe (idx "
        f"{safelist_use_idx})"
    )
    assert def_idx < first_use


def test_mismatch_tracked_levels_defined_only_once() -> None:
    """Guard against the duplicate-definition state we briefly had
    when the constant was hoisted but the original copy was kept."""
    import inspect

    from src.llm.prompts import planner_judge_prompts as mod

    source = inspect.getsource(mod)
    occurrences = source.count("_MISMATCH_TRACKED_LEVELS = frozenset")
    assert occurrences == 1, (
        f"expected exactly one definition of _MISMATCH_TRACKED_LEVELS, "
        f"found {occurrences}"
    )


# =========================================================================
# Phase D: P2-3 — single-segment path consumes cache
# =========================================================================


def test_single_segment_cache_hit_skips_llm() -> None:
    """When ``review_plan`` runs with one segment and the prior round's
    snapshot has the same signature with APPROVED verdict, the LLM
    must not be invoked. Confirms the single-segment path now reads
    from ``prior_segment_results`` (parity with the multi-segment path).
    """
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from src.agents.planner_judge_agent import (
        PlannerJudgeAgent,
        SegmentReviewSnapshot,
    )
    from src.models.config import AgentLLMConfig
    from src.models.plan_judge import PlanJudgeResult, PlanJudgeVerdict
    from src.llm.prompts.planner_judge_prompts import compute_segment_signature

    file_diffs = [_make_fd(f"src/f{i}.py") for i in range(3)]
    plan = _make_plan([(RiskLevel.AUTO_SAFE, [fd.file_path for fd in file_diffs])])

    batch_risk_map = {fd.file_path: "auto_safe" for fd in file_diffs}
    sig = compute_segment_signature(file_diffs, batch_risk_map)
    cached_verdict = PlanJudgeVerdict(
        result=PlanJudgeResult.APPROVED,
        revision_round=0,
        issues=[],
        approved_files_count=len(file_diffs),
        flagged_files_count=0,
        summary="prior round approval",
        judge_model="mock-model",
        timestamp=datetime.now(),
    )
    prior = {
        0: SegmentReviewSnapshot(
            segment_idx=0,
            signature=sig,
            verdict=cached_verdict,
        )
    }

    llm_cfg = AgentLLMConfig(
        provider="anthropic",
        model="mock-model",
        api_key_env="ANTHROPIC_API_KEY",
        max_retries=1,
    )
    agent = PlannerJudgeAgent(llm_cfg)
    agent.llm = MagicMock()
    review_single_mock = AsyncMock()
    agent._review_single = review_single_mock

    out: dict[int, SegmentReviewSnapshot] = {}
    verdict = asyncio.get_event_loop().run_until_complete(
        agent.review_plan(
            plan,
            file_diffs,
            revision_round=1,
            prior_segment_results=prior,
            out_segment_results=out,
        )
    )

    review_single_mock.assert_not_called()
    assert verdict.result == PlanJudgeResult.APPROVED
    assert verdict.revision_round == 1
    assert out[0].source == "cache"


def test_single_segment_cache_miss_invokes_llm() -> None:
    """Negative control: when no prior snapshot exists the LLM still
    runs (cache is opt-in, not mandatory)."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from src.agents.planner_judge_agent import (
        PlannerJudgeAgent,
        SegmentReviewSnapshot,
    )
    from src.models.config import AgentLLMConfig
    from src.models.plan_judge import PlanJudgeResult, PlanJudgeVerdict

    file_diffs = [_make_fd("src/f.py")]
    plan = _make_plan([(RiskLevel.AUTO_SAFE, ["src/f.py"])])

    llm_cfg = AgentLLMConfig(
        provider="anthropic",
        model="mock-model",
        api_key_env="ANTHROPIC_API_KEY",
        max_retries=1,
    )
    agent = PlannerJudgeAgent(llm_cfg)
    agent.llm = MagicMock()
    fresh_verdict = PlanJudgeVerdict(
        result=PlanJudgeResult.APPROVED,
        revision_round=1,
        issues=[],
        approved_files_count=1,
        flagged_files_count=0,
        summary="fresh review",
        judge_model="mock-model",
        timestamp=datetime.now(),
    )
    review_single_mock = AsyncMock(return_value=fresh_verdict)
    agent._review_single = review_single_mock

    out: dict[int, SegmentReviewSnapshot] = {}
    asyncio.get_event_loop().run_until_complete(
        agent.review_plan(
            plan,
            file_diffs,
            revision_round=1,
            prior_segment_results=None,
            out_segment_results=out,
        )
    )

    review_single_mock.assert_called_once()
    assert out[0].source == "llm"


# =========================================================================
# Phase E: P3-1 — _merge_with_precheck merge semantics
# =========================================================================


def _make_verdict(
    *,
    result: "PlanJudgeResult",
    issues: list[PlanIssue],
    approved: int,
    flagged: int,
    summary: str = "base",
) -> "PlanJudgeVerdict":
    from src.models.plan_judge import PlanJudgeVerdict

    return PlanJudgeVerdict(
        result=result,
        revision_round=0,
        issues=issues,
        approved_files_count=approved,
        flagged_files_count=flagged,
        summary=summary,
        judge_model="mock-model",
        timestamp=datetime.now(),
    )


def test_merge_with_precheck_empty_returns_base_unchanged() -> None:
    from src.agents.planner_judge_agent import _merge_with_precheck
    from src.models.plan_judge import PlanJudgeResult

    base = _make_verdict(
        result=PlanJudgeResult.APPROVED, issues=[], approved=10, flagged=0
    )
    result = _merge_with_precheck(
        base, precheck_issues=[], total_files=10, judge_model="m", revision_round=0
    )
    assert result is base


def test_merge_with_precheck_upgrades_approved_to_revision_needed() -> None:
    from src.agents.planner_judge_agent import _merge_with_precheck
    from src.models.plan_judge import PlanJudgeResult

    base = _make_verdict(
        result=PlanJudgeResult.APPROVED, issues=[], approved=5, flagged=0
    )
    pre = [
        PlanIssue(
            file_path="x.py",
            current_classification=RiskLevel.AUTO_SAFE,
            suggested_classification=RiskLevel.HUMAN_REQUIRED,
            reason="MISMATCH",
            issue_type="risk_underestimated",
        )
    ]
    merged = _merge_with_precheck(
        base, precheck_issues=pre, total_files=5, judge_model="m", revision_round=2
    )
    assert merged.result == PlanJudgeResult.REVISION_NEEDED
    assert len(merged.issues) == 1
    assert merged.issues[0].file_path == "x.py"
    assert merged.flagged_files_count == 1
    assert merged.approved_files_count == 4  # 5 total - 1 flagged
    assert merged.revision_round == 2
    assert "precheck added 1 integrity issue" in merged.summary


def test_merge_with_precheck_dedups_by_file_path() -> None:
    """When LLM issue and precheck issue point at the same file, the
    precheck issue is dropped — LLM verdict carries more context."""
    from src.agents.planner_judge_agent import _merge_with_precheck
    from src.models.plan_judge import PlanJudgeResult

    llm_issue = PlanIssue(
        file_path="dup.py",
        current_classification=RiskLevel.AUTO_SAFE,
        suggested_classification=RiskLevel.HUMAN_REQUIRED,
        reason="LLM analysis: critical security path",
        issue_type="risk_underestimated",
    )
    base = _make_verdict(
        result=PlanJudgeResult.REVISION_NEEDED,
        issues=[llm_issue],
        approved=0,
        flagged=1,
    )
    precheck_dup = PlanIssue(
        file_path="dup.py",
        current_classification=RiskLevel.AUTO_SAFE,
        suggested_classification=RiskLevel.AUTO_RISKY,
        reason="MISMATCH precheck",
        issue_type="risk_underestimated",
    )
    precheck_new = PlanIssue(
        file_path="new.py",
        current_classification=RiskLevel.AUTO_SAFE,
        suggested_classification=RiskLevel.AUTO_RISKY,
        reason="NOT-BATCHED",
        issue_type="wrong_batch",
    )
    merged = _merge_with_precheck(
        base,
        precheck_issues=[precheck_dup, precheck_new],
        total_files=2,
        judge_model="m",
        revision_round=0,
    )
    paths = [iss.file_path for iss in merged.issues]
    assert paths.count("dup.py") == 1
    assert "new.py" in paths
    # LLM issue's reason wins for dup.py (precheck duplicate dropped)
    dup_iss = next(i for i in merged.issues if i.file_path == "dup.py")
    assert "LLM analysis" in dup_iss.reason


def test_merge_with_precheck_keeps_revision_needed_when_base_already_failed() -> None:
    from src.agents.planner_judge_agent import _merge_with_precheck
    from src.models.plan_judge import PlanJudgeResult

    base = _make_verdict(
        result=PlanJudgeResult.REVISION_NEEDED,
        issues=[
            PlanIssue(
                file_path="a.py",
                current_classification=RiskLevel.AUTO_SAFE,
                suggested_classification=RiskLevel.AUTO_RISKY,
                reason="LLM concern",
                issue_type="risk_underestimated",
            )
        ],
        approved=0,
        flagged=1,
    )
    pre = [
        PlanIssue(
            file_path="b.py",
            current_classification=RiskLevel.AUTO_SAFE,
            suggested_classification=RiskLevel.HUMAN_REQUIRED,
            reason="precheck",
            issue_type="wrong_batch",
        )
    ]
    merged = _merge_with_precheck(
        base, precheck_issues=pre, total_files=2, judge_model="m", revision_round=0
    )
    assert merged.result == PlanJudgeResult.REVISION_NEEDED
    assert merged.flagged_files_count == 2
    assert merged.approved_files_count == 0


# =========================================================================
# Phase E: P3-2 — multi-segment cache hit avoids LLM
# =========================================================================


def test_multi_segment_all_cache_hits_skip_llm() -> None:
    """Two-segment review where both segment signatures match prior
    APPROVED snapshots: ``_review_segment`` is never invoked."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch

    from src.agents.planner_judge_agent import (
        PlannerJudgeAgent,
        SegmentReviewSnapshot,
    )
    from src.models.config import AgentLLMConfig
    from src.models.plan_judge import PlanJudgeResult, PlanJudgeVerdict
    from src.llm.prompts.planner_judge_prompts import (
        REVIEW_SEGMENT_SIZE,
        compute_segment_signature,
    )

    n_files = REVIEW_SEGMENT_SIZE + 5  # forces 2 segments
    file_diffs = [_make_fd(f"src/f{i}.py") for i in range(n_files)]
    plan = _make_plan([(RiskLevel.AUTO_SAFE, [fd.file_path for fd in file_diffs])])

    batch_risk_map = {fd.file_path: "auto_safe" for fd in file_diffs}
    seg0 = file_diffs[:REVIEW_SEGMENT_SIZE]
    seg1 = file_diffs[REVIEW_SEGMENT_SIZE:]
    sig0 = compute_segment_signature(seg0, batch_risk_map)
    sig1 = compute_segment_signature(seg1, batch_risk_map)

    def make_cached(idx: int, sig: str, count: int) -> SegmentReviewSnapshot:
        v = PlanJudgeVerdict(
            result=PlanJudgeResult.APPROVED,
            revision_round=0,
            issues=[],
            approved_files_count=count,
            flagged_files_count=0,
            summary=f"prior R0 segment {idx}",
            judge_model="mock-model",
            timestamp=datetime.now(),
        )
        return SegmentReviewSnapshot(
            segment_idx=idx, signature=sig, verdict=v, source="llm"
        )

    prior = {
        0: make_cached(0, sig0, len(seg0)),
        1: make_cached(1, sig1, len(seg1)),
    }

    llm_cfg = AgentLLMConfig(
        provider="anthropic",
        model="mock-model",
        api_key_env="ANTHROPIC_API_KEY",
        max_retries=1,
    )
    agent = PlannerJudgeAgent(llm_cfg)
    agent.llm = MagicMock()

    review_segment_mock = AsyncMock()
    out: dict[int, SegmentReviewSnapshot] = {}
    with patch.object(agent, "_review_segment", review_segment_mock):
        verdict = asyncio.get_event_loop().run_until_complete(
            agent.review_plan(
                plan,
                file_diffs,
                revision_round=1,
                prior_segment_results=prior,
                out_segment_results=out,
            )
        )

    review_segment_mock.assert_not_called()
    assert verdict.result == PlanJudgeResult.APPROVED
    assert out[0].source == "cache"
    assert out[1].source == "cache"


# =========================================================================
# Phase E: P3-3 — PlanReviewPhase short-circuit auto-approve end-to-end
# =========================================================================


def test_planreview_phase_shortcircuits_after_planner_accepts_all() -> None:
    """End-to-end: R0 returns REVISION_NEEDED with one
    risk_underestimated issue → Planner accepts and applies → R1 must
    short-circuit with NO second LLM review_plan call.
    """
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch

    from src.core.phases.plan_review import PlanReviewPhase
    from src.models.config import MergeConfig
    from src.models.plan import (
        MergePhase as MP,
        MergePlan,
        PhaseFileBatch,
        RiskSummary,
    )
    from src.models.plan_judge import PlanJudgeResult, PlanJudgeVerdict
    from src.models.plan_review import IssueResponseAction, PlannerIssueResponse
    from src.models.state import MergeState
    from uuid import uuid4

    config = MergeConfig(upstream_ref="upstream/main", fork_ref="origin/main")
    config.max_plan_revision_rounds = 5
    config.output.language = "en"

    # Initial plan: b.py is auto_safe (will be flagged + reclassified)
    initial_phases = [
        PhaseFileBatch(
            batch_id=str(uuid4()),
            phase=MP.AUTO_MERGE,
            file_paths=["a.py", "b.py"],
            risk_level=RiskLevel.AUTO_SAFE,
            can_parallelize=True,
        ),
    ]
    initial_plan = MergePlan(
        created_at=datetime.now(),
        upstream_ref="upstream/main",
        fork_ref="origin/main",
        merge_base_commit="abc",
        phases=initial_phases,
        risk_summary=RiskSummary(
            total_files=2,
            auto_safe_count=2,
            auto_risky_count=0,
            human_required_count=0,
            deleted_only_count=0,
            binary_count=0,
            excluded_count=0,
            estimated_auto_merge_rate=1.0,
        ),
        project_context_summary="",
    )

    # Revised plan: b.py moved to AUTO_RISKY batch (Planner applied issue)
    revised_phases = [
        PhaseFileBatch(
            batch_id=str(uuid4()),
            phase=MP.AUTO_MERGE,
            file_paths=["a.py"],
            risk_level=RiskLevel.AUTO_SAFE,
            can_parallelize=True,
        ),
        PhaseFileBatch(
            batch_id=str(uuid4()),
            phase=MP.CONFLICT_ANALYSIS,
            file_paths=["b.py"],
            risk_level=RiskLevel.AUTO_RISKY,
            can_parallelize=True,
        ),
    ]
    revised_plan = MergePlan(
        created_at=datetime.now(),
        upstream_ref="upstream/main",
        fork_ref="origin/main",
        merge_base_commit="abc",
        phases=revised_phases,
        risk_summary=initial_plan.risk_summary,
        project_context_summary="",
    )

    state = MergeState(config=config)
    state.merge_plan = initial_plan
    state.file_diffs = [
        _make_fd("a.py"),
        _make_fd("b.py", risk_level=RiskLevel.AUTO_RISKY),
    ]

    flagging_issue = PlanIssue(
        file_path="b.py",
        current_classification=RiskLevel.AUTO_SAFE,
        suggested_classification=RiskLevel.AUTO_RISKY,
        reason="b.py risk underestimated",
        issue_type="risk_underestimated",
    )
    r0_verdict = PlanJudgeVerdict(
        result=PlanJudgeResult.REVISION_NEEDED,
        revision_round=0,
        issues=[flagging_issue],
        approved_files_count=1,
        flagged_files_count=1,
        summary="R0",
        judge_model="mock-model",
        timestamp=datetime.now(),
    )
    accept_response = PlannerIssueResponse(
        issue_id=flagging_issue.issue_id,
        file_path="b.py",
        action=IssueResponseAction.ACCEPT,
        reason="agreed",
    )

    mock_judge = AsyncMock()
    mock_judge.review_plan = AsyncMock(return_value=r0_verdict)
    mock_judge.llm_config = MagicMock(model="mock-model")

    mock_planner = AsyncMock()
    mock_planner.revise_plan = AsyncMock(
        return_value=(revised_plan, [accept_response], [])
    )

    mock_sm = MagicMock()
    mock_sm.transition = MagicMock()

    ctx = MagicMock()
    ctx.agents = {"planner": mock_planner, "planner_judge": mock_judge}
    ctx.config = config
    ctx.state_machine = mock_sm

    phase = PlanReviewPhase()
    with patch("src.core.phases.plan_review.write_plan_review_report"):
        asyncio.get_event_loop().run_until_complete(phase.execute(state, ctx))

    # Exactly ONE review_plan call: R0. R1 short-circuits.
    assert mock_judge.review_plan.call_count == 1, (
        f"expected 1 LLM review_plan call (R0 only); got "
        f"{mock_judge.review_plan.call_count}"
    )
    # R1's deterministic verdict was applied
    assert state.plan_judge_verdict is not None
    assert state.plan_judge_verdict.result == PlanJudgeResult.APPROVED
    assert "deterministically" in state.plan_judge_verdict.summary


def test_planreview_phase_does_not_shortcircuit_with_unsafe_issue_type() -> None:
    """If a prior-round issue carries an issue_type outside
    SHORTCIRCUIT_SAFE_ISSUE_TYPES (e.g. ``batch_ordering``), R1 must
    still call review_plan."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch

    from src.core.phases.plan_review import PlanReviewPhase
    from src.models.config import MergeConfig
    from src.models.plan import (
        MergePhase as MP,
        MergePlan,
        PhaseFileBatch,
        RiskSummary,
    )
    from src.models.plan_judge import PlanJudgeResult, PlanJudgeVerdict
    from src.models.plan_review import IssueResponseAction, PlannerIssueResponse
    from src.models.state import MergeState
    from uuid import uuid4

    config = MergeConfig(upstream_ref="upstream/main", fork_ref="origin/main")
    config.max_plan_revision_rounds = 5
    config.output.language = "en"

    # Two files so revised_plan can change b.py's classification (to
    # bypass the convergence guard) while keeping a.py's classification
    # stable (so _all_issues_applied still passes — leaving issue_type
    # as the *only* short-circuit guard that fails).
    plan = MergePlan(
        created_at=datetime.now(),
        upstream_ref="upstream/main",
        fork_ref="origin/main",
        merge_base_commit="abc",
        phases=[
            PhaseFileBatch(
                batch_id=str(uuid4()),
                phase=MP.AUTO_MERGE,
                file_paths=["a.py", "b.py"],
                risk_level=RiskLevel.AUTO_SAFE,
                can_parallelize=True,
            )
        ],
        risk_summary=RiskSummary(
            total_files=2,
            auto_safe_count=2,
            auto_risky_count=0,
            human_required_count=0,
            deleted_only_count=0,
            binary_count=0,
            excluded_count=0,
            estimated_auto_merge_rate=1.0,
        ),
        project_context_summary="",
    )
    revised_plan = MergePlan(
        created_at=datetime.now(),
        upstream_ref="upstream/main",
        fork_ref="origin/main",
        merge_base_commit="abc",
        phases=[
            PhaseFileBatch(
                batch_id=str(uuid4()),
                phase=MP.AUTO_MERGE,
                file_paths=["a.py"],
                risk_level=RiskLevel.AUTO_SAFE,
                can_parallelize=True,
            ),
            PhaseFileBatch(
                batch_id=str(uuid4()),
                phase=MP.CONFLICT_ANALYSIS,
                file_paths=["b.py"],
                risk_level=RiskLevel.AUTO_RISKY,  # change drives plan_changed=True
                can_parallelize=True,
            ),
        ],
        risk_summary=plan.risk_summary,
        project_context_summary="",
    )

    state = MergeState(config=config)
    state.merge_plan = plan
    state.file_diffs = [_make_fd("a.py"), _make_fd("b.py")]

    ordering_issue = PlanIssue(
        file_path="a.py",
        current_classification=RiskLevel.AUTO_SAFE,
        suggested_classification=RiskLevel.AUTO_SAFE,
        reason="batch order broken",
        issue_type="batch_ordering",
    )
    r0_verdict = PlanJudgeVerdict(
        result=PlanJudgeResult.REVISION_NEEDED,
        revision_round=0,
        issues=[ordering_issue],
        approved_files_count=0,
        flagged_files_count=1,
        summary="R0",
        judge_model="mock-model",
        timestamp=datetime.now(),
    )
    r1_verdict = PlanJudgeVerdict(
        result=PlanJudgeResult.APPROVED,
        revision_round=1,
        issues=[],
        approved_files_count=1,
        flagged_files_count=0,
        summary="R1",
        judge_model="mock-model",
        timestamp=datetime.now(),
    )
    accept_response = PlannerIssueResponse(
        issue_id=ordering_issue.issue_id,
        file_path="a.py",
        action=IssueResponseAction.ACCEPT,
        reason="agreed",
    )

    mock_judge = AsyncMock()
    mock_judge.review_plan = AsyncMock(side_effect=[r0_verdict, r1_verdict])
    mock_judge.llm_config = MagicMock(model="mock-model")

    mock_planner = AsyncMock()
    mock_planner.revise_plan = AsyncMock(
        return_value=(revised_plan, [accept_response], [])
    )

    ctx = MagicMock()
    ctx.agents = {"planner": mock_planner, "planner_judge": mock_judge}
    ctx.config = config
    ctx.state_machine = MagicMock()

    phase = PlanReviewPhase()
    with patch("src.core.phases.plan_review.write_plan_review_report"):
        asyncio.get_event_loop().run_until_complete(phase.execute(state, ctx))

    # TWO review_plan calls: R0 and R1 (no short-circuit)
    assert mock_judge.review_plan.call_count == 2


# =========================================================================
# Phase E: P3-4 — PlannerAgent.run propagates rescored diffs to state
# =========================================================================


def test_run_writes_rescored_diffs_back_to_state() -> None:
    """Behavior contract: when LLM rescoring is enabled and changes a
    file's risk_score, ``run`` reassigns ``state.file_diffs`` so
    downstream agents see the rescored value."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from src.agents.planner_agent import PlannerAgent
    from src.models.config import AgentLLMConfig, MergeConfig
    from src.models.state import MergeState

    config = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/fork")
    config.llm_risk_scoring.enabled = True
    config.llm_risk_scoring.gray_zone_low = 0.0
    config.llm_risk_scoring.gray_zone_high = 1.0
    config.llm_risk_scoring.rule_weight = 0.0  # let LLM dominate

    state = MergeState(config=config)
    original = _make_fd("src/borderline.py", lines_added=5, lines_deleted=0)
    state.file_diffs = [original]
    original_id = id(state.file_diffs)

    llm_cfg = AgentLLMConfig(
        provider="anthropic",
        model="test-model",
        api_key_env="ANTHROPIC_API_KEY",
        max_retries=1,
    )
    agent = PlannerAgent(llm_cfg)
    agent.llm = MagicMock()
    # First call (rescoring) returns high risk; later calls (planner
    # batch classification) return a fallback minimal plan to keep
    # _build_merge_plan happy.
    agent._call_llm_with_retry = AsyncMock(
        side_effect=[
            '{"llm_risk_score": 0.9}',
            '{"phases": [], "risk_summary": {"total_files": 1, '
            '"auto_safe_count": 0, "auto_risky_count": 1, '
            '"human_required_count": 0, "deleted_only_count": 0, '
            '"binary_count": 0, "excluded_count": 0, '
            '"estimated_auto_merge_rate": 0.0, "top_risk_files": []}, '
            '"project_context_summary": "", "special_instructions": []}',
        ]
    )

    asyncio.get_event_loop().run_until_complete(agent.run(state))

    # state.file_diffs reference replaced (rescored list)
    assert id(state.file_diffs) != original_id
    assert len(state.file_diffs) == 1
    assert state.file_diffs[0].risk_score >= 0.5, (
        f"rescored risk_score should reflect the LLM's 0.9 verdict, "
        f"got {state.file_diffs[0].risk_score}"
    )


def test_run_does_not_reassign_when_rescoring_disabled() -> None:
    """When rescoring is off, ``run`` must keep the original
    ``state.file_diffs`` list object — no spurious reassignment."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from src.agents.planner_agent import PlannerAgent
    from src.models.config import AgentLLMConfig, MergeConfig
    from src.models.state import MergeState

    config = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/fork")
    config.llm_risk_scoring.enabled = False

    state = MergeState(config=config)
    state.file_diffs = [_make_fd("src/foo.py")]
    original_id = id(state.file_diffs)

    llm_cfg = AgentLLMConfig(
        provider="anthropic",
        model="test-model",
        api_key_env="ANTHROPIC_API_KEY",
        max_retries=1,
    )
    agent = PlannerAgent(llm_cfg)
    agent.llm = MagicMock()
    agent._call_llm_with_retry = AsyncMock(
        return_value=(
            '{"phases": [{"batch_id": "b1", "phase": "auto_merge", '
            '"file_paths": ["src/foo.py"], "risk_level": "auto_safe", '
            '"can_parallelize": true}], '
            '"risk_summary": {"total_files": 1, "auto_safe_count": 1, '
            '"auto_risky_count": 0, "human_required_count": 0, '
            '"deleted_only_count": 0, "binary_count": 0, '
            '"excluded_count": 0, "estimated_auto_merge_rate": 1.0, '
            '"top_risk_files": []}, '
            '"project_context_summary": "", "special_instructions": []}'
        )
    )

    asyncio.get_event_loop().run_until_complete(agent.run(state))

    assert id(state.file_diffs) == original_id, (
        "state.file_diffs should not be reassigned when rescoring is disabled"
    )


# =========================================================================
# P2-2: PlanIssue.current_classification Optional
# =========================================================================


def test_planissue_accepts_none_current_classification() -> None:
    iss = PlanIssue(
        file_path="x.py",
        current_classification=None,
        suggested_classification=RiskLevel.AUTO_RISKY,
        reason="NOT-BATCHED",
        issue_type="wrong_batch",
    )
    assert iss.current_classification is None


def test_precheck_not_batched_emits_none_current_classification() -> None:
    """Replaces the prior placeholder ``RiskLevel.AUTO_SAFE`` —
    NOT-BATCHED files have no batch, so no current classification."""
    fd = _make_fd("src/lost.py", risk_level=RiskLevel.AUTO_RISKY)
    plan = _make_plan([(RiskLevel.AUTO_SAFE, ["src/other.py"])])
    issues = precheck_plan_integrity(plan, [fd])
    assert len(issues) == 1
    assert issues[0].issue_type == "wrong_batch"
    assert issues[0].current_classification is None


def test_precheck_mismatch_still_carries_concrete_current_classification() -> None:
    fd = _make_fd("src/foo.py", risk_level=RiskLevel.HUMAN_REQUIRED)
    plan = _make_plan([(RiskLevel.AUTO_SAFE, ["src/foo.py"])])
    issues = precheck_plan_integrity(plan, [fd])
    assert len(issues) == 1
    assert issues[0].current_classification == RiskLevel.AUTO_SAFE


def test_build_revision_prompt_handles_none_current_classification() -> None:
    """The revision prompt must render NOT-BATCHED issues without
    raising AttributeError on ``None.value``."""
    from src.llm.prompts.planner_prompts import build_revision_prompt

    plan = _make_plan([(RiskLevel.AUTO_SAFE, ["src/other.py"])])
    issues = [
        PlanIssue(
            file_path="src/lost.py",
            current_classification=None,
            suggested_classification=RiskLevel.AUTO_RISKY,
            reason="NOT-BATCHED",
            issue_type="wrong_batch",
        )
    ]
    text = build_revision_prompt(plan, issues)
    assert "src/lost.py" in text
    assert "(not in plan)" in text
    assert "auto_risky" in text


def test_build_evaluation_prompt_handles_none_current_classification() -> None:
    from src.llm.prompts.planner_prompts import build_evaluation_prompt

    plan = _make_plan([(RiskLevel.AUTO_SAFE, ["src/other.py"])])
    issues = [
        PlanIssue(
            file_path="src/lost.py",
            current_classification=None,
            suggested_classification=RiskLevel.HUMAN_REQUIRED,
            reason="precheck NOT-BATCHED",
            issue_type="wrong_batch",
        )
    ]
    text = build_evaluation_prompt(plan, issues)
    assert "src/lost.py" in text
    assert "(not in plan)" in text


def test_classify_prior_issues_handles_none_current_classification() -> None:
    """classify_prior_issues iterates issues from prior rounds; a
    NOT-BATCHED issue with current=None must format cleanly even when
    rendered into the dispute manifest."""
    from src.llm.prompts.planner_judge_prompts import classify_prior_issues

    issues = [
        PlanIssue(
            file_path="src/lost.py",
            current_classification=None,
            suggested_classification=RiskLevel.AUTO_RISKY,
            reason="NOT-BATCHED",
            issue_type="wrong_batch",
        ),
    ]
    cls_map = {"src/lost.py": RiskLevel.AUTO_RISKY}  # planner now batches it
    resolved, still_open = classify_prior_issues(issues, cls_map)
    # The issue is resolved (planner batched the file at the
    # suggested risk level)
    assert len(resolved) == 1
    assert resolved[0].current_classification is None


# =========================================================================
# P2-4: matches_any_pattern_ci — case-insensitive risk_hint matching
# =========================================================================


def test_matches_any_pattern_ci_handles_uppercase_paths() -> None:
    from src.tools.file_classifier import matches_any_pattern_ci

    patterns = ["**/*login*", "**/*oauth*", "**/*otp*"]
    assert matches_any_pattern_ci("src/Login.py", patterns)
    assert matches_any_pattern_ci("vendor/OAuth.ts", patterns)
    assert matches_any_pattern_ci("lib/OTP_handler.go", patterns)
    # lowercase still works
    assert matches_any_pattern_ci("src/login.py", patterns)


def test_matches_any_pattern_ci_returns_false_on_no_match() -> None:
    from src.tools.file_classifier import matches_any_pattern_ci

    assert not matches_any_pattern_ci("src/main.py", ["**/*login*"])
    assert not matches_any_pattern_ci("src/main.py", [])


def test_matches_any_pattern_remains_case_sensitive() -> None:
    """Sanity guard: the original case-sensitive matcher must not be
    silently changed. forks-profile / excluded-pattern call sites rely
    on case-sensitive semantics."""
    from src.tools.file_classifier import matches_any_pattern

    # Capital "L" must NOT match lowercase pattern under the
    # case-sensitive matcher.
    assert not matches_any_pattern("src/Login.py", ["**/*login*"])
    # Lowercase still matches.
    assert matches_any_pattern("src/login.py", ["**/*login*"])


def test_compute_risk_score_now_bumps_uppercase_login_paths() -> None:
    """Behavioural payoff of switching risk_hint to CI: a file like
    ``Login.py`` (PascalCase) used to escape the hint bump; now it
    should pick it up like its lowercase twin."""
    config = FileClassifierConfig()
    fd_lower = _make_fd("src/auth/login.py", lines_added=10, lines_deleted=2)
    fd_upper = _make_fd("src/auth/Login.py", lines_added=10, lines_deleted=2)
    score_lower = compute_risk_score(fd_lower, config)
    score_upper = compute_risk_score(fd_upper, config)
    # Same hint bump applied to both — scores match within float
    # rounding tolerance.
    assert score_upper == score_lower
