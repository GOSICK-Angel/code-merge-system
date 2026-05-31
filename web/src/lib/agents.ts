import type { AgentActivityEvent, SystemStatus } from "../types/state";

// A run only does live agent work in these states; anything else (waiting on
// a human, finished, paused) means nothing is executing right now.
const INACTIVE_STATUSES: ReadonlySet<string> = new Set<SystemStatus>([
  "awaiting_human",
  "completed",
  "failed",
  "paused",
]);

// Real busy signal: the agent of the most recent activity event, but only
// while that event marks the start/continuation of work (not its end) and the
// run itself is actively executing. Replaces the old heuristic that lit up any
// agent whose name contained the first token of currentPhase — which kept
// glowing even after the run suspended to AWAITING_HUMAN.
export function busyAgentIds(
  status: SystemStatus | undefined,
  activity: readonly AgentActivityEvent[],
): Set<string> {
  if (!status || INACTIVE_STATUSES.has(status)) return new Set();
  for (let i = activity.length - 1; i >= 0; i--) {
    const e = activity[i];
    if (e.event_type === "start" || e.event_type === "progress") {
      return new Set([e.agent]);
    }
    if (e.event_type === "complete" || e.event_type === "error") {
      return new Set();
    }
  }
  return new Set();
}

export interface AgentRunState {
  running: boolean;
  action: string;
  // Epoch seconds when the current run started (for a live elapsed timer),
  // or null when idle/unknown.
  startedAt: number | null;
  // Duration (s) of the last completed call, or null.
  lastElapsed: number | null;
  calls: number;
  lastEventType: AgentActivityEvent["event_type"];
}

export interface CommEdge {
  from: string;
  to: string;
  label: string;
  at: number;
}

export interface AgentRuntime {
  states: Record<string, AgentRunState>;
  comms: CommEdge[];
}

// Newest few communication edges to retain for rendering.
const COMM_KEEP = 6;

// Fold the activity stream into per-agent run state + recent communication
// edges. Run-state events (target == null) update the emitting agent;
// communication events (target set) become directed edges. "Latest event per
// agent wins" keeps this robust to retry/fallback nesting. When the run is
// not actively executing, every agent is forced idle and no edge is live.
// Edge fade-out by age is handled by the renderer's own clock.
export function deriveAgentRuntime(
  status: SystemStatus | undefined,
  activity: readonly AgentActivityEvent[],
): AgentRuntime {
  const active = !!status && !INACTIVE_STATUSES.has(status);
  const states: Record<string, AgentRunState> = {};
  const comms: CommEdge[] = [];

  const ensure = (id: string): AgentRunState => {
    if (!states[id]) {
      states[id] = {
        running: false,
        action: "",
        startedAt: null,
        lastElapsed: null,
        calls: 0,
        lastEventType: "complete",
      };
    }
    return states[id];
  };

  for (const e of activity) {
    if (e.target != null && e.target !== "") {
      ensure(e.agent);
      ensure(e.target);
      if (active) {
        comms.push({
          from: e.agent,
          to: e.target,
          label: e.action,
          at: typeof e.ts === "number" ? e.ts : 0,
        });
      }
      continue;
    }

    const st = ensure(e.agent);
    st.lastEventType = e.event_type;
    if (e.event_type === "start") {
      st.running = active;
      st.action = e.action;
      st.startedAt = typeof e.ts === "number" ? e.ts : null;
      st.calls += 1;
    } else if (e.event_type === "progress") {
      if (e.action) st.action = e.action;
    } else {
      // complete | error → idle
      st.running = false;
      st.startedAt = null;
      if (typeof e.elapsed === "number") st.lastElapsed = e.elapsed;
    }
  }

  if (!active) {
    for (const id of Object.keys(states)) {
      states[id].running = false;
      states[id].startedAt = null;
    }
  }

  return { states, comms: comms.slice(-COMM_KEEP) };
}
