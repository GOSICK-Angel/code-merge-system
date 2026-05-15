// Front-end mirror of src/web/serializers.py:serialize_state output.
// Keep field names in sync with that module. Additive fields (costSummary /
// phaseElapsed / decisionRecordCounts / future Phase 3 fields) are Optional
// because older snapshots may not include them.

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

export const SYSTEM_STATUS_ORDER: SystemStatus[] = [
  "initialized",
  "planning",
  "plan_reviewing",
  "plan_revising",
  "auto_merging",
  "plan_dispute_pending",
  "analyzing_conflicts",
  "awaiting_human",
  "judge_reviewing",
  "generating_report",
  "completed",
];

export interface PhaseResult {
  phase: string;
  status: "pending" | "running" | "completed" | "failed" | "skipped";
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
}

// Mirror of src.models.decision.MergeDecision — ESCALATE_HUMAN is hidden
// from L3 selectable options per plan v1.1 §4 (analyst already escalated by
// landing the request here; users pick among the 5 actionable outcomes).
export type MergeDecisionValue =
  | "take_current"
  | "take_target"
  | "semantic_merge"
  | "manual_patch"
  | "escalate_human"
  | "skip";

export const SELECTABLE_DECISIONS: MergeDecisionValue[] = [
  "take_current",
  "take_target",
  "semantic_merge",
  "manual_patch",
  "skip",
];

export interface ChangeIntent {
  description: string;
  intent_type: string;
  confidence: number;
}

export interface ConflictPoint {
  conflict_id: string | null;
  hunk_id: string | null;
  conflict_type: string;
  description: string;
  severity: "high" | "medium" | "low";
  line_range: string;
  upstream_intent: ChangeIntent | null;
  fork_intent: ChangeIntent | null;
  can_coexist: boolean | null;
  suggested_decision: string | null;
  confidence: number;
  rationale: string;
  risk_factors: string[];
}

export interface DecisionOption {
  option_key: string;
  decision: MergeDecisionValue;
  description: string;
  preview_content: string | null;
  risk_warning: string | null;
}

export interface HumanDecisionRequest {
  request_id: string | null;
  file_path: string;
  priority: number;
  conflict_points: ConflictPoint[];
  context_summary: string;
  upstream_change_summary: string;
  fork_change_summary: string;
  analyst_recommendation: MergeDecisionValue | null;
  analyst_confidence: number | null;
  analyst_rationale: string;
  options: DecisionOption[];
  human_decision: MergeDecisionValue | null;
  custom_content: string | null;
  reviewer_notes: string | null;
  related_files: string[];
}

export interface JudgeVerdict {
  verdict: string;
  summary: string;
  veto_triggered: boolean;
  veto_reason: string | null;
  issues: Array<{
    file_path: string;
    issue_type: string;
    severity: string;
    description: string;
  }>;
  repair_instructions: Array<{ instruction: string; is_repairable: boolean }>;
}

export interface PendingUserDecision {
  item_id: string;
  file_path: string;
  description: string;
  options: Array<{ key: string; label: string; description: string }>;
  user_choice: string | null;
  user_input: string | null;
}

export interface CostSummary {
  total_cost_usd?: number;
  total_tokens?: number;
  by_agent?: Record<string, { cost_usd?: number; tokens?: number }>;
  [k: string]: unknown;
}

export interface MergeStateSnapshot {
  runId: string;
  status: SystemStatus;
  currentPhase: string;
  phaseResults: Record<string, PhaseResult>;
  mergePlan: unknown | null;
  fileClassifications: Record<string, string>;
  fileDiffs: Array<{
    file_path: string;
    risk_level: string;
    risk_score: number;
    lines_added: number;
    lines_deleted: number;
  }>;
  fileDecisionRecords: Record<
    string,
    {
      file_path: string;
      decision: string;
      strategy_used: string;
      success: boolean;
      error: string | null;
    }
  >;
  humanDecisionRequests: Record<string, HumanDecisionRequest>;
  humanDecisions: Record<string, string>;
  judgeVerdict: JudgeVerdict | null;
  judgeRepairRounds: number;
  planReviewLog: unknown[];
  reviewConclusion: unknown | null;
  pendingUserDecisions: PendingUserDecision[];
  gateHistory: unknown[];
  errors: Array<{ message?: string }>;
  messages: unknown[];
  memory: unknown;
  createdAt: string;
  // Phase 1 additive fields
  costSummary?: CostSummary | null;
  phaseElapsed?: Record<string, number | null>;
  decisionRecordCounts?: Record<string, number>;
}

export interface AgentActivityEvent {
  agent: string;
  action: string;
  phase: string;
  event_type: "start" | "progress" | "complete" | "error";
  elapsed: number | null;
}
