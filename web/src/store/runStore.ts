import { create } from "zustand";
import type {
  AgentActivityEvent,
  MergeStateSnapshot,
} from "../types/state";
import type { ConnState } from "../ws/client";

const ACTIVITY_MAX = 200;

interface RunStoreState {
  conn: ConnState;
  snapshot: MergeStateSnapshot | null;
  activity: AgentActivityEvent[];
  lastCancelError: { reason: string; current_status: string } | null;
  setConn: (s: ConnState) => void;
  applySnapshot: (s: MergeStateSnapshot) => void;
  appendActivity: (e: AgentActivityEvent) => void;
  replaceActivity: (events: AgentActivityEvent[]) => void;
  setCancelError: (e: { reason: string; current_status: string }) => void;
  clearCancelError: () => void;
}

export const useRunStore = create<RunStoreState>((set) => ({
  conn: "connecting",
  snapshot: null,
  activity: [],
  lastCancelError: null,
  setConn: (s) => set({ conn: s }),
  applySnapshot: (s) => set({ snapshot: s }),
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
}));
