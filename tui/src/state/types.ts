// TypeScript mirrors of Python models in src/models/

export type SystemStatus =
  | "initialized"
  | "planning"
  | "plan_reviewing"
  | "plan_revising"
  | "auto_merging"
  | "plan_dispute_pending"
  | "analyzing_conflicts"
  | "awaiting_human"
  | "judge_reviewing"
  | "generating_report"
  | "completed"
  | "failed"
  | "paused";

export type MergePhase =
  | "analysis"
  | "plan_review"
  | "plan_revising"
  | "auto_merge"
  | "conflict_analysis"
  | "human_review"
  | "judge_review"
  | "report";

export type RiskLevel =
  | "auto_safe"
  | "auto_risky"
  | "human_required"
  | "deleted_only"
  | "binary"
  | "excluded";

export type MergeDecision =
  | "take_current"
  | "take_target"
  | "three_way_merge"
  | "semantic_merge"
  | "manual_patch"
  | "escalate_human"
  | "skip";

export type VerdictType = "pass" | "conditional" | "fail";

export type FileChangeCategory =
  | "A"
  | "B"
  | "C"
  | "D_MISSING"
  | "D_EXTRA"
  | "E";

export interface PhaseResult {
  phase: MergePhase;
  status: "pending" | "running" | "completed" | "failed" | "skipped";
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
}

export interface RiskSummary {
  total_files: number;
  auto_safe_count: number;
  auto_risky_count: number;
  human_required_count: number;
  deleted_only_count: number;
  binary_count: number;
  excluded_count: number;
  estimated_auto_merge_rate: number;
  top_risk_files: string[];
}

export interface CategorySummary {
  total_files: number;
  a_unchanged: number;
  b_upstream_only: number;
  c_both_changed: number;
  d_missing: number;
  d_extra: number;
  e_current_only: number;
}

export interface PhaseFileBatch {
  batch_id: string;
  phase: MergePhase;
  file_paths: string[];
  risk_level: RiskLevel;
  layer_id: number | null;
  change_category: FileChangeCategory | null;
}

export interface MergeLayer {
  layer_id: number;
  name: string;
  description: string;
  depends_on: number[];
}

export interface MergePlan {
  plan_id: string;
  created_at: string;
  upstream_ref: string;
  fork_ref: string;
  merge_base_commit: string;
  phases: PhaseFileBatch[];
  risk_summary: RiskSummary;
  category_summary: CategorySummary | null;
  layers: MergeLayer[];
  project_context_summary: string;
  special_instructions: string[];
}

export interface FileDiff {
  file_path: string;
  risk_level: RiskLevel;
  risk_score: number;
  lines_added: number;
  lines_deleted: number;
  language: string | null;
  is_security_sensitive: boolean;
  change_category: FileChangeCategory | null;
  raw_diff: string;
}

export interface ConflictPoint {
  description: string;
  severity: string;
  line_range: string;
}

export interface DecisionOption {
  option_key: string;
  decision: MergeDecision;
  description: string;
  risk_warning?: string;
}

export interface HumanDecisionRequest {
  file_path: string;
  priority: number;
  conflict_points: ConflictPoint[];
  context_summary: string;
  upstream_change_summary: string;
  fork_change_summary: string;
  analyst_recommendation: MergeDecision;
  analyst_confidence: number;
  analyst_rationale: string;
  options: DecisionOption[];
  human_decision: MergeDecision | null;
}

export interface JudgeIssue {
  file_path: string;
  issue_type: string;
  severity: string;
  description: string;
}

export interface JudgeVerdict {
  verdict: VerdictType;
  summary: string;
  issues: JudgeIssue[];
  veto_triggered: boolean;
  veto_reason: string | null;
  repair_instructions: { instruction: string; is_repairable: boolean }[];
}

export interface FileDecisionRecord {
  file_path: string;
  decision: MergeDecision;
  strategy_used: string;
  success: boolean;
  error: string | null;
}

export interface GateResult {
  gate_name: string;
  passed: boolean;
  output: string;
}

export interface GateEntry {
  phase: string;
  timestamp: string;
  all_passed: boolean;
  results: GateResult[];
}

export interface ErrorEntry {
  timestamp: string;
  phase: string;
  message: string;
}

export interface MessageEntry {
  timestamp: string;
  type: string;
  from?: string;
  to?: string;
  reason?: string;
}

export interface MergeMemory {
  phase_summaries: Record<string, string>;
  entries: { key: string; value: string; phase: string }[];
}

export interface PlanReviewRound {
  round_number: number;
  verdict_result: string;
  verdict_summary: string;
  issues_count: number;
}

export type ScreenId =
  | "dashboard"
  | "plan_review"
  | "decisions"
  | "file_detail"
  | "judge"
  | "report";
