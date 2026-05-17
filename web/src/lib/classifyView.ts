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
 *   2. ``awaiting_human`` + pending plan-review items → L2 plan_review
 *   3. ``awaiting_human`` + pending conflict requests → L3 conflict_resolution
 *   4. ``awaiting_human`` + judge_verdict present + no resolution → L4 judge_verdict
 *   5. Everything else → L1 dashboard
 *
 * Why this order:
 *   - Terminal states win regardless of any in-flight state — once the
 *     run is done the user wants the report, not the half-stale gates
 *   - Plan-review > conflict_resolution: ``pending_user_decisions`` is
 *     populated upstream of ``human_decision_requests`` (the former in
 *     ``src/core/phases/plan_review.py``, the latter in
 *     ``src/core/phases/conflict_analysis.py`` / ``auto_merge.py``).
 *     If the two ever coexist (stale state), route to the upstream
 *     decision first
 *   - L4 lives at the bottom of the awaiting_human stack because the
 *     judge gate is the *last* checkpoint — we should never land here
 *     while plan_review or conflict items are still pending
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

  return "dashboard";
}
