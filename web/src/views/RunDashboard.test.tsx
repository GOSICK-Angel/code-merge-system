/**
 * U-W2.1 — RunDashboard budget progress bar three-state rendering.
 *
 * Asserts:
 *  (a) ratio < warn_pct          → green / data-state="ok"
 *  (b) warn_pct <= ratio < 1.0   → orange / data-state="warn"
 *  (c) ratio >= 1.0              → red / data-state="exceeded"
 *  (d) limit_usd === null        → BudgetBar hidden (no data-testid)
 */
import { beforeEach, describe, expect, it } from "vitest";
import { act, render } from "@testing-library/react";
import { useRunStore } from "../store/runStore";
import type { MergeStateSnapshot } from "../types/state";
import { RunDashboard } from "./RunDashboard";

function makeSnapshot(
  totalCost: number,
  limit: number | null,
  warnPct = 0.8,
): MergeStateSnapshot {
  return {
    runId: "run-1",
    status: "auto_merging",
    currentPhase: "auto_merge",
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
    createdAt: "2026-05-18T00:00:00",
    costSummary: {
      total_cost_usd: totalCost,
      total_tokens: 0,
      limit_usd: limit,
      warn_pct: warnPct,
    },
  } as MergeStateSnapshot;
}

function makeClientRef() {
  return { current: null } as unknown as React.MutableRefObject<unknown>;
}

beforeEach(() => {
  useRunStore.setState({
    conn: "open",
    snapshot: null,
    activity: [],
    lastCancelError: null,
  });
});

describe("RunDashboard BudgetBar", () => {
  it("(a) renders green data-state=ok when spent below warn band", () => {
    act(() => {
      useRunStore.setState({ snapshot: makeSnapshot(1.0, 5.0, 0.8) });
    });
    const { getByTestId } = render(
      <RunDashboard
        clientRef={
          makeClientRef() as unknown as React.MutableRefObject<
            import("../ws/client").WsClient | null
          >
        }
      />,
    );
    const bar = getByTestId("budget-bar");
    expect(bar.getAttribute("data-state")).toBe("ok");
    expect(bar.getAttribute("aria-valuenow")).toBe("20");
  });

  it("(b) renders orange data-state=warn at >= warn_pct", () => {
    act(() => {
      useRunStore.setState({ snapshot: makeSnapshot(4.2, 5.0, 0.8) });
    });
    const { getByTestId } = render(
      <RunDashboard
        clientRef={
          makeClientRef() as unknown as React.MutableRefObject<
            import("../ws/client").WsClient | null
          >
        }
      />,
    );
    const bar = getByTestId("budget-bar");
    expect(bar.getAttribute("data-state")).toBe("warn");
    expect(bar.getAttribute("aria-valuenow")).toBe("84");
  });

  it("(c) renders red data-state=exceeded at >= 100%", () => {
    act(() => {
      useRunStore.setState({ snapshot: makeSnapshot(5.0, 5.0, 0.8) });
    });
    const { getByTestId } = render(
      <RunDashboard
        clientRef={
          makeClientRef() as unknown as React.MutableRefObject<
            import("../ws/client").WsClient | null
          >
        }
      />,
    );
    const bar = getByTestId("budget-bar");
    expect(bar.getAttribute("data-state")).toBe("exceeded");
  });

  it("(d) hides BudgetBar when limit_usd is null", () => {
    act(() => {
      useRunStore.setState({ snapshot: makeSnapshot(1.0, null, 0.8) });
    });
    const { queryByTestId } = render(
      <RunDashboard
        clientRef={
          makeClientRef() as unknown as React.MutableRefObject<
            import("../ws/client").WsClient | null
          >
        }
      />,
    );
    expect(queryByTestId("budget-bar")).toBeNull();
  });
});
