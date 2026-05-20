import { describe, expect, it } from "vitest";
import { busyAgentIds, deriveAgentRuntime } from "./agents";
import type { AgentActivityEvent, SystemStatus } from "../types/state";

function ev(
  agent: string,
  event_type: AgentActivityEvent["event_type"],
  extra: Partial<AgentActivityEvent> = {},
): AgentActivityEvent {
  return { agent, action: "", phase: "", event_type, elapsed: null, ...extra };
}

function comm(
  from: string,
  to: string,
  action: string,
  ts: number,
): AgentActivityEvent {
  return {
    agent: from,
    action,
    phase: "",
    event_type: "progress",
    elapsed: null,
    target: to,
    ts,
  };
}

describe("busyAgentIds", () => {
  it("returns empty when the run is not actively executing", () => {
    const activity = [ev("executor", "start")];
    for (const s of ["awaiting_human", "completed", "failed", "paused"] as SystemStatus[]) {
      expect(busyAgentIds(s, activity).size).toBe(0);
    }
  });

  it("lights up the agent of the latest start/progress event while active", () => {
    const activity = [ev("planner", "complete"), ev("executor", "start")];
    expect([...busyAgentIds("auto_merging", activity)]).toEqual(["executor"]);
  });

  it("treats a trailing complete/error as no agent busy", () => {
    const activity = [ev("executor", "start"), ev("executor", "complete")];
    expect(busyAgentIds("auto_merging", activity).size).toBe(0);
  });

  it("returns empty for missing status or empty activity", () => {
    expect(busyAgentIds(undefined, []).size).toBe(0);
    expect(busyAgentIds("auto_merging", []).size).toBe(0);
  });
});

describe("deriveAgentRuntime", () => {
  it("marks an agent running on start with action, calls and startedAt", () => {
    const rt = deriveAgentRuntime("auto_merging", [
      ev("executor", "start", { action: "auto_merge", ts: 1000 }),
    ]);
    expect(rt.states.executor.running).toBe(true);
    expect(rt.states.executor.action).toBe("auto_merge");
    expect(rt.states.executor.calls).toBe(1);
    expect(rt.states.executor.startedAt).toBe(1000);
  });

  it("latest event wins: start then complete → idle with lastElapsed", () => {
    const rt = deriveAgentRuntime("auto_merging", [
      ev("executor", "start", { ts: 1000 }),
      ev("executor", "complete", { elapsed: 4.2 }),
    ]);
    expect(rt.states.executor.running).toBe(false);
    expect(rt.states.executor.startedAt).toBeNull();
    expect(rt.states.executor.lastElapsed).toBe(4.2);
    expect(rt.states.executor.calls).toBe(1);
  });

  it("forces every agent idle and drops comms when run is inactive", () => {
    const rt = deriveAgentRuntime("awaiting_human", [
      ev("executor", "start", { ts: 1000 }),
      comm("judge", "executor", "2 blocking issue(s)", 1001),
    ]);
    expect(rt.states.executor.running).toBe(false);
    expect(rt.comms).toHaveLength(0);
  });

  it("turns communication events into directed edges and registers both agents", () => {
    const rt = deriveAgentRuntime("auto_merging", [
      comm("judge", "executor", "2 blocking issue(s)", 1001),
    ]);
    expect(rt.comms).toEqual([
      { from: "judge", to: "executor", label: "2 blocking issue(s)", at: 1001 },
    ]);
    expect(rt.states.judge).toBeDefined();
    expect(rt.states.executor).toBeDefined();
  });
});
