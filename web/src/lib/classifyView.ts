import type { MergeStateSnapshot } from "../types/state";

export type ActiveView = "dashboard" | "plan_review" | "conflict_resolution";

/**
 * Derive the top-level view from the snapshot.
 *
 * Priority at ``AWAITING_HUMAN``:
 *   L2 (plan_review) > L3 (conflict_resolution) > L1 (dashboard)
 *
 * Why this order: ``pending_user_decisions`` is produced by the
 * plan_review phase (``src/core/phases/plan_review.py``) while
 * ``human_decision_requests`` is produced later by conflict_analysis /
 * auto_merge. Structurally the two lists are populated by different
 * phases and shouldn't both be non-empty at the same time, but the
 * ordering here is defensive: if a future code path leaves stale plan
 * items in state when entering conflict review, the user still gets
 * routed to the plan review first (the more upstream decision).
 *
 * All other cases (initial load, planning, post-cancel, completed,
 * failed, paused) render the L1 Dashboard.
 */
export function classifyView(
  snapshot: MergeStateSnapshot | null,
): ActiveView {
  if (!snapshot) return "dashboard";
  if (snapshot.status !== "awaiting_human") return "dashboard";

  const planPending = snapshot.pendingUserDecisions.some(
    (item) => item.user_choice === null,
  );
  if (planPending) return "plan_review";

  const conflictPending = Object.values(snapshot.humanDecisionRequests).some(
    (r) => r.human_decision === null,
  );
  if (conflictPending) return "conflict_resolution";

  return "dashboard";
}
