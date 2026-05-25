/**
 * Auto-routing coverage: when the orchestrator parks at an actionable
 * awaiting_human gate (here: the judge verdict), the app must pull the user
 * to that view proactively — they should never have to discover it via the
 * nav "OPEN" badge. Also asserts the badge does not invite premature clicks
 * while the judge is still computing (status judge_reviewing).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render } from "@testing-library/react";
import { useRunStore } from "./store/runStore";
import { useWsClient } from "./ws/useWsClient";
import type { MergeStateSnapshot, JudgeVerdict } from "./types/state";
import { App } from "./App";

vi.mock("./ws/useWsClient", () => ({ useWsClient: vi.fn() }));

const verdict: JudgeVerdict = {
  verdict: "fail",
  summary: "syntax error in models/user/user.go",
  failed_files: ["models/user/user.go"],
  passed_files: [],
  conditional_files: ["models/auth/auth_token.go"],
  reviewed_files_count: 16,
  critical_issues_count: 1,
  high_issues_count: 1,
  overall_confidence: 0.7,
  blocking_issues: [],
  issues: [],
  veto_triggered: false,
  veto_reason: null,
  repair_instructions: [],
};

const base: MergeStateSnapshot = {
  runId: "run-1",
  status: "judge_reviewing",
  currentPhase: "judge_review",
  phaseResults: {},
  mergePlan: null,
  fileClassifications: {},
  fileDiffs: [],
  fileDecisionRecords: {},
  humanDecisionRequests: {},
  humanDecisions: {},
  judgeVerdict: verdict,
  judgeResolution: null,
  judgeRepairRounds: 1,
  planReviewLog: [],
  reviewConclusion: null,
  pendingUserDecisions: [],
  gateHistory: [],
  errors: [],
  messages: [],
  memory: { phase_summaries: {}, entries: [] },
  createdAt: "2026-05-25T00:00:00",
};

beforeEach(() => {
  useRunStore.setState({ conn: "open", snapshot: base, mode: "run", activity: [] });
  vi.mocked(useWsClient).mockReturnValue({
    current: { send: vi.fn(), close: vi.fn(), pendingCount: () => 0 },
  } as unknown as ReturnType<typeof useWsClient>);
});

describe("App auto-routing to the judge gate", () => {
  it("stays on the dashboard while the judge is still computing (judge_reviewing)", () => {
    const { container } = render(<App />);
    expect(container.textContent).toContain("Live merge");
    // The verdict's action buttons must not be reachable yet.
    expect(container.textContent).not.toContain("Pick an action above");
  });

  it("auto-navigates to the judge verdict view when the gate opens", () => {
    const { container } = render(<App />);
    expect(container.textContent).toContain("Live merge");

    act(() => {
      useRunStore.setState({ snapshot: { ...base, status: "awaiting_human" } });
    });

    expect(container.textContent).toContain("VERDICT:");
    expect(container.textContent).toContain("Pick an action above");
  });

  it("pulls the user back to the gate even after a manual detour to the dashboard", () => {
    const { container, getByText } = render(<App />);
    act(() => {
      useRunStore.setState({ snapshot: { ...base, status: "awaiting_human" } });
    });
    // Manual detour: user clicks the dashboard nav to inspect progress.
    act(() => {
      getByText("DASHBOARD").click();
    });
    // A fresh snapshot push while still parked at the gate must re-assert.
    act(() => {
      useRunStore.setState({
        snapshot: { ...base, status: "awaiting_human", judgeRepairRounds: 1 },
      });
    });
    expect(container.textContent).toContain("VERDICT:");
  });
});
