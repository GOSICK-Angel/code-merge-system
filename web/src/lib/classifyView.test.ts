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

  it("returns dashboard when awaiting_human but no requests at all (plan-review case)", () => {
    expect(classifyView({ ...base, status: "awaiting_human" })).toBe(
      "dashboard",
    );
  });
});
