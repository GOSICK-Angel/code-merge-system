import { create } from "zustand";
import type { MergeDecisionValue } from "../types/state";

export interface ConflictDraft {
  decision: MergeDecisionValue;
  reviewer_notes: string;
  custom_content: string;
}

interface ConflictDraftStoreState {
  drafts: Record<string, ConflictDraft>;
  selectedFile: string | null;
  setDraftDecision: (filePath: string, decision: MergeDecisionValue) => void;
  setDraftNotes: (filePath: string, notes: string) => void;
  setDraftCustomContent: (filePath: string, content: string) => void;
  clearDraft: (filePath: string) => void;
  selectFile: (filePath: string | null) => void;
  applyRecommendedToAll: (
    pairs: Array<{ file_path: string; recommendation: MergeDecisionValue | null }>,
  ) => number;
}

function emptyDraft(decision: MergeDecisionValue): ConflictDraft {
  return { decision, reviewer_notes: "", custom_content: "" };
}

/**
 * Local-only draft buffer for L3 decisions.
 *
 * Why a separate store from runStore: the WebSocket bridge mirrors
 * authoritative state (server is source of truth for ``human_decision``);
 * the draft is *pre-submit* UI state that survives view-switches but
 * never round-trips to the backend until the user explicitly hits Submit.
 *
 * MANUAL_PATCH is the only choice that requires ``custom_content`` — the
 * view layer enforces this; the store accepts the choice regardless so
 * the user can switch back and forth without losing data.
 */
export const useConflictDraftStore = create<ConflictDraftStoreState>(
  (set) => ({
    drafts: {},
    selectedFile: null,
    setDraftDecision: (filePath, decision) =>
      set((state) => {
        const existing = state.drafts[filePath] ?? emptyDraft(decision);
        return {
          drafts: {
            ...state.drafts,
            [filePath]: { ...existing, decision },
          },
        };
      }),
    setDraftNotes: (filePath, notes) =>
      set((state) => {
        const existing = state.drafts[filePath];
        if (!existing) return state;
        return {
          drafts: {
            ...state.drafts,
            [filePath]: { ...existing, reviewer_notes: notes },
          },
        };
      }),
    setDraftCustomContent: (filePath, content) =>
      set((state) => {
        const existing = state.drafts[filePath];
        if (!existing) return state;
        return {
          drafts: {
            ...state.drafts,
            [filePath]: { ...existing, custom_content: content },
          },
        };
      }),
    clearDraft: (filePath) =>
      set((state) => {
        const { [filePath]: _omit, ...rest } = state.drafts;
        return { drafts: rest };
      }),
    selectFile: (filePath) => set({ selectedFile: filePath }),
    applyRecommendedToAll: (pairs) => {
      let applied = 0;
      set((state) => {
        const next = { ...state.drafts };
        for (const { file_path, recommendation } of pairs) {
          if (!recommendation) continue;
          const existing = next[file_path] ?? emptyDraft(recommendation);
          next[file_path] = { ...existing, decision: recommendation };
          applied += 1;
        }
        return { drafts: next };
      });
      return applied;
    },
  }),
);

/**
 * Validate that a draft is submittable: MANUAL_PATCH requires
 * ``custom_content`` (non-empty after trim). Returns an error message
 * string when invalid, ``null`` when OK.
 */
export function validateDraft(draft: ConflictDraft): string | null {
  if (draft.decision === "manual_patch" && draft.custom_content.trim() === "") {
    return "MANUAL_PATCH requires a custom patch — paste it into the editor below.";
  }
  return null;
}
