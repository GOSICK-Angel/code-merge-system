"""Mock MergeWSBridge driver for Web UI development.

Boots a real ``MergeWSBridge`` (so the on-wire schema is identical to
production) bound to a fabricated ``MergeState``, then drip-feeds a few
agent activity frames and a state-change transition so the L1 Dashboard
has something interesting to render. Cancel command echoes follow the
production code path — no shims.

Run alongside ``cd web && npm run dev``; see ``web/dev/README.md``.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Make ``src.*`` importable regardless of CWD — the script can be launched
# from anywhere (``python web/dev/mock-bridge.py`` or from inside web/).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import os  # noqa: E402

from src.core.phases.base import ActivityEvent  # noqa: E402
from src.models.conflict import (  # noqa: E402
    ChangeIntent,
    ConflictPoint,
    ConflictType,
)
from src.models.config import MergeConfig  # noqa: E402
from src.models.decision import MergeDecision  # noqa: E402
from src.models.diff import FileChangeCategory, RiskLevel  # noqa: E402
from src.models.human import (  # noqa: E402
    DecisionOption,
    HumanDecisionRequest,
)
from src.models.plan import (  # noqa: E402
    CategorySummary,
    MergeLayer,
    MergePhase,
    MergePlan,
    PhaseFileBatch,
    RiskSummary,
)
from src.models.plan_review import (  # noqa: E402
    DecisionOption as PlanDecisionOption,
)
from src.models.plan_review import (  # noqa: E402
    NegotiationMessage,
    PlanDiffEntry,
    PlannerIssueResponse,
    PlanReviewRound,
    UserDecisionItem,
)
from src.models.plan_judge import PlanJudgeResult  # noqa: E402
from src.models.state import MergeState, PhaseResult, SystemStatus  # noqa: E402
from src.web.ws_bridge import MergeWSBridge  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("mock-bridge")


def _make_state() -> MergeState:
    cfg = MergeConfig(upstream_ref="upstream/main", fork_ref="feat/web")
    state = MergeState(config=cfg)
    state.status = SystemStatus.PLANNING
    start = datetime.now() - timedelta(seconds=42)
    state.phase_results["analysis"] = PhaseResult(
        phase=MergePhase.ANALYSIS,
        status="completed",
        started_at=start,
        completed_at=start + timedelta(seconds=12),
    )
    state.phase_results["plan_review"] = PhaseResult(
        phase=MergePhase.PLAN_REVIEW,
        status="running",
        started_at=start + timedelta(seconds=12),
    )
    state.cost_summary = {
        "total_cost_usd": 0.4231,
        "total_tokens": 18_452,
        "by_agent": {
            "planner": {"cost_usd": 0.21, "tokens": 9_200},
            "conflict_analyst": {"cost_usd": 0.21, "tokens": 9_252},
        },
    }
    return state


async def _drip_activity(bridge: MergeWSBridge) -> None:
    """Stream a few agent activity events so the L1 stream is non-empty."""
    sample = [
        ("planner", "Drafting layered plan", "analysis", "start", None),
        ("planner", "Layer 0 routed: 12 files", "analysis", "progress", 1.2),
        ("planner", "Plan v1 emitted (3 layers)", "analysis", "complete", 5.4),
        ("planner_judge", "Reviewing plan v1", "plan_review", "start", None),
        (
            "planner_judge",
            "2 risk_score deltas accepted",
            "plan_review",
            "progress",
            2.1,
        ),
    ]
    for agent, action, phase, evt, elapsed in sample:
        bridge.notify_agent_activity(
            ActivityEvent(
                agent=agent,
                action=action,
                phase=phase,
                event_type=evt,
                elapsed=elapsed,
            )
        )
        await asyncio.sleep(1.5)


def _standard_options() -> list[DecisionOption]:
    """5 selectable options per HumanDecisionRequest (ESCALATE_HUMAN omitted
    — the analyst already escalated by surfacing the request; the user
    chooses among actionable outcomes per plan v1.1 §4 L3 rule)."""
    return [
        DecisionOption(
            option_key="opt_take_current",
            decision=MergeDecision.TAKE_CURRENT,
            description="Preserve fork edits as-is",
        ),
        DecisionOption(
            option_key="opt_take_target",
            decision=MergeDecision.TAKE_TARGET,
            description="Accept upstream version",
        ),
        DecisionOption(
            option_key="opt_semantic_merge",
            decision=MergeDecision.SEMANTIC_MERGE,
            description="Semantic merge — combine both intents",
            risk_warning="Requires reviewer validation",
        ),
        DecisionOption(
            option_key="opt_manual_patch",
            decision=MergeDecision.MANUAL_PATCH,
            description="Provide a hand-written patch",
        ),
        DecisionOption(
            option_key="opt_skip",
            decision=MergeDecision.SKIP,
            description="Skip this file in the merge",
            risk_warning="Defers the conflict to a later run",
        ),
    ]


def _intent(description: str, intent_type: str, confidence: float) -> ChangeIntent:
    return ChangeIntent(
        description=description, intent_type=intent_type, confidence=confidence
    )


def _make_conflict_requests() -> list[HumanDecisionRequest]:
    now = datetime.now()
    return [
        HumanDecisionRequest(
            file_path="api/auth.py",
            priority=8,
            conflict_points=[
                ConflictPoint(
                    file_path="api/auth.py",
                    hunk_id="hunk-1",
                    conflict_type=ConflictType.INTERFACE_CHANGE,
                    upstream_intent=_intent(
                        "Switch sign-in endpoint to take a token-only payload",
                        "refactor",
                        0.9,
                    ),
                    fork_intent=_intent(
                        "Add cvte-sso-id header on top of existing payload",
                        "feature",
                        0.8,
                    ),
                    can_coexist=False,
                    suggested_decision=MergeDecision.TAKE_CURRENT,
                    confidence=0.85,
                    rationale="Fork uses cvte-sso integration; upstream refactor strips the header",
                    risk_factors=["regression-risk", "auth-flow"],
                ),
                ConflictPoint(
                    file_path="api/auth.py",
                    hunk_id="hunk-2",
                    conflict_type=ConflictType.DEPENDENCY_UPDATE,
                    upstream_intent=_intent("Bump pyjwt to 2.x", "dep-bump", 0.95),
                    fork_intent=_intent(
                        "Pin pyjwt 1.7 — required by cvte-sso shim",
                        "compat-pin",
                        0.7,
                    ),
                    can_coexist=False,
                    suggested_decision=MergeDecision.TAKE_CURRENT,
                    confidence=0.55,
                    rationale="Shim API changed in 2.x; needs deeper migration",
                    risk_factors=["dep-pin"],
                ),
            ],
            context_summary="Auth flow conflict — fork integrates cvte-sso, upstream did a token refactor.",
            upstream_change_summary=(
                "def sign_in(token: str) -> User:\n"
                "    payload = jwt.decode(token, _SECRET, algorithms=['HS256'])\n"
                "    user = User.get(payload['sub'])\n"
                "    if not user:\n"
                "        raise AuthError('unknown user')\n"
                "    return user\n"
            ),
            fork_change_summary=(
                "def sign_in(token: str, sso_id: str | None = None) -> User:\n"
                "    payload = jwt.decode(token, _SECRET, algorithms=['HS256'])\n"
                "    user = User.get(payload['sub'])\n"
                "    if user is None and sso_id:\n"
                "        user = cvte_sso.provision(sso_id)\n"
                "    if not user:\n"
                "        raise AuthError('unknown user')\n"
                "    return user\n"
            ),
            analyst_recommendation=MergeDecision.TAKE_CURRENT,
            analyst_confidence=0.85,
            analyst_rationale="Fork's cvte-sso provisioning is project-critical; upstream refactor drops that path.",
            options=_standard_options(),
            created_at=now,
        ),
        HumanDecisionRequest(
            file_path="config/database.yaml",
            priority=6,
            conflict_points=[
                ConflictPoint(
                    file_path="config/database.yaml",
                    hunk_id="hunk-1",
                    conflict_type=ConflictType.CONFIGURATION,
                    upstream_intent=_intent("Add pool_recycle: 3600", "tuning", 0.6),
                    fork_intent=_intent("Set custom pool_size: 30", "tuning", 0.5),
                    can_coexist=True,
                    suggested_decision=MergeDecision.SEMANTIC_MERGE,
                    confidence=0.35,
                    rationale="Both keys can coexist; safe to merge",
                    risk_factors=[],
                ),
            ],
            context_summary="Database connection pool tuning conflict (both sides additive).",
            upstream_change_summary=(
                "database:\n"
                "  url: postgresql://...\n"
                "  pool_recycle: 3600  # added upstream\n"
                "  pool_size: 10\n"
            ),
            fork_change_summary=(
                "database:\n"
                "  url: postgresql://...\n"
                "  pool_size: 30  # fork raised for batch workload\n"
            ),
            analyst_recommendation=MergeDecision.SEMANTIC_MERGE,
            analyst_confidence=0.7,
            analyst_rationale="Both edits are additive and well-named; semantic merge keeps both knobs.",
            options=_standard_options(),
            created_at=now,
        ),
        HumanDecisionRequest(
            file_path="utils/retry.py",
            priority=4,
            conflict_points=[
                ConflictPoint(
                    file_path="utils/retry.py",
                    hunk_id="hunk-1",
                    conflict_type=ConflictType.LOGIC_CONTRADICTION,
                    upstream_intent=_intent(
                        "Cap retries at 5 with exponential backoff",
                        "policy",
                        0.95,
                    ),
                    fork_intent=_intent(
                        "Remove retry loop in favour of caller-side logic",
                        "refactor",
                        0.9,
                    ),
                    can_coexist=False,
                    suggested_decision=MergeDecision.TAKE_TARGET,
                    confidence=0.92,
                    rationale="Upstream policy is the team-wide standard; fork's deletion was scoped to one caller",
                    risk_factors=["behavioral-shift"],
                ),
                ConflictPoint(
                    file_path="utils/retry.py",
                    hunk_id="hunk-2",
                    conflict_type=ConflictType.LOGIC_CONTRADICTION,
                    upstream_intent=_intent("Add jitter to backoff", "policy", 0.85),
                    fork_intent=_intent(
                        "(deleted)",
                        "deletion",
                        0.9,
                    ),
                    can_coexist=False,
                    suggested_decision=MergeDecision.TAKE_TARGET,
                    confidence=0.8,
                    rationale="Jitter is recommended for the upstream policy; fork's deletion would lose it",
                    risk_factors=[],
                ),
            ],
            context_summary="Retry policy: upstream tightened, fork ripped it out.",
            upstream_change_summary=(
                "def with_retry(fn, *, max_retries=5):\n"
                "    for attempt in range(max_retries):\n"
                "        try:\n"
                "            return fn()\n"
                "        except TransientError:\n"
                "            sleep(2**attempt + jitter())\n"
                "    raise\n"
            ),
            fork_change_summary=(
                "# retry loop removed; callers must handle their own backoff\n"
                "def call(fn):\n"
                "    return fn()\n"
            ),
            analyst_recommendation=MergeDecision.TAKE_TARGET,
            analyst_confidence=0.92,
            analyst_rationale="Upstream's policy is more conservative and reusable across callers.",
            options=_standard_options(),
            created_at=now,
        ),
        HumanDecisionRequest(
            file_path="docs/CHANGELOG.md",
            priority=2,
            conflict_points=[
                ConflictPoint(
                    file_path="docs/CHANGELOG.md",
                    hunk_id="hunk-1",
                    conflict_type=ConflictType.CONCURRENT_MODIFICATION,
                    upstream_intent=_intent("Document v2.1.0 release", "docs", 0.5),
                    fork_intent=_intent("Document cvte-2.1.0 release", "docs", 0.5),
                    can_coexist=True,
                    suggested_decision=MergeDecision.SEMANTIC_MERGE,
                    confidence=0.3,
                    rationale="Two changelog entries near each other; safe to interleave",
                    risk_factors=[],
                ),
            ],
            context_summary="Both sides added release notes; minor markdown conflict.",
            upstream_change_summary=(
                "## v2.1.0 — 2026-04-30\n"
                "- Switch sign_in to token-only payload\n"
                "- Bump pyjwt to 2.x\n"
            ),
            fork_change_summary=(
                "## cvte-2.1.0 — 2026-04-30\n"
                "- Add cvte-sso provisioning to sign_in\n"
                "- Tune connection pool for batch workload\n"
            ),
            analyst_recommendation=MergeDecision.ESCALATE_HUMAN,
            analyst_confidence=0.2,
            analyst_rationale="Analyst is unsure of the release-notes style preferred for the merged repo.",
            options=_standard_options(),
            created_at=now,
        ),
    ]


def _make_merge_plan() -> MergePlan:
    """3-layer / 8-batch fabricated plan covering low/medium/high/critical
    risk levels and a spread of FileChangeCategory values so the L2
    ``PlanTree`` widget renders a useful demo."""
    return MergePlan(
        created_at=datetime.now(),
        upstream_ref="upstream/main",
        fork_ref="feat/web",
        merge_base_commit="abc1234",
        layers=[
            MergeLayer(
                layer_id=0,
                name="Cosmetic & docs",
                description="Low-risk surface edits — safe to land first",
                depends_on=[],
            ),
            MergeLayer(
                layer_id=1,
                name="Refactor backbone",
                description="Plumbing and dependency updates",
                depends_on=[0],
            ),
            MergeLayer(
                layer_id=2,
                name="Feature integration",
                description="Feature work that depends on the new backbone",
                depends_on=[1],
            ),
        ],
        phases=[
            PhaseFileBatch(
                batch_id="L0-cosmetic",
                phase=MergePhase.AUTO_MERGE,
                file_paths=["README.md", "docs/usage.md"],
                risk_level=RiskLevel.AUTO_SAFE,
                layer_id=0,
                change_category=FileChangeCategory.B,
            ),
            PhaseFileBatch(
                batch_id="L0-docs",
                phase=MergePhase.AUTO_MERGE,
                file_paths=["docs/api.md"],
                risk_level=RiskLevel.AUTO_SAFE,
                layer_id=0,
                change_category=FileChangeCategory.A,
            ),
            PhaseFileBatch(
                batch_id="L1-refactor",
                phase=MergePhase.AUTO_MERGE,
                file_paths=["utils/__init__.py", "utils/io.py"],
                risk_level=RiskLevel.AUTO_RISKY,
                layer_id=1,
                change_category=FileChangeCategory.C,
            ),
            PhaseFileBatch(
                batch_id="L1-bugfix",
                phase=MergePhase.AUTO_MERGE,
                file_paths=["utils/retry.py"],
                risk_level=RiskLevel.AUTO_RISKY,
                layer_id=1,
                change_category=FileChangeCategory.C,
            ),
            PhaseFileBatch(
                batch_id="L1-deps",
                phase=MergePhase.AUTO_MERGE,
                file_paths=["pyproject.toml"],
                risk_level=RiskLevel.HUMAN_REQUIRED,
                layer_id=1,
                change_category=FileChangeCategory.B,
            ),
            PhaseFileBatch(
                batch_id="L2-feature",
                phase=MergePhase.AUTO_MERGE,
                file_paths=["src/payment.py", "src/checkout.py"],
                risk_level=RiskLevel.AUTO_RISKY,
                layer_id=2,
                change_category=FileChangeCategory.C,
            ),
            PhaseFileBatch(
                batch_id="L2-breaking",
                phase=MergePhase.AUTO_MERGE,
                file_paths=["models/user.py"],
                risk_level=RiskLevel.HUMAN_REQUIRED,
                layer_id=2,
                change_category=FileChangeCategory.C,
            ),
            PhaseFileBatch(
                batch_id="L2-tests",
                phase=MergePhase.AUTO_MERGE,
                file_paths=["tests/test_auth.py"],
                risk_level=RiskLevel.AUTO_SAFE,
                layer_id=2,
                change_category=FileChangeCategory.C,
            ),
        ],
        risk_summary=RiskSummary(
            total_files=12,
            auto_safe_count=5,
            auto_risky_count=4,
            human_required_count=3,
            deleted_only_count=0,
            binary_count=0,
            excluded_count=0,
            estimated_auto_merge_rate=0.75,
            top_risk_files=["pyproject.toml", "models/user.py"],
        ),
        category_summary=CategorySummary(
            total_files=12,
            a_unchanged=1,
            b_upstream_only=3,
            c_both_changed=8,
        ),
        project_context_summary=(
            "Mock fork — Python web service with payment + auth plumbing.\n"
            "Upstream just refactored utils/ and bumped pyjwt to 2.x."
        ),
        special_instructions=[
            "Layer 2 must wait for Layer 1 refactor to land first.",
            "pyproject.toml dep bump needs human eyes — coordinate with infra.",
        ],
    )


def _make_plan_review_log() -> list[PlanReviewRound]:
    ts = datetime.now()
    return [
        PlanReviewRound(
            round_number=1,
            verdict_result=PlanJudgeResult.REVISION_NEEDED,
            verdict_summary=(
                "5 risk-score concerns: 2 underestimates and 1 cycle risk "
                "in Layer 2 → Layer 1."
            ),
            issues_count=5,
            issues_detail=[
                {"file": "models/user.py", "reason": "schema migration not isolated"},
                {"file": "pyproject.toml", "reason": "pyjwt 2.x is breaking"},
            ],
            planner_revision_summary="Promoted pyproject and models/user to HUMAN_REQUIRED.",
            planner_responses=[
                PlannerIssueResponse(
                    issue_id="i1",
                    file_path="models/user.py",
                    action="accept",
                    reason="agree, escalate to HUMAN_REQUIRED",
                ),
                PlannerIssueResponse(
                    issue_id="i2",
                    file_path="pyproject.toml",
                    action="accept",
                    reason="dep bump impact higher than initial estimate",
                ),
                PlannerIssueResponse(
                    issue_id="i3",
                    file_path="utils/retry.py",
                    action="accept",
                    reason="upgrade to AUTO_RISKY",
                ),
                PlannerIssueResponse(
                    issue_id="i4",
                    file_path="src/payment.py",
                    action="reject",
                    reason="risk already covered by L2 ordering constraint",
                ),
                PlannerIssueResponse(
                    issue_id="i5",
                    file_path="docs/api.md",
                    action="discuss",
                    reason="needs reviewer input on doc-only ranking",
                ),
            ],
            plan_diff=[
                PlanDiffEntry(
                    file_path="models/user.py",
                    old_risk="auto_risky",
                    new_risk="human_required",
                ),
                PlanDiffEntry(
                    file_path="pyproject.toml",
                    old_risk="auto_safe",
                    new_risk="human_required",
                ),
            ],
            negotiation_messages=[
                NegotiationMessage(
                    sender="planner_judge",
                    round_number=1,
                    content="schema migration in models/user.py crosses layer boundary",
                    timestamp=ts,
                ),
                NegotiationMessage(
                    sender="planner",
                    round_number=1,
                    content="agree, will isolate as a Layer 2 breaking-change batch",
                    timestamp=ts,
                ),
            ],
            timestamp=ts,
        ),
        PlanReviewRound(
            round_number=2,
            verdict_result=PlanJudgeResult.APPROVED,
            verdict_summary="All revisions accepted; plan ready for human review.",
            issues_count=0,
            planner_revision_summary=None,
            planner_responses=[],
            plan_diff=[],
            negotiation_messages=[
                NegotiationMessage(
                    sender="planner_judge",
                    round_number=2,
                    content="All concerns addressed. Plan approved.",
                    timestamp=ts + timedelta(seconds=8),
                )
            ],
            timestamp=ts + timedelta(seconds=8),
        ),
    ]


def _make_user_decisions() -> list[UserDecisionItem]:
    """6 pending plan-review decisions covering 5 actionable shapes + 1
    no-options notes-only edge case (matches the L2 widget validation
    matrix; the last item must not block ``Approve all``)."""
    standard = [
        PlanDecisionOption(key="take_upstream", label="Take upstream version"),
        PlanDecisionOption(key="take_fork", label="Keep fork edits"),
        PlanDecisionOption(key="manual", label="Provide manual patch"),
    ]
    skip = [
        PlanDecisionOption(key="take_upstream", label="Take upstream version"),
        PlanDecisionOption(key="take_fork", label="Keep fork edits"),
        PlanDecisionOption(key="skip", label="Skip this file"),
    ]
    return [
        UserDecisionItem(
            item_id="i1_auth",
            file_path="config/auth.yaml",
            description="Auth config diverges from upstream — sensitive",
            risk_context="security-sensitive config drift",
            conflict_preview="",
            current_classification="HUMAN_REQUIRED",
            options=standard,
        ),
        UserDecisionItem(
            item_id="i2_payment",
            file_path="src/payment.py",
            description="Refund flow conflict",
            risk_context="behavioral-shift",
            conflict_preview=(
                "<<<<<<< upstream\n"
                "def refund(order):\n"
                "    return gateway.refund(order.id)\n"
                "=======\n"
                "def refund(order, reason=None):\n"
                "    if not order.refundable:\n"
                "        raise RefundError('not refundable')\n"
                "    return gateway.refund(order.id, reason=reason)\n"
                ">>>>>>> fork\n"
            ),
            current_classification="HUMAN_REQUIRED",
            options=skip,
        ),
        UserDecisionItem(
            item_id="i3_readme",
            file_path="README.md",
            description="Docs banner conflict",
            risk_context="cosmetic",
            current_classification="AUTO_SAFE",
            options=[
                PlanDecisionOption(key="take_upstream", label="Take upstream"),
                PlanDecisionOption(key="take_fork", label="Keep fork"),
            ],
        ),
        UserDecisionItem(
            item_id="i4_user_model",
            file_path="models/user.py",
            description="User model schema migration",
            risk_context="breaking-change — affects DB migration",
            conflict_preview=(
                "<<<<<<< upstream\n"
                "class User(BaseModel):\n"
                "    id: int\n"
                "    email: EmailStr\n"
                "    created_at: datetime\n"
                "=======\n"
                "class User(BaseModel):\n"
                "    id: int\n"
                "    email: EmailStr\n"
                "    cvte_sso_id: str | None = None  # fork addition\n"
                "    created_at: datetime\n"
                ">>>>>>> fork\n"
            ),
            current_classification="HUMAN_REQUIRED",
            options=[
                PlanDecisionOption(
                    key="manual",
                    label="Manual schema patch",
                    description="Reviewer writes a combined migration",
                ),
                PlanDecisionOption(
                    key="escalate",
                    label="Escalate to migration team",
                ),
            ],
        ),
        UserDecisionItem(
            item_id="i5_tests",
            file_path="tests/test_auth.py",
            description="Auth tests need updating",
            risk_context="test-coverage",
            current_classification="AUTO_MERGE_FAILED",
            options=skip,
        ),
        # No-options path — verifies Approve all does NOT require this
        # item to be drafted (plan v1.1 §P1-3 + team-lead boundary check)
        UserDecisionItem(
            item_id="i6_migration",
            file_path="docs/migration.md",
            description="New migration guide — reviewer may add notes",
            risk_context="docs-only, new file",
            current_classification="NEW_FILE",
            options=[],
        ),
    ]


def _make_judge_verdict() -> Any:
    """Fabricated JudgeVerdict that exercises the L4 view: veto banner,
    multi-file issue grouping, severity ladder, repair instructions."""
    from src.models.judge import (
        IssueResolvability,
        IssueSeverity,
        JudgeIssue,
        JudgeVerdict,
        RepairInstruction,
        VerdictType,
    )

    return JudgeVerdict(
        verdict=VerdictType.FAIL,
        reviewed_files_count=12,
        passed_files=["README.md", "docs/usage.md", "utils/io.py"],
        failed_files=["models/user.py", "src/payment.py"],
        conditional_files=["pyproject.toml"],
        issues=[
            JudgeIssue(
                file_path="models/user.py",
                issue_level=IssueSeverity.CRITICAL,
                issue_type="customization_grep_below_baseline",
                description="cvte_sso_id field annotation lost in merged version",
                affected_lines=[12, 45, 89],
                evidence_excerpt="-    cvte_sso_id: str | None = None",
                suggested_fix="re-add cvte_sso_id column with the same default",
                must_fix_before_merge=True,
                resolvability=IssueResolvability.FIXABLE,
            ),
            JudgeIssue(
                file_path="models/user.py",
                issue_level=IssueSeverity.HIGH,
                issue_type="top_level_invocation_lost",
                description="@cvte_audit_log decorator missing on save()",
                affected_lines=[120],
                must_fix_before_merge=True,
                resolvability=IssueResolvability.FIXABLE,
            ),
            JudgeIssue(
                file_path="src/payment.py",
                issue_level=IssueSeverity.HIGH,
                issue_type="smoke_test_failed",
                description="Refund flow regression — test_refund_with_reason failing",
                must_fix_before_merge=True,
                resolvability=IssueResolvability.HUMAN_REQUIRED,
            ),
        ],
        critical_issues_count=1,
        high_issues_count=2,
        overall_confidence=0.88,
        summary="2 files failed quality gates; customization preservation breach in models/user.py",
        blocking_issues=["models/user.py", "src/payment.py"],
        timestamp=datetime.now(),
        judge_model="claude-sonnet-4-6",
        veto_triggered=True,
        veto_reason="Customization grep count below baseline (models/user.py)",
        repair_instructions=[
            RepairInstruction(
                file_path="models/user.py",
                instruction="Re-add ``cvte_sso_id`` column and ``@cvte_audit_log`` decorator",
                severity=IssueSeverity.CRITICAL,
                is_repairable=True,
                source_issue_id=None,
            ),
            RepairInstruction(
                file_path="src/payment.py",
                instruction="Manually reconcile refund flow with upstream gateway signature",
                severity=IssueSeverity.HIGH,
                is_repairable=False,
                source_issue_id=None,
            ),
        ],
    )


def _populate_report_artifacts(repo_root: Path, run_id: str) -> None:
    """Write a fake ``merge_report.md`` + ``checkpoint.json`` under
    ``<repo_root>/.merge/runs/<run_id>/`` so the L5 view has artifacts
    to fetch via the static_server ``/runs/`` route."""
    run_dir = repo_root / ".merge" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "merge_report.md").write_text(
        "# Merge Report — run "
        + run_id[:8]
        + "\n\n"
        + "## Summary\n\n"
        + "Merged upstream/main into feat/web with 4 conflicts resolved by reviewer.\n"
        + "Judge verdict: ``conditional`` (1 high-severity issue resolved offline).\n\n"
        + "## Files\n\n"
        + "- ``api/auth.py`` — TAKE_CURRENT\n"
        + "- ``config/database.yaml`` — SEMANTIC_MERGE\n"
        + "- ``utils/retry.py`` — TAKE_TARGET\n"
        + "- ``docs/CHANGELOG.md`` — MANUAL_PATCH\n\n"
        + "## Cost\n\n"
        + "Total: $0.4231 across 18,452 tokens (planner + planner_judge).\n\n"
        + "```\n"
        + "completed at "
        + datetime.now().isoformat()
        + "\n"
        + "```\n",
        encoding="utf-8",
    )
    (run_dir / "checkpoint.json").write_text(
        '{"run_id": "' + run_id + '", "status": "completed"}',
        encoding="utf-8",
    )
    (run_dir / "plan_review.md").write_text(
        "# Plan Review — run "
        + run_id[:8]
        + "\n\n"
        + "2 negotiation rounds → APPROVED.\n",
        encoding="utf-8",
    )


async def _eventually_park_at_human(bridge: MergeWSBridge, state: MergeState) -> None:
    """After the warm-up, flip to AWAITING_HUMAN.

    ``MOCK_VIEW`` env var selects the scenario:
      * ``conflict`` (default) — inject 4 HumanDecisionRequest objects so
        ``classifyView`` derives L3
      * ``plan_review`` — populate ``merge_plan`` + ``plan_review_log`` +
        ``pending_user_decisions`` so ``classifyView`` derives L2
      * ``judge`` — set ``judge_verdict`` (veto_triggered) and leave
        ``judge_resolution`` None so ``classifyView`` derives L4
      * ``report`` — set ``status=COMPLETED`` so ``classifyView`` derives L5;
        also writes fake ``runs/<run_id>/merge_report.md`` for the route
    """
    await asyncio.sleep(10)
    scenario = os.environ.get("MOCK_VIEW", "conflict").lower()
    if scenario == "judge":
        from src.models.plan import MergePhase as _MergePhase

        state.judge_verdict = _make_judge_verdict()
        state.current_phase = _MergePhase.JUDGE_REVIEW
        state.status = SystemStatus.AWAITING_HUMAN
        bridge.notify_state_change("parked at AWAITING_HUMAN with judge verdict (mock)")
        logger.info("State -> AWAITING_HUMAN with judge_verdict (L4 should open)")
        return
    if scenario == "report":
        _populate_report_artifacts(_REPO_ROOT, state.run_id)
        state.status = SystemStatus.COMPLETED
        bridge.notify_state_change("parked at COMPLETED (mock report)")
        logger.info(
            "State -> COMPLETED with fake report at .merge/runs/%s/ (L5 should open)",
            state.run_id,
        )
        return
    if scenario == "plan_review":
        state.merge_plan = _make_merge_plan()
        state.plan_review_log = _make_plan_review_log()
        state.pending_user_decisions = _make_user_decisions()
        state.status = SystemStatus.AWAITING_HUMAN
        bridge.notify_state_change(
            f"parked at AWAITING_HUMAN with {len(state.pending_user_decisions)} "
            "plan-review items (mock)"
        )
        logger.info(
            "State -> AWAITING_HUMAN with %d pending_user_decisions (L2 should open)",
            len(state.pending_user_decisions),
        )
    else:
        requests = _make_conflict_requests()
        for req in requests:
            state.human_decision_requests[req.file_path] = req
        state.status = SystemStatus.AWAITING_HUMAN
        bridge.notify_state_change(
            f"parked at AWAITING_HUMAN with {len(requests)} conflicts (mock)"
        )
        logger.info(
            "State -> AWAITING_HUMAN with %d conflict requests (L3 should open)",
            len(requests),
        )


async def main() -> None:
    from src.web.static_server import StaticHTTPServer

    state = _make_state()
    bridge = MergeWSBridge(state)
    await bridge.start("localhost", 8765)
    logger.info("Mock bridge ready on ws://localhost:8765")
    logger.info("Open http://localhost:5173/?ws=8765 in a browser.")

    # Mount a runs-only static server on 5174 so the Vite dev server's
    # ``/runs`` proxy has somewhere to route L5 artifact fetches in dev
    # mode. Production reuses the main 5173 port via web.py.
    runs_root = _REPO_ROOT / ".merge" / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    runs_server = StaticHTTPServer(runs_root, runs_root=runs_root)
    await runs_server.start("localhost", 5174)
    logger.info("Runs artifact server ready on http://localhost:5174")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    drip_task = asyncio.create_task(_drip_activity(bridge))
    park_task = asyncio.create_task(_eventually_park_at_human(bridge, state))

    try:
        await stop.wait()
    finally:
        drip_task.cancel()
        park_task.cancel()
        await runs_server.stop()
        await bridge.stop()
        logger.info("Mock bridge stopped.")


if __name__ == "__main__":
    asyncio.run(main())
