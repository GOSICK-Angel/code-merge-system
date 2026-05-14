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

export interface HumanDecisionRequest {
  file_path: string;
  priority: number;
  human_decision: string | null;
  analyst_recommendation: string | null;
  analyst_confidence: number | null;
  options: Array<{
    option_key: string;
    decision: string;
    description: string;
    risk_warning: string | null;
  }>;
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
