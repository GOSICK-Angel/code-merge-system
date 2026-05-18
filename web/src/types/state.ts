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

export type JudgeIssueSeverity =
  | "critical"
  | "high"
  | "medium"
  | "low"
  | "info"
  | "unknown";

export interface JudgeIssuePayload {
  issue_id: string | null;
  file_path: string;
  issue_type: string;
  severity: JudgeIssueSeverity | string;
  description: string;
  suggested_fix: string | null;
  must_fix_before_merge: boolean;
  resolvability: string | null;
  affected_lines: number[];
}

export interface JudgeRepairInstructionPayload {
  file_path: string;
  instruction: string;
  is_repairable: boolean;
  severity: string | null;
  source_issue_id: string | null;
}

export interface JudgeVerdict {
  verdict: string;
  summary: string;
  failed_files: string[];
  passed_files: string[];
  conditional_files: string[];
  reviewed_files_count: number;
  critical_issues_count: number;
  high_issues_count: number;
  overall_confidence: number;
  blocking_issues: string[];
  issues: JudgeIssuePayload[];
  veto_triggered: boolean;
  veto_reason: string | null;
  repair_instructions: JudgeRepairInstructionPayload[];
}

export type JudgeResolution = "accept" | "abort" | "rerun";

export interface PlanHumanReviewPayload {
  decision: string;
  reviewer_name: string | null;
  reviewer_notes: string | null;
  decided_at: string | null;
  item_decisions_count: number;
}

// Mirror of src/models/state.py ReviewConclusion. Populated when the
// plan_review phase finishes — either because planner/judge converged
// (``reason="converged"``) or because the loop hit a terminal condition
// (``max_rounds`` / ``stalled`` / ``llm_failure``). For non-converged
// reasons, AWAITING_HUMAN is required even if no per-file decisions
// exist; the UI uses this payload to surface that to the reviewer.
export interface ReviewConclusionPayload {
  reason: string;
  final_round: number;
  total_rounds: number;
  max_rounds: number;
  summary: string;
  pending_decisions_count: number;
  rejection_details: unknown[];
}

export interface PendingUserDecisionOption {
  key: string;
  label: string;
  description: string;
}

export interface PendingUserDecision {
  item_id: string;
  file_path: string;
  description: string;
  risk_context?: string;
  conflict_preview?: string;
  current_classification?: string;
  options: PendingUserDecisionOption[];
  user_choice: string | null;
  user_input: string | null;
}

// L2 plan/layer/log payload — mirror of src/web/serializers.py
// serialize_plan / serialize_review_round output. Optional fields
// align with snapshots taken before plan_review completes.
export interface PlanLayer {
  layer_id: number;
  name: string;
  description: string;
  depends_on: number[];
}

export interface PlanPhaseBatch {
  batch_id: string;
  phase: string;
  file_paths: string[];
  risk_level: string;
  layer_id: number;
  change_category: string | null;
}

// Mirror of src/models/plan.py RiskSummary / CategorySummary. Both are
// emitted verbatim by serialize_plan() (model_dump mode="json"), so the
// shape on the wire is stable. Keeping these typed (rather than a loose
// Record<string, number>) lets the planner-summary cards consume
// estimated_auto_merge_rate / top_risk_files without unsafe casts.
export interface RiskSummaryPayload {
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

export interface CategorySummaryPayload {
  total_files: number;
  a_unchanged: number;
  b_upstream_only: number;
  c_both_changed: number;
  d_missing: number;
  d_extra: number;
  e_current_only: number;
}

export interface MergePlanPayload {
  plan_id: string;
  created_at: string | null;
  upstream_ref: string;
  fork_ref: string;
  merge_base_commit: string;
  phases: PlanPhaseBatch[];
  risk_summary: RiskSummaryPayload;
  category_summary: CategorySummaryPayload | null;
  layers: PlanLayer[];
  project_context_summary: string;
  special_instructions: string[];
}

export interface PlanReviewIssue {
  file_path: string;
  reason: string;
  current: string;
  suggested: string;
}

export interface PlanReviewRoundPayload {
  round_number: number;
  verdict_result: string;
  verdict_summary: string;
  issues_count: number;
  // Loose Record fallback for forward compatibility — judge may add new
  // fields, and older snapshots may omit current/suggested.
  issues_detail: Array<Partial<PlanReviewIssue> & Record<string, unknown>>;
  planner_revision_summary: string | null;
  planner_responses: Array<{
    issue_id: string;
    file_path: string;
    action: string;
    reason: string;
    counter_proposal: string | null;
  }>;
  plan_diff: Array<{
    file_path: string;
    old_risk: string;
    new_risk: string;
  }>;
  negotiation_messages: Array<{
    sender: string;
    round_number: number;
    content: string;
    timestamp: string;
  }>;
  timestamp: string;
}

export interface CostSummary {
  total_cost_usd?: number;
  total_tokens?: number;
  by_agent?: Record<string, { cost_usd?: number; tokens?: number }>;
  // U2 per-run budget knobs surfaced by serializers._serialize_cost_summary.
  // ``limit_usd === null`` means the cap is disabled (no progress bar).
  limit_usd?: number | null;
  warn_pct?: number;
  [k: string]: unknown;
}

export interface MergeStateSnapshot {
  runId: string;
  status: SystemStatus;
  currentPhase: string;
  phaseResults: Record<string, PhaseResult>;
  mergePlan: MergePlanPayload | null;
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
  planReviewLog: PlanReviewRoundPayload[];
  reviewConclusion: ReviewConclusionPayload | null;
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
  // Phase 4 additive fields
  judgeResolution?: JudgeResolution | null;
  rerunRound?: number;
  maxRerunRounds?: number;
  planHumanReview?: PlanHumanReviewPayload | null;
}

export interface AgentActivityEvent {
  agent: string;
  action: string;
  phase: string;
  event_type: "start" | "progress" | "complete" | "error";
  elapsed: number | null;
}

// ---- Setup wizard ---------------------------------------------------------
// Mirrors src/models/setup.py. The setup view is shown when the backend
// reports `setup_snapshot` (i.e. no .merge/config.yaml exists yet, or the
// run was launched with reconfigure intent). Submission flows through the
// same WebSocket as run-mode commands; once the server emits `setup_ready`
// + a `state_snapshot`, the store flips `mode` to "run" and rendering
// auto-routes to the dashboard.

export type ProviderName = "anthropic" | "openai";

export type ApiKeyHintSource = "shell" | "project_env" | "global_env" | "";

export interface ApiKeyHint {
  name: string;
  masked: string;
  source: ApiKeyHintSource;
}

export interface ProviderConfig {
  enabled: boolean;
  api_key: string;
  base_url: string | null;
  // Authoritative list of models this provider exposes to AGENT
  // OVERRIDES. ``models[0]`` is the implicit default for agents
  // without an explicit override.
  models: string[];
}

export interface AgentChoice {
  provider: ProviderName;
  model: string;
}

export interface AgentInventoryEntry {
  name: string;
  blurb: string;
}

export interface SetupContext {
  current_branch: string;
  suggested_target: string;
  fork_divergence_count: number;
  has_existing_config: boolean;
  existing_config_summary: Record<string, unknown> | null;
  forks_profile_threshold: number;
  // True when ~/.config/code-merge-system/.env exists. Used by the
  // Setup view: when both this flag and ``has_existing_config`` are
  // false the provider sections render with empty key/base_url/models
  // so a fresh device + fresh project doesn't show preset values.
  has_global_env: boolean;

  // Per-provider key/base-url hints + the recommended-model dropdown
  // source. ``agent_inventory`` is the ordered list of agent roles to
  // render in the AGENT OVERRIDES table.
  anthropic_key_hint: ApiKeyHint;
  openai_key_hint: ApiKeyHint;
  github_token_hint: ApiKeyHint;
  anthropic_base_url: string | null;
  openai_base_url: string | null;
  provider_recommended_models: Record<ProviderName, string[]>;
  agent_inventory: AgentInventoryEntry[];
  // (provider, agent_name) → recommended model name. UI uses it to
  // pre-fill the AGENT OVERRIDES table; falls back to provider.models[0]
  // when the recommended one isn't in the configured models list.
  recommended_agent_models: Partial<Record<ProviderName, Record<string, string>>>;
}

export interface ThresholdsPayload {
  auto_merge_confidence?: number | null;
  risk_score_low?: number | null;
  risk_score_high?: number | null;
}

export interface SetupPayload {
  target_branch: string;
  fork_ref: string;
  project_context: string;
  anthropic: ProviderConfig;
  openai: ProviderConfig;
  github_token: string;
  default_provider: ProviderName | null;
  agent_choices: Record<string, AgentChoice>;
  thresholds: ThresholdsPayload | null;
  dry_run: boolean;
  workflow: string | null;
  init_forks_profile: boolean;
}

export interface SetupReady {
  config_path: string;
  dry_run: boolean;
  workflow: string | null;
  init_forks_profile: boolean;
}

export interface SetupError {
  reason: string;
  details?: string;
}
