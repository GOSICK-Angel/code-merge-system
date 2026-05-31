import { create } from "zustand";
import type { OutboundMessage } from "../ws/messages";
import type { PendingUserDecision } from "../types/state";

export interface PlanReviewDraft {
  user_choice: string;
  user_input: string;
}

interface PlanReviewDraftStoreState {
  drafts: Record<string, PlanReviewDraft>; // keyed by item_id
  notes: string; // shared reviewer_notes for Approve/Modify/Reject
  setDraft: (itemId: string, choice: string) => void;
  setDraftInput: (itemId: string, input: string) => void;
  clearDraft: (itemId: string) => void;
  setNotes: (notes: string) => void;
  applyRecommendedToAll: (items: PendingUserDecision[]) => number;
  reset: () => void;
}

function emptyDraft(choice: string): PlanReviewDraft {
  return { user_choice: choice, user_input: "" };
}

/**
 * Local-only draft buffer for L2 plan review decisions.
 *
 * Plan v1.1 §P1-3 (the "two-step submit" rule) — single-item choices
 * land here and *do not* round-trip to the backend. Only the top-level
 * Approve/Reject/Modify buttons translate the buffered state into
 * outbound WS messages, via ``commitApprove`` / ``commitReject`` /
 * ``commitModify`` below.
 *
 * Why this is load-bearing: the back-end ``_apply_user_plan_decisions``
 * (ws_bridge.py:308-336) *immediately* sets
 * ``plan_human_review.decision = APPROVE`` and signals the
 * orchestrator's ``_plan_review_received`` event when items arrive.
 * Sending plan-item decisions before the user has actually approved
 * would race the orchestrator into running auto-merge against a plan
 * the user is still reviewing.
 */
export const usePlanReviewDraftStore = create<PlanReviewDraftStoreState>(
  (set) => ({
    drafts: {},
    notes: "",
    setDraft: (itemId, choice) =>
      set((state) => {
        const existing = state.drafts[itemId] ?? emptyDraft(choice);
        return {
          drafts: { ...state.drafts, [itemId]: { ...existing, user_choice: choice } },
        };
      }),
    setDraftInput: (itemId, input) =>
      set((state) => {
        // Auto-create a notes-only draft (``user_choice=""``) when the
        // item has no selectable options — the reviewer's text input is
        // the only signal they can provide. ``buildItemsPayload`` will
        // serialize ``user_choice=""`` correctly.
        const existing = state.drafts[itemId] ?? {
          user_choice: "",
          user_input: "",
        };
        return {
          drafts: { ...state.drafts, [itemId]: { ...existing, user_input: input } },
        };
      }),
    clearDraft: (itemId) =>
      set((state) => {
        // eslint-disable-next-line @typescript-eslint/no-unused-vars
        const { [itemId]: _omit, ...rest } = state.drafts;
        return { drafts: rest };
      }),
    setNotes: (notes) => set({ notes }),
    applyRecommendedToAll: (items) => {
      let applied = 0;
      set((state) => {
        const next = { ...state.drafts };
        for (const item of items) {
          if (item.user_choice !== null) continue; // already decided server-side
          // M15 audit: ``options[0]`` is the CONSERVATIVE DEFAULT, not an
          // analyst-recommended choice. ``src/core/phases/plan_review.py:1020-1068``
          // builds the option list with ``keep_head`` first (preserves fork
          // edits / discards upstream — the safest no-op for the reviewer to
          // accept) followed by ``take_target`` and ``llm_auto_merge``. Plan
          // v1.1 §4 L2 calls this UX "Apply recommended" but the semantics
          // are "apply conservative default everywhere"; future work could
          // surface a true analyst pick (cf. L3's analyst_recommendation
          // field on HumanDecisionRequest).
          const recommended = item.options[0]?.key;
          if (!recommended) continue;
          const existing = next[item.item_id] ?? emptyDraft(recommended);
          next[item.item_id] = { ...existing, user_choice: recommended };
          applied += 1;
        }
        return { drafts: next };
      });
      return applied;
    },
    reset: () => set({ drafts: {}, notes: "" }),
  }),
);

/**
 * Build the ``submit_user_plan_decisions`` items list covering every
 * pending item — including items the reviewer left undecided (e.g.
 * the items with no options at all, or simply skipped). Each item
 * gets the drafted choice if present, otherwise ``user_choice=""``
 * (back-end ``UserDecisionItem.user_choice`` is ``str | None``; empty
 * string distinguishes "explicitly skipped by reviewer" from "never
 * surfaced to user" while staying type-compatible with the wire
 * schema's string-only field).
 *
 * Why we include un-drafted items: the back-end iterates the wire
 * items list, not the server-side pending list. Sending only drafted
 * items leaves no-option / no-draft items with stale defaults and
 * masks the reviewer's explicit "approve everything, leave the rest"
 * intent (e.g. a NEW_FILE item with only notes guidance).
 */
function buildItemsPayload(
  pending: Array<{ item_id: string }>,
  drafts: Record<string, PlanReviewDraft>,
): Array<{ item_id: string; user_choice: string; user_input?: string }> {
  return pending.map((p) => {
    const d = drafts[p.item_id];
    return {
      item_id: p.item_id,
      user_choice: d?.user_choice ?? "",
      user_input: d?.user_input || undefined,
    };
  });
}

/**
 * Two-step commit helper for Approve.
 *
 * Sends ``submit_user_plan_decisions`` first (covering every pending
 * item — see ``buildItemsPayload``) and then ``submit_plan_review``
 * with ``decision=approve``. Both frames go through the same WS
 * client; ordering is preserved by the underlying ws.send() queue.
 */
export function commitApprove(
  send: (msg: OutboundMessage) => void,
  pending: Array<{ item_id: string }>,
  drafts: Record<string, PlanReviewDraft>,
  notes: string,
): void {
  send({
    type: "submit_user_plan_decisions",
    payload: { items: buildItemsPayload(pending, drafts) },
  });
  send({
    type: "submit_plan_review",
    payload: { decision: "approve", notes: notes || undefined },
  });
}

/**
 * Reject path — by design **does not** flush draft items. The user is
 * abandoning the plan; sending their pending choices would let the
 * back-end auto-approve (see ws_bridge.py:329-335) and race the
 * orchestrator. Only ``submit_plan_review {reject}`` is sent.
 */
export function commitReject(
  send: (msg: OutboundMessage) => void,
  notes: string,
): void {
  send({
    type: "submit_plan_review",
    payload: { decision: "reject", notes: notes || undefined },
  });
}

/**
 * Modify path — send the drafts so the planner sees the user's
 * preferred choices, then issue ``modify`` so the orchestrator routes
 * back to PLAN_REVISING for another round. Same two-frame ordering as
 * Approve, same full-coverage rule for items.
 */
export function commitModify(
  send: (msg: OutboundMessage) => void,
  pending: Array<{ item_id: string }>,
  drafts: Record<string, PlanReviewDraft>,
  notes: string,
): void {
  send({
    type: "submit_user_plan_decisions",
    payload: { items: buildItemsPayload(pending, drafts) },
  });
  send({
    type: "submit_plan_review",
    payload: { decision: "modify", notes: notes || undefined },
  });
}
