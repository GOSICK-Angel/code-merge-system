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

from src.core.phases.base import ActivityEvent  # noqa: E402
from src.models.conflict import (  # noqa: E402
    ChangeIntent,
    ConflictPoint,
    ConflictType,
)
from src.models.config import MergeConfig  # noqa: E402
from src.models.decision import MergeDecision  # noqa: E402
from src.models.human import (  # noqa: E402
    DecisionOption,
    HumanDecisionRequest,
)
from src.models.plan import MergePhase  # noqa: E402
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


async def _eventually_park_at_human(bridge: MergeWSBridge, state: MergeState) -> None:
    """After the warm-up, flip to AWAITING_HUMAN and inject conflict
    requests so the front-end ``classifyView`` derives the L3 view. The
    real submit_decision / submit_conflict_decisions_batch handlers run
    from this point — exactly the production code path."""
    await asyncio.sleep(10)
    requests = _make_conflict_requests()
    for req in requests:
        state.human_decision_requests[req.file_path] = req
    state.status = SystemStatus.AWAITING_HUMAN
    bridge.notify_state_change("parked at AWAITING_HUMAN with 4 conflicts (mock)")
    logger.info(
        "State -> AWAITING_HUMAN with %d conflict requests (L3 should open)",
        len(requests),
    )


async def main() -> None:
    state = _make_state()
    bridge = MergeWSBridge(state)
    await bridge.start("localhost", 8765)
    logger.info("Mock bridge ready on ws://localhost:8765")
    logger.info("Open http://localhost:5173/?ws=8765 in a browser.")

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
        await bridge.stop()
        logger.info("Mock bridge stopped.")


if __name__ == "__main__":
    asyncio.run(main())
