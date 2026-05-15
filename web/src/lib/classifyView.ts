import type { MergeStateSnapshot } from "../types/state";

export type ActiveView = "dashboard" | "conflict_resolution";

/**
 * Derive the top-level view from the snapshot.
 *
 * L3 is shown when the run is parked at AWAITING_HUMAN **and** at least
 * one ``HumanDecisionRequest`` is still pending (``human_decision ===
 * null``). All other cases (initial load, planning, plan review,
 * post-cancel, completed, failed) render the L1 Dashboard.
 *
 * Plan v1.1 §4 calls out that plan-review-time AWAITING_HUMAN (no
 * conflicts) belongs to L2 (later phase) — until L2 lands we keep those
 * cases on L1 so the user still sees status + cost + activity.
 */
export function classifyView(
  snapshot: MergeStateSnapshot | null,
): ActiveView {
  if (!snapshot) return "dashboard";
  if (snapshot.status !== "awaiting_human") return "dashboard";
  const pending = Object.values(snapshot.humanDecisionRequests).some(
    (r) => r.human_decision === null,
  );
  return pending ? "conflict_resolution" : "dashboard";
}
