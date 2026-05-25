import type { MergeStateSnapshot } from "../types/state";

export type ActiveView =
  | "setup"
  | "dashboard"
  | "plan_review"
  | "conflict_resolution"
  | "judge_verdict"
  | "report";

export type StoreMode = "setup" | "run";

/**
 * Derive the top-level view from the snapshot.
 *
 * Routing rules (highest priority first):
 *   1. Terminal status (``completed`` / ``failed``) → L5 report
 *   2. ``awaiting_human`` + pending per-file plan decisions → L2 plan_review
 *   3. ``awaiting_human`` + pending conflict requests → L3 conflict_resolution
 *   4. ``awaiting_human`` + judge_verdict present + no resolution → L4 judge_verdict
 *   5. ``awaiting_human`` + reviewConclusion present + planHumanReview
 *      not yet recorded → L2 plan_review (plan-level sign-off fallback — the
 *      planner/judge loop terminated, so per-file pendingUserDecisions may be
 *      empty but the reviewer still has to approve / modify / reject the plan)
 *   6. Everything else → L1 dashboard
 *
 * Why this order:
 *   - Terminal states win regardless of any in-flight state — once the
 *     run is done the user wants the report, not the half-stale gates
 *   - The plan-level sign-off fallback (rule 5) is checked AFTER the
 *     conflict/judge gates, not before. When a plan is auto-approved (no
 *     HUMAN_REQUIRED files) the orchestrator skips sign-off and leaves
 *     ``planHumanReview`` null while ``reviewConclusion`` is set — so that
 *     condition stays true for the rest of the run. Checking it before the
 *     conflict/judge gates would mask a live downstream gate (the operator
 *     would be parked on an already-approved plan while 3 conflict files wait
 *     for a decision). A plan that genuinely still needs sign-off has no
 *     pending conflicts and no judge verdict, so it falls through to rule 5.
 *   - L4 (judge) sits above the plan fallback but below conflicts because the
 *     judge gate is the *last* real checkpoint of a run.
 */
export function classifyView(
  snapshot: MergeStateSnapshot | null,
  mode: StoreMode = "run",
): ActiveView {
  // Setup mode wins outright. The orchestrator hasn't started yet, so any
  // snapshot lingering from a prior run is stale and must not drive
  // routing — otherwise a reconfigure flow would briefly flash the old
  // dashboard before the form mounts.
  if (mode === "setup") return "setup";
  if (!snapshot) return "dashboard";

  if (snapshot.status === "completed" || snapshot.status === "failed") {
    return "report";
  }

  if (snapshot.status !== "awaiting_human") return "dashboard";

  const planPending = snapshot.pendingUserDecisions.some(
    (item) => item.user_choice === null,
  );
  if (planPending) return "plan_review";

  const conflictPending = Object.values(snapshot.humanDecisionRequests).some(
    (r) => r.human_decision === null,
  );
  if (conflictPending) return "conflict_resolution";

  if (
    snapshot.judgeVerdict !== null &&
    (snapshot.judgeResolution ?? null) === null
  ) {
    return "judge_verdict";
  }

  // Plan-level sign-off fallback — see the "Why this order" note above.
  // Checked last among the awaiting_human gates: when a plan is auto-approved
  // the orchestrator skips sign-off and leaves planHumanReview null while
  // reviewConclusion is set, so this would otherwise mask a live conflict or
  // judge gate downstream.
  if (
    snapshot.reviewConclusion != null &&
    (snapshot.planHumanReview ?? null) == null
  ) {
    return "plan_review";
  }

  return "dashboard";
}
