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
import { render } from "@testing-library/react";
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
    current: { send, close: vi.fn() },
  } as unknown as React.MutableRefObject<{
    send: (msg: OutboundMessage) => void;
    close: () => void;
  }>);
});

function makeClientRef() {
  return {
    current: { send: vi.fn(), close: vi.fn() },
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
