import { describe, it, expect, beforeEach } from "vitest";
import { useRunStore } from "./runStore";
import type {
  AgentActivityEvent,
  MergeStateSnapshot,
} from "../types/state";

const baseSnapshot: MergeStateSnapshot = {
  runId: "abc-123",
  status: "planning",
  currentPhase: "analysis",
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

function makeEvent(action: string): AgentActivityEvent {
  return {
    agent: "planner",
    action,
    phase: "analysis",
    event_type: "progress",
    elapsed: null,
  };
}

describe("runStore", () => {
  beforeEach(() => {
    useRunStore.setState({
      conn: "connecting",
      snapshot: null,
      activity: [],
      lastCancelError: null,
    });
  });

  it("setConn updates the connection state", () => {
    useRunStore.getState().setConn("open");
    expect(useRunStore.getState().conn).toBe("open");
  });

  it("applySnapshot replaces the snapshot", () => {
    useRunStore.getState().applySnapshot(baseSnapshot);
    expect(useRunStore.getState().snapshot?.runId).toBe("abc-123");
  });

  it("appendActivity respects the 200-event cap", () => {
    const { appendActivity } = useRunStore.getState();
    for (let i = 0; i < 250; i++) {
      appendActivity(makeEvent(`step-${i}`));
    }
    const { activity } = useRunStore.getState();
    expect(activity.length).toBe(200);
    expect(activity[0].action).toBe("step-50");
    expect(activity[199].action).toBe("step-249");
  });

  it("replaceActivity resets the buffer", () => {
    useRunStore.getState().appendActivity(makeEvent("first"));
    useRunStore.getState().replaceActivity([
      makeEvent("replay-1"),
      makeEvent("replay-2"),
    ]);
    const { activity } = useRunStore.getState();
    expect(activity.length).toBe(2);
    expect(activity[0].action).toBe("replay-1");
  });

  it("setCancelError / clearCancelError round-trip", () => {
    useRunStore.getState().setCancelError({
      reason: "not_in_human_gate",
      current_status: "planning",
    });
    expect(useRunStore.getState().lastCancelError?.reason).toBe(
      "not_in_human_gate",
    );
    useRunStore.getState().clearCancelError();
    expect(useRunStore.getState().lastCancelError).toBeNull();
  });
});
