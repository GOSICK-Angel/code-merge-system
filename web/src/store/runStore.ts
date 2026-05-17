import { create } from "zustand";
import type {
  AgentActivityEvent,
  MergeStateSnapshot,
  SetupContext,
  SetupError,
  SetupReady,
} from "../types/state";
import type { StoreMode } from "../lib/classifyView";
import type { ConnState } from "../ws/client";

const ACTIVITY_MAX = 200;

export type SetupStatus = "idle" | "submitting" | "ready" | "error";

interface RunStoreState {
  conn: ConnState;
  mode: StoreMode;
  snapshot: MergeStateSnapshot | null;
  activity: AgentActivityEvent[];
  lastCancelError: { reason: string; current_status: string } | null;
  setupContext: SetupContext | null;
  setupStatus: SetupStatus;
  setupReady: SetupReady | null;
  setupError: SetupError | null;
  setConn: (s: ConnState) => void;
  applySnapshot: (s: MergeStateSnapshot) => void;
  appendActivity: (e: AgentActivityEvent) => void;
  replaceActivity: (events: AgentActivityEvent[]) => void;
  setCancelError: (e: { reason: string; current_status: string }) => void;
  clearCancelError: () => void;
  applySetupSnapshot: (ctx: SetupContext) => void;
  markSetupSubmitting: () => void;
  applySetupReady: (ready: SetupReady) => void;
  applySetupError: (err: SetupError) => void;
}

export const useRunStore = create<RunStoreState>((set) => ({
  conn: "connecting",
  // Default to "run" so a session that connects straight into an
  // in-flight merge keeps rendering the dashboard without needing an
  // initial setup_snapshot. The bridge switches us into "setup" the
  // moment the first setup_snapshot lands.
  mode: "run",
  snapshot: null,
  activity: [],
  lastCancelError: null,
  setupContext: null,
  setupStatus: "idle",
  setupReady: null,
  setupError: null,
  setConn: (s) => set({ conn: s }),
  // Receiving a state snapshot always means we're in run mode — even if
  // we were mid-setup, the orchestrator has now taken over and the form
  // is no longer authoritative.
  applySnapshot: (s) => set({ snapshot: s, mode: "run" }),
  appendActivity: (e) =>
    set((state) => {
      const next = [...state.activity, e];
      return {
        activity:
          next.length > ACTIVITY_MAX ? next.slice(-ACTIVITY_MAX) : next,
      };
    }),
  replaceActivity: (events) =>
    set({ activity: events.slice(-ACTIVITY_MAX) }),
  setCancelError: (e) => set({ lastCancelError: e }),
  clearCancelError: () => set({ lastCancelError: null }),
  applySetupSnapshot: (ctx) =>
    set({
      mode: "setup",
      setupContext: ctx,
      setupStatus: "idle",
      setupError: null,
    }),
  markSetupSubmitting: () =>
    set({ setupStatus: "submitting", setupError: null }),
  applySetupReady: (ready) =>
    set({ setupStatus: "ready", setupReady: ready, setupError: null }),
  applySetupError: (err) =>
    set({ setupStatus: "error", setupError: err }),
}));
