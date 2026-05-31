import type {
  AgentActivityEvent,
  MergeStateSnapshot,
  ProviderName,
  SetupContext,
  SetupError,
  SetupPayload,
  SetupReady,
  SetupTestResult,
} from "../types/state";

export type InboundMessage =
  | { type: "state_snapshot"; payload: MergeStateSnapshot }
  | { type: "state_patch"; payload: MergeStateSnapshot }
  | { type: "agent_activity"; payload: AgentActivityEvent }
  | { type: "agent_activity_replay"; payload: { events: AgentActivityEvent[] } }
  | {
      type: "cancel_error";
      payload: { reason: string; current_status: string };
    }
  | { type: "setup_snapshot"; payload: SetupContext }
  | { type: "setup_ready"; payload: SetupReady }
  | { type: "setup_error"; payload: SetupError }
  | { type: "setup_test_result"; payload: SetupTestResult }
  | {
      type: "command_error";
      payload: { reason: string; command: string };
    };

export type OutboundMessage =
  | {
      type: "submit_decision";
      payload: {
        filePath: string;
        decision: string;
        reviewer_notes?: string | null;
        custom_content?: string | null;
      };
    }
  | {
      type: "submit_conflict_decisions_batch";
      payload: {
        items: Array<{
          file_path: string;
          decision: string;
          reviewer_notes?: string | null;
          custom_content?: string | null;
        }>;
      };
    }
  | {
      type: "submit_plan_review";
      payload: { decision: "approve" | "reject" | "modify"; notes?: string };
    }
  | {
      type: "submit_user_plan_decisions";
      payload: {
        items: Array<{
          item_id: string;
          user_choice: string;
          user_input?: string;
        }>;
      };
    }
  | {
      type: "submit_judge_resolution";
      payload: { resolution: "accept" | "abort" | "rerun" };
    }
  | { type: "cancel_run"; payload: Record<string, never> }
  | { type: "pause"; payload: Record<string, never> }
  | { type: "resume"; payload: Record<string, never> }
  | { type: "setup.detect"; payload: Record<string, never> }
  | { type: "setup.submit"; payload: SetupPayload }
  | {
      type: "setup.test_connection";
      payload: {
        provider: ProviderName;
        api_key: string;
        base_url: string | null;
        models: string[];
      };
    };
