import type { AppStore } from "./store.js";
import type { FileDiff, HumanDecisionRequest, RiskLevel } from "./types.js";

export function selectFilesByRisk(state: AppStore): Record<RiskLevel, FileDiff[]> {
  const groups: Record<RiskLevel, FileDiff[]> = {
    auto_safe: [],
    auto_risky: [],
    human_required: [],
    deleted_only: [],
    binary: [],
    excluded: [],
  };
  for (const fd of state.fileDiffs) {
    const level = fd.risk_level;
    if (groups[level]) {
      groups[level].push(fd);
    }
  }
  return groups;
}

export function selectPendingDecisions(state: AppStore): HumanDecisionRequest[] {
  return Object.values(state.humanDecisionRequests).filter(
    (r) => r.human_decision === null
  );
}

export function selectDecidedCount(state: AppStore): number {
  return Object.values(state.humanDecisionRequests).filter(
    (r) => r.human_decision !== null
  ).length;
}

export function selectTotalDecisionCount(state: AppStore): number {
  return Object.keys(state.humanDecisionRequests).length;
}

export function selectFilteredFiles(state: AppStore): FileDiff[] {
  if (!state.searchQuery) return state.fileDiffs;
  const q = state.searchQuery.toLowerCase();
  return state.fileDiffs.filter((f) => f.file_path.toLowerCase().includes(q));
}

export function selectPhaseOrder(): string[] {
  return [
    "analysis",
    "plan_review",
    "plan_revising",
    "auto_merge",
    "conflict_analysis",
    "human_review",
    "judge_review",
    "report",
  ];
}

export function selectRiskCounts(state: AppStore) {
  const counts = { auto_safe: 0, auto_risky: 0, human_required: 0, deleted_only: 0, binary: 0, excluded: 0 };
  for (const fd of state.fileDiffs) {
    if (fd.risk_level in counts) {
      counts[fd.risk_level as keyof typeof counts]++;
    }
  }
  return counts;
}
