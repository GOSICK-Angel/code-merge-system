import { create } from "zustand";
import type {
  SystemStatus,
  MergePhase,
  PhaseResult,
  MergePlan,
  RiskLevel,
  FileDiff,
  HumanDecisionRequest,
  MergeDecision,
  JudgeVerdict,
  GateEntry,
  ErrorEntry,
  MessageEntry,
  MergeMemory,
  FileDecisionRecord,
  PlanReviewRound,
  ScreenId,
} from "./types.js";

interface MergeStateSlice {
  runId: string;
  status: SystemStatus;
  currentPhase: MergePhase;
  phaseResults: Record<string, PhaseResult>;
  mergePlan: MergePlan | null;
  fileClassifications: Record<string, RiskLevel>;
  fileDiffs: FileDiff[];
  fileDecisionRecords: Record<string, FileDecisionRecord>;
  humanDecisionRequests: Record<string, HumanDecisionRequest>;
  humanDecisions: Record<string, MergeDecision>;
  judgeVerdict: JudgeVerdict | null;
  judgeRepairRounds: number;
  planReviewLog: PlanReviewRound[];
  gateHistory: GateEntry[];
  errors: ErrorEntry[];
  messages: MessageEntry[];
  memory: MergeMemory;
  createdAt: string;
  agentActivity: { agent: string; action: string } | null;
}

interface UISlice {
  activeScreen: ScreenId;
  selectedFileIndex: number;
  selectedFile: string | null;
  searchQuery: string;
  connectionStatus: "connecting" | "connected" | "disconnected";
}

interface Actions {
  applySnapshot: (data: Partial<MergeStateSlice>) => void;
  applyPatch: (patch: Partial<MergeStateSlice>) => void;
  setAgentActivity: (activity: { agent: string; action: string } | null) => void;
  setActiveScreen: (screen: ScreenId) => void;
  setSelectedFileIndex: (index: number) => void;
  setSelectedFile: (file: string | null) => void;
  setSearchQuery: (query: string) => void;
  setConnectionStatus: (status: UISlice["connectionStatus"]) => void;
}

export type AppStore = MergeStateSlice & UISlice & Actions;

export const useAppStore = create<AppStore>((set) => ({
  // MergeState defaults
  runId: "",
  status: "initialized",
  currentPhase: "analysis",
  phaseResults: {},
  mergePlan: null,
  fileClassifications: {},
  fileDiffs: [],
  fileDecisionRecords: {},
  humanDecisionRequests: {},
  humanDecisions: {},
  judgeVerdict: null,
  judgeRepairRounds: 0,
  planReviewLog: [],
  gateHistory: [],
  errors: [],
  messages: [],
  memory: { phase_summaries: {}, entries: [] },
  createdAt: new Date().toISOString(),
  agentActivity: null,

  // UI defaults
  activeScreen: "dashboard",
  selectedFileIndex: 0,
  selectedFile: null,
  searchQuery: "",
  connectionStatus: "connecting",

  // Actions
  applySnapshot: (data) => set((state) => ({ ...state, ...data })),
  applyPatch: (patch) => set((state) => ({ ...state, ...patch })),
  setAgentActivity: (activity) => set({ agentActivity: activity }),
  setActiveScreen: (screen) => set({ activeScreen: screen }),
  setSelectedFileIndex: (index) => set({ selectedFileIndex: index }),
  setSelectedFile: (file) => set({ selectedFile: file }),
  setSearchQuery: (query) => set({ searchQuery: query }),
  setConnectionStatus: (status) => set({ connectionStatus: status }),
}));
