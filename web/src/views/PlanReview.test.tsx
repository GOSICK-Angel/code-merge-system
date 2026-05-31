/**
 * Fix 7: the top-right status pill in the PlanReview view used to be
 * hardcoded as ``AWAITING_HUMAN · {pending.length}`` even when the
 * orchestrator had moved on (a stale WS snapshot reaching the page after
 * the run progressed past plan_review left the pill lying to the user).
 *
 * These tests pin the pill to ``snapshot.status`` — the suffix logic
 * (plan sign-off / pending count / DECIDED) still applies only inside
 * the ``awaiting_human`` branch.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render } from "@testing-library/react";
import { useRunStore } from "../store/runStore";
import { usePlanReviewDraftStore } from "../store/planReviewDraftStore";
import { useWsClient } from "../ws/useWsClient";
import type { MergeStateSnapshot } from "../types/state";
import type { OutboundMessage } from "../ws/messages";
import { PlanReview } from "./PlanReview";

vi.mock("../ws/useWsClient", () => ({
  useWsClient: vi.fn(),
}));

const baseSnapshot: MergeStateSnapshot = {
  runId: "run-1",
  status: "awaiting_human",
  currentPhase: "plan_review",
  phaseResults: {},
  mergePlan: null,
  fileClassifications: {},
  fileDiffs: [],
  fileDecisionRecords: {},
  humanDecisionRequests: {},
  humanDecisions: {},
  judgeVerdict: null,
  judgeRepairRounds: 0,
  planReviewLog: [],
  reviewConclusion: null,
  pendingUserDecisions: [],
  gateHistory: [],
  errors: [],
  messages: [],
  memory: { phase_summaries: {}, entries: [] },
  createdAt: "2026-05-14T00:00:00",
};

beforeEach(() => {
  useRunStore.setState({
    conn: "open",
    snapshot: baseSnapshot,
    activity: [],
    lastCancelError: null,
  });
  usePlanReviewDraftStore.setState({ drafts: {}, notes: "" });
  const send = vi.fn();
  vi.mocked(useWsClient).mockReturnValue({
    current: { send, close: vi.fn(), pendingCount: () => 0 },
  } as unknown as React.MutableRefObject<{
    send: (msg: OutboundMessage) => void;
    close: () => void;
    pendingCount: () => number;
  }>);
});

function makeClientRef() {
  return {
    current: { send: vi.fn(), close: vi.fn(), pendingCount: () => 0 },
  };
}

describe("PlanReview status pill", () => {
  it("shows AWAITING_HUMAN suffix when status === 'awaiting_human' with 0 pending", () => {
    useRunStore.setState({
      snapshot: { ...baseSnapshot, status: "awaiting_human" },
    });
    const ref = makeClientRef();
    const { container } = render(
      <PlanReview
        clientRef={
          ref as unknown as React.MutableRefObject<
            ReturnType<typeof useWsClient>["current"]
          >
        }
      />,
    );
    expect(container.textContent).toContain("AWAITING_HUMAN");
  });

  it("pill reflects actual status when orchestrator already moved on (judge_reviewing)", () => {
    // Stale snapshot scenario: WS pushed an awaiting_human snapshot
    // earlier, but the orchestrator already advanced past plan_review.
    // The pill must say JUDGE_REVIEWING — NOT lie with AWAITING_HUMAN.
    useRunStore.setState({
      snapshot: { ...baseSnapshot, status: "judge_reviewing" },
    });
    const ref = makeClientRef();
    const { container } = render(
      <PlanReview
        clientRef={
          ref as unknown as React.MutableRefObject<
            ReturnType<typeof useWsClient>["current"]
          >
        }
      />,
    );
    expect(container.textContent).toContain("JUDGE_REVIEWING");
    // And it must NOT claim AWAITING_HUMAN at the same time.
    const pillCandidates = Array.from(
      container.querySelectorAll<HTMLElement>("[class*='pill']"),
    ).map((el) => el.textContent ?? "");
    expect(
      pillCandidates.some((t) => /AWAITING_HUMAN/.test(t)),
    ).toBe(false);
  });

  it("pill reflects auto_merging when run is mid-merge", () => {
    useRunStore.setState({
      snapshot: { ...baseSnapshot, status: "auto_merging" },
    });
    const ref = makeClientRef();
    const { container } = render(
      <PlanReview
        clientRef={
          ref as unknown as React.MutableRefObject<
            ReturnType<typeof useWsClient>["current"]
          >
        }
      />,
    );
    expect(container.textContent).toContain("AUTO_MERGING");
  });
});

describe("PlanReview submit lock + timeout warning", () => {
  function renderWithSendSpy(): {
    sendSpy: ReturnType<typeof vi.fn>;
    container: HTMLElement;
    rerender: (ui: React.ReactElement) => void;
  } {
    const sendSpy = vi.fn();
    const ref = {
      current: { send: sendSpy, close: vi.fn(), pendingCount: () => 0 },
    } as unknown as React.MutableRefObject<
      ReturnType<typeof useWsClient>["current"]
    >;
    const { container, rerender } = render(<PlanReview clientRef={ref} />);
    return { sendSpy, container, rerender };
  }

  it("locks the action row to SUBMITTING… once Approve fires and surfaces no warning before the 3s SLA", () => {
    vi.useFakeTimers();
    try {
      useRunStore.setState({
        snapshot: {
          ...baseSnapshot,
          reviewConclusion: {
            reason: "approved",
            final_round: 1,
            total_rounds: 1,
            max_rounds: 2,
            summary: "ok",
            pending_decisions_count: 0,
            rejection_details: [],
          },
        },
      });
      const { sendSpy, container } = renderWithSendSpy();
      const approve = container.querySelector(
        "[data-testid=plan-review-approve]",
      ) as HTMLButtonElement;
      const modify = container.querySelector(
        "[data-testid=plan-review-modify]",
      ) as HTMLButtonElement;
      const reject = container.querySelector(
        "[data-testid=plan-review-reject]",
      ) as HTMLButtonElement;
      expect(approve).toBeTruthy();
      expect(approve.disabled).toBe(false);

      act(() => {
        fireEvent.click(approve);
      });

      expect(approve.textContent).toContain("SUBMITTING");
      expect(approve.disabled).toBe(true);
      expect(modify.disabled).toBe(true);
      expect(reject.disabled).toBe(true);
      expect(sendSpy).toHaveBeenCalledTimes(2);
      expect(
        container.querySelector("[data-testid=plan-review-submit-warning]"),
      ).toBeNull();

      act(() => {
        vi.advanceTimersByTime(2500);
      });
      expect(
        container.querySelector("[data-testid=plan-review-submit-warning]"),
      ).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("surfaces the NO ACK banner once 3s have elapsed without a planHumanReview snapshot", () => {
    vi.useFakeTimers();
    try {
      useRunStore.setState({
        snapshot: {
          ...baseSnapshot,
          reviewConclusion: {
            reason: "approved",
            final_round: 1,
            total_rounds: 1,
            max_rounds: 2,
            summary: "ok",
            pending_decisions_count: 0,
            rejection_details: [],
          },
        },
      });
      const { container } = renderWithSendSpy();
      const approve = container.querySelector(
        "[data-testid=plan-review-approve]",
      ) as HTMLButtonElement;
      act(() => {
        fireEvent.click(approve);
      });

      act(() => {
        vi.advanceTimersByTime(3100);
      });

      const banner = container.querySelector(
        "[data-testid=plan-review-submit-warning]",
      );
      expect(banner).toBeTruthy();
      expect(banner?.textContent).toContain("NO ACK");
    } finally {
      vi.useRealTimers();
    }
  });

  it("releases the lock when a snapshot carrying plan_human_review arrives", () => {
    vi.useFakeTimers();
    try {
      useRunStore.setState({
        snapshot: {
          ...baseSnapshot,
          reviewConclusion: {
            reason: "approved",
            final_round: 1,
            total_rounds: 1,
            max_rounds: 2,
            summary: "ok",
            pending_decisions_count: 0,
            rejection_details: [],
          },
        },
      });
      const { container } = renderWithSendSpy();
      const approve = container.querySelector(
        "[data-testid=plan-review-approve]",
      ) as HTMLButtonElement;
      act(() => {
        fireEvent.click(approve);
      });
      expect(approve.disabled).toBe(true);

      act(() => {
        useRunStore.setState((s) => ({
          snapshot: s.snapshot
            ? {
                ...s.snapshot,
                planHumanReview: {
                  decision: "approve",
                  reviewer_name: "web_user",
                  reviewer_notes: null,
                  decided_at: "2026-05-19T09:40:00",
                  item_decisions_count: 0,
                },
              }
            : s.snapshot,
        }));
      });

      // The banner stays hidden because decided_at change clears
      // submission before the SLA fires.
      act(() => {
        vi.advanceTimersByTime(3100);
      });
      expect(
        container.querySelector("[data-testid=plan-review-submit-warning]"),
      ).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("PlanReview round 2 — plan approved, fresh items pending", () => {
  // Background: auto_merge can surface new ``pending_user_decisions``
  // items (e.g. ``conflict_markers_<path>`` for files where the
  // cherry-pick fall-back wrote conflict markers) AFTER the user has
  // already plan-level approved. Previously ``serverDecided=true``
  // would disable every action button, leaving the reviewer staring at
  // a HUMAN_REQUIRED file they could not resolve.
  const round2Snapshot: MergeStateSnapshot = {
    ...baseSnapshot,
    status: "awaiting_human",
    reviewConclusion: {
      reason: "approved",
      final_round: 1,
      total_rounds: 1,
      max_rounds: 2,
      summary: "ok",
      pending_decisions_count: 0,
      rejection_details: [],
    },
    planHumanReview: {
      decision: "approve",
      reviewer_name: "web_user",
      reviewer_notes: null,
      decided_at: "2026-05-19T10:37:57",
      item_decisions_count: 2,
    },
    pendingUserDecisions: [
      // Round-1 items already decided server-side.
      {
        item_id: "i-1",
        file_path: "models/auth/auth_token.go",
        description: "",
        risk_context: "",
        conflict_preview: "",
        current_classification: "human_required",
        options: [],
        user_choice: "take_target",
        user_input: null,
        custom_instruction: null,
        manual_resolution: null,
      },
      // Round-2 item — surfaced by auto_merge, awaiting decision.
      {
        item_id: "i-conflict",
        file_path: "models/user/user.go",
        description: "conflict markers",
        risk_context: "unresolved_conflict_markers",
        conflict_preview: "<<<<<<< HEAD",
        current_classification: "human_required",
        options: [
          {
            key: "approve_human",
            label: "Manual review",
            description: "",
            kind: "llm_default",
            preview: null,
          },
          {
            key: "take_target",
            label: "Take upstream",
            description: "",
            kind: "llm_default",
            preview: null,
          },
        ],
        user_choice: null,
        user_input: null,
        custom_instruction: null,
        manual_resolution: null,
      },
    ],
  };

  function renderWithSendSpy(): {
    sendSpy: ReturnType<typeof vi.fn>;
    container: HTMLElement;
  } {
    const sendSpy = vi.fn();
    const ref = {
      current: { send: sendSpy, close: vi.fn(), pendingCount: () => 0 },
    } as unknown as React.MutableRefObject<
      ReturnType<typeof useWsClient>["current"]
    >;
    const { container } = render(<PlanReview clientRef={ref} />);
    return { sendSpy, container };
  }

  it("keeps APPROVE ALL enabled when plan_human_review is set but pending items remain", () => {
    useRunStore.setState({ snapshot: round2Snapshot });
    const { container } = renderWithSendSpy();
    const approve = container.querySelector(
      "[data-testid=plan-review-approve]",
    ) as HTMLButtonElement;
    expect(approve).toBeTruthy();
    expect(approve.disabled).toBe(false);
    // Modify/Reject are plan-level decisions — round 2 is item-only,
    // so they stay disabled to prevent contradictory submissions.
    const modify = container.querySelector(
      "[data-testid=plan-review-modify]",
    ) as HTMLButtonElement;
    const reject = container.querySelector(
      "[data-testid=plan-review-reject]",
    ) as HTMLButtonElement;
    expect(modify.disabled).toBe(true);
    expect(reject.disabled).toBe(true);
  });

  it("ack signal is the decided_at advancing, not the planHumanReview->null transition", () => {
    vi.useFakeTimers();
    try {
      useRunStore.setState({ snapshot: round2Snapshot });
      const { container, sendSpy } = renderWithSendSpy();
      const approve = container.querySelector(
        "[data-testid=plan-review-approve]",
      ) as HTMLButtonElement;

      act(() => {
        fireEvent.click(approve);
      });

      expect(sendSpy).toHaveBeenCalledTimes(2);
      // The button is locked into SUBMITTING… and stays there until a
      // FRESH decided_at lands — without the fix the lock would clear
      // instantly because ``serverDecided`` was already true.
      expect(approve.textContent).toContain("SUBMITTING");
      expect(approve.disabled).toBe(true);

      // Snapshot with the same decided_at must NOT clear submission.
      act(() => {
        useRunStore.setState((s) => ({ snapshot: { ...s.snapshot! } }));
      });
      expect(approve.textContent).toContain("SUBMITTING");

      // Advancing decided_at simulates the bridge writing a fresh
      // ``plan_human_review`` after applying the new items.
      act(() => {
        useRunStore.setState((s) => ({
          snapshot: {
            ...s.snapshot!,
            planHumanReview: {
              ...s.snapshot!.planHumanReview!,
              decided_at: "2026-05-19T10:45:00",
            },
          },
        }));
      });
      expect(approve.textContent).not.toContain("SUBMITTING");
    } finally {
      vi.useRealTimers();
    }
  });

  it("status pill in round 2 shows AWAITING_HUMAN · N MORE so the reviewer doesn't read it as 'DECIDED'", () => {
    useRunStore.setState({ snapshot: round2Snapshot });
    const { container } = renderWithSendSpy();
    // Look at every pill — at least one must include the round-2 hint.
    const pillTexts = Array.from(
      container.querySelectorAll<HTMLElement>("[class*='pill']"),
    ).map((el) => el.textContent ?? "");
    expect(pillTexts.some((t) => /MORE/i.test(t))).toBe(true);
    // Must NOT confuse the reviewer with a green DECIDED badge.
    expect(pillTexts.some((t) => /DECIDED/i.test(t))).toBe(false);
  });
});
