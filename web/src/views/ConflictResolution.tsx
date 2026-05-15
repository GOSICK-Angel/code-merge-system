import { useEffect, useMemo } from "react";
import type { WsClient } from "../ws/client";
import { useRunStore } from "../store/runStore";
import {
  useConflictDraftStore,
  validateDraft,
} from "../store/conflictDraftStore";
import type {
  HumanDecisionRequest,
  MergeDecisionValue,
} from "../types/state";
import { StatusBanner } from "../components/StatusBanner";
import { FileTree } from "../components/FileTree";
import { DiffViewer } from "../components/DiffViewer";
import { DecisionPanel } from "../components/DecisionPanel";
import { ConflictPointMarker } from "../components/ConflictPointMarker";
import { BatchActionBar } from "../components/BatchActionBar";

interface Props {
  clientRef: React.MutableRefObject<WsClient | null>;
}

function recommendedSubmittable(
  r: HumanDecisionRequest,
): MergeDecisionValue | null {
  if (!r.analyst_recommendation) return null;
  if (r.analyst_recommendation === "escalate_human") return null;
  return r.analyst_recommendation;
}

export function ConflictResolution({ clientRef }: Props): JSX.Element {
  const snapshot = useRunStore((s) => s.snapshot);
  const conn = useRunStore((s) => s.conn);

  const drafts = useConflictDraftStore((s) => s.drafts);
  const selectedFile = useConflictDraftStore((s) => s.selectedFile);
  const selectFile = useConflictDraftStore((s) => s.selectFile);
  const setDraftDecision = useConflictDraftStore((s) => s.setDraftDecision);
  const setDraftNotes = useConflictDraftStore((s) => s.setDraftNotes);
  const setDraftCustomContent = useConflictDraftStore(
    (s) => s.setDraftCustomContent,
  );
  const clearDraft = useConflictDraftStore((s) => s.clearDraft);
  const applyRecommendedToAll = useConflictDraftStore(
    (s) => s.applyRecommendedToAll,
  );

  const requests = useMemo<HumanDecisionRequest[]>(
    () => Object.values(snapshot?.humanDecisionRequests ?? {}),
    [snapshot],
  );
  const pending = useMemo(
    () => requests.filter((r) => r.human_decision === null),
    [requests],
  );
  const recommendedCount = useMemo(
    () => pending.filter((r) => recommendedSubmittable(r) !== null).length,
    [pending],
  );

  // Auto-select the first pending file on mount / after submit.
  useEffect(() => {
    if (selectedFile && requests.some((r) => r.file_path === selectedFile)) {
      return;
    }
    if (pending.length > 0) selectFile(pending[0].file_path);
    else if (requests.length > 0) selectFile(requests[0].file_path);
  }, [pending, requests, selectedFile, selectFile]);

  const current = useMemo(
    () => requests.find((r) => r.file_path === selectedFile) ?? null,
    [requests, selectedFile],
  );
  const currentDraft = current ? drafts[current.file_path] : undefined;

  const sendSingle = (filePath: string, decision: MergeDecisionValue) => {
    clientRef.current?.send({
      type: "submit_decision",
      payload: { filePath, decision },
    });
  };

  const draftedEntries = useMemo(
    () =>
      pending
        .map((r) => ({ request: r, draft: drafts[r.file_path] }))
        .filter((p) => p.draft !== undefined),
    [pending, drafts],
  );

  const submitDisabledReason: string | null = (() => {
    if (draftedEntries.length === 0) return "No drafts to submit";
    for (const { request, draft } of draftedEntries) {
      if (!draft) continue;
      const err = validateDraft(draft);
      if (err) return `${request.file_path}: ${err}`;
    }
    return null;
  })();

  const submitAllDrafts = () => {
    if (submitDisabledReason !== null) return;
    const items = draftedEntries.map(({ request, draft }) => ({
      file_path: request.file_path,
      decision: draft!.decision,
    }));
    if (items.length === 0) return;
    clientRef.current?.send({
      type: "submit_conflict_decisions_batch",
      payload: { items },
    });
  };

  const applyRecommendedClicked = () => {
    applyRecommendedToAll(
      pending.map((r) => ({
        file_path: r.file_path,
        recommendation: recommendedSubmittable(r),
      })),
    );
  };

  const submitCurrent = () => {
    if (!current || !currentDraft) return;
    if (validateDraft(currentDraft) !== null) return;
    sendSingle(current.file_path, currentDraft.decision);
  };

  return (
    <div className="min-h-screen flex flex-col bg-slate-950 text-slate-100">
      <StatusBanner
        runId={snapshot?.runId ?? null}
        status={snapshot?.status ?? null}
        conn={conn}
      />
      <BatchActionBar
        pendingCount={pending.length}
        draftCount={Object.keys(drafts).length}
        recommendedCount={recommendedCount}
        onApplyRecommendedToAll={applyRecommendedClicked}
        onSubmitAllDrafts={submitAllDrafts}
        submitDisabledReason={
          draftedEntries.length === 0 ? null : submitDisabledReason
        }
      />
      <div className="flex flex-1 min-h-0">
        <FileTree
          requests={requests}
          drafts={drafts}
          selectedFile={selectedFile}
          onSelect={selectFile}
        />
        {current ? (
          <div className="flex-1 flex flex-col min-w-0">
            <header className="px-4 py-3 border-b border-slate-800 flex items-baseline gap-3">
              <h2 className="text-sm font-semibold text-slate-100 truncate">
                {current.file_path}
              </h2>
              <span className="text-xs text-slate-500">
                priority {current.priority}
              </span>
            </header>
            <div className="flex-1 overflow-y-auto px-4 py-3 space-y-4">
              <section className="space-y-2">
                <div className="text-xs text-slate-500 uppercase tracking-wider">
                  Conflict context
                </div>
                <p className="text-xs text-slate-300 leading-relaxed">
                  {current.context_summary || (
                    <span className="italic text-slate-500">
                      (no summary)
                    </span>
                  )}
                </p>
              </section>
              {current.conflict_points.length > 0 && (
                <section className="space-y-2">
                  <div className="text-xs text-slate-500 uppercase tracking-wider">
                    Conflict points ({current.conflict_points.length})
                  </div>
                  <div className="space-y-1.5">
                    {current.conflict_points.map((cp, idx) => (
                      <ConflictPointMarker
                        key={cp.conflict_id ?? `${current.file_path}-${idx}`}
                        cp={cp}
                        idx={idx}
                      />
                    ))}
                  </div>
                </section>
              )}
              <section className="space-y-2">
                <div className="text-xs text-slate-500 uppercase tracking-wider">
                  Upstream vs fork
                </div>
                <DiffViewer
                  oldLabel="upstream"
                  newLabel="fork"
                  oldText={current.upstream_change_summary}
                  newText={current.fork_change_summary}
                />
              </section>
            </div>
            <DecisionPanel
              request={current}
              draft={currentDraft}
              onPickDecision={(d) => setDraftDecision(current.file_path, d)}
              onNotesChange={(n) => setDraftNotes(current.file_path, n)}
              onCustomContentChange={(c) =>
                setDraftCustomContent(current.file_path, c)
              }
              onSubmit={submitCurrent}
              onClear={() => clearDraft(current.file_path)}
            />
          </div>
        ) : (
          <div className="flex-1 flex items-center justify-center text-slate-500 text-sm">
            No conflict file selected.
          </div>
        )}
      </div>
    </div>
  );
}
