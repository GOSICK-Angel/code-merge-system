import { describe, expect, it } from "vitest";
import { classifyView } from "./classifyView";
import type { MergeStateSnapshot } from "../types/state";

const base: MergeStateSnapshot = {
  runId: "r1",
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

describe("classifyView", () => {
  it("returns setup when mode='setup' regardless of snapshot", () => {
    expect(classifyView(null, "setup")).toBe("setup");
    expect(classifyView({ ...base, status: "completed" }, "setup")).toBe(
      "setup",
    );
  });

  it("returns dashboard when snapshot is null", () => {
    expect(classifyView(null)).toBe("dashboard");
  });

  it("returns dashboard when status is not awaiting_human", () => {
    expect(classifyView({ ...base, status: "planning" })).toBe("dashboard");
    expect(classifyView({ ...base, status: "auto_merging" })).toBe("dashboard");
  });

  it("returns conflict_resolution when awaiting_human + pending requests", () => {
    const snap: MergeStateSnapshot = {
      ...base,
      status: "awaiting_human",
      humanDecisionRequests: {
        "a.py": {
          request_id: "r1",
          file_path: "a.py",
          priority: 1,
          conflict_points: [],
          context_summary: "",
          upstream_change_summary: "",
          fork_change_summary: "",
          analyst_recommendation: null,
          analyst_confidence: null,
          analyst_rationale: "",
          options: [],
          human_decision: null,
          custom_content: null,
          reviewer_notes: null,
          related_files: [],
        },
      },
    };
    expect(classifyView(snap)).toBe("conflict_resolution");
  });

  it("returns dashboard when awaiting_human but all requests decided", () => {
    const snap: MergeStateSnapshot = {
      ...base,
      status: "awaiting_human",
      humanDecisionRequests: {
        "a.py": {
          request_id: "r1",
          file_path: "a.py",
          priority: 1,
          conflict_points: [],
          context_summary: "",
          upstream_change_summary: "",
          fork_change_summary: "",
          analyst_recommendation: null,
          analyst_confidence: null,
          analyst_rationale: "",
          options: [],
          human_decision: "take_current",
          custom_content: null,
          reviewer_notes: null,
          related_files: [],
        },
      },
    };
    expect(classifyView(snap)).toBe("dashboard");
  });

  it("returns dashboard when awaiting_human but no requests at all", () => {
    expect(classifyView({ ...base, status: "awaiting_human" })).toBe(
      "dashboard",
    );
  });

  it("returns plan_review when awaiting_human + pendingUserDecisions undecided", () => {
    const snap: MergeStateSnapshot = {
      ...base,
      status: "awaiting_human",
      pendingUserDecisions: [
        {
          item_id: "i1",
          file_path: "a.py",
          description: "decide",
          options: [{ key: "k", label: "L", description: "" }],
          user_choice: null,
          user_input: null,
        },
      ],
    };
    expect(classifyView(snap)).toBe("plan_review");
  });

  it("plan_review takes priority over conflict_resolution when both pending", () => {
    const snap: MergeStateSnapshot = {
      ...base,
      status: "awaiting_human",
      pendingUserDecisions: [
        {
          item_id: "i1",
          file_path: "a.py",
          description: "decide",
          options: [],
          user_choice: null,
          user_input: null,
        },
      ],
      humanDecisionRequests: {
        "b.py": {
          request_id: "r1",
          file_path: "b.py",
          priority: 1,
          conflict_points: [],
          context_summary: "",
          upstream_change_summary: "",
          fork_change_summary: "",
          analyst_recommendation: null,
          analyst_confidence: null,
          analyst_rationale: "",
          options: [],
          human_decision: null,
          custom_content: null,
          reviewer_notes: null,
          related_files: [],
        },
      },
    };
    expect(classifyView(snap)).toBe("plan_review");
  });

  it("returns conflict_resolution when all plan items decided but conflicts pending", () => {
    const snap: MergeStateSnapshot = {
      ...base,
      status: "awaiting_human",
      pendingUserDecisions: [
        {
          item_id: "i1",
          file_path: "a.py",
          description: "decide",
          options: [],
          user_choice: "k1",
          user_input: null,
        },
      ],
      humanDecisionRequests: {
        "b.py": {
          request_id: "r1",
          file_path: "b.py",
          priority: 1,
          conflict_points: [],
          context_summary: "",
          upstream_change_summary: "",
          fork_change_summary: "",
          analyst_recommendation: null,
          analyst_confidence: null,
          analyst_rationale: "",
          options: [],
          human_decision: null,
          custom_content: null,
          reviewer_notes: null,
          related_files: [],
        },
      },
    };
    expect(classifyView(snap)).toBe("conflict_resolution");
  });

  it("returns dashboard when all plan items decided and no conflict requests", () => {
    const snap: MergeStateSnapshot = {
      ...base,
      status: "awaiting_human",
      pendingUserDecisions: [
        {
          item_id: "i1",
          file_path: "a.py",
          description: "decide",
          options: [],
          user_choice: "k1",
          user_input: null,
        },
      ],
    };
    expect(classifyView(snap)).toBe("dashboard");
  });

  it("returns report when status is completed", () => {
    expect(classifyView({ ...base, status: "completed" })).toBe("report");
  });

  it("returns report when status is failed", () => {
    expect(classifyView({ ...base, status: "failed" })).toBe("report");
  });

  it("returns judge_verdict when awaiting_human + verdict + no resolution", () => {
    const snap: MergeStateSnapshot = {
      ...base,
      status: "awaiting_human",
      judgeVerdict: {
        verdict: "fail",
        summary: "issues",
        failed_files: ["a.py"],
        passed_files: [],
        conditional_files: [],
        reviewed_files_count: 1,
        critical_issues_count: 1,
        high_issues_count: 0,
        overall_confidence: 0.8,
        blocking_issues: [],
        issues: [],
        veto_triggered: true,
        veto_reason: "test",
        repair_instructions: [],
      },
    };
    expect(classifyView(snap)).toBe("judge_verdict");
  });

  it("returns dashboard when judge verdict present but resolution recorded", () => {
    const snap: MergeStateSnapshot = {
      ...base,
      status: "awaiting_human",
      judgeVerdict: {
        verdict: "fail",
        summary: "issues",
        failed_files: ["a.py"],
        passed_files: [],
        conditional_files: [],
        reviewed_files_count: 1,
        critical_issues_count: 1,
        high_issues_count: 0,
        overall_confidence: 0.8,
        blocking_issues: [],
        issues: [],
        veto_triggered: false,
        veto_reason: null,
        repair_instructions: [],
      },
      judgeResolution: "accept",
    };
    expect(classifyView(snap)).toBe("dashboard");
  });

  it("plan_review takes priority over judge_verdict", () => {
    const snap: MergeStateSnapshot = {
      ...base,
      status: "awaiting_human",
      pendingUserDecisions: [
        {
          item_id: "i1",
          file_path: "a.py",
          description: "decide",
          options: [],
          user_choice: null,
          user_input: null,
        },
      ],
      judgeVerdict: {
        verdict: "fail",
        summary: "",
        failed_files: [],
        passed_files: [],
        conditional_files: [],
        reviewed_files_count: 0,
        critical_issues_count: 0,
        high_issues_count: 0,
        overall_confidence: 0.8,
        blocking_issues: [],
        issues: [],
        veto_triggered: false,
        veto_reason: null,
        repair_instructions: [],
      },
    };
    expect(classifyView(snap)).toBe("plan_review");
  });

  it("report wins over judge_verdict when both are present (terminal status)", () => {
    const snap: MergeStateSnapshot = {
      ...base,
      status: "completed",
      judgeVerdict: {
        verdict: "fail",
        summary: "",
        failed_files: [],
        passed_files: [],
        conditional_files: [],
        reviewed_files_count: 0,
        critical_issues_count: 0,
        high_issues_count: 0,
        overall_confidence: 0.8,
        blocking_issues: [],
        issues: [],
        veto_triggered: false,
        veto_reason: null,
        repair_instructions: [],
      },
    };
    expect(classifyView(snap)).toBe("report");
  });
});
