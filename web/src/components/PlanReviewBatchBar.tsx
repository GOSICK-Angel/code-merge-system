interface Props {
  pendingCount: number;
  draftedCount: number;
  unrecommendedCount: number; // items with no first-option recommendation
  notes: string;
  onNotesChange: (n: string) => void;
  onApplyRecommended: () => void;
  onApproveAll: () => void;
  onReject: () => void;
  onModify: () => void;
  serverDecided: boolean;
}

export function PlanReviewBatchBar({
  pendingCount,
  draftedCount,
  unrecommendedCount,
  notes,
  onNotesChange,
  onApplyRecommended,
  onApproveAll,
  onReject,
  onModify,
  serverDecided,
}: Props): JSX.Element {
  // Approve is enabled whenever the run is still awaiting a human
  // decision. Per-item drafts are best-effort context, not a
  // prerequisite — items the reviewer leaves un-drafted (e.g. those
  // with no selectable options) get serialised as ``user_choice=""``
  // so the back-end sees the reviewer's "approve, leave the rest"
  // intent explicitly. Disabled iff the server has already recorded
  // a plan_human_review.
  const approveDisabled = serverDecided;
  const approveReason = serverDecided
    ? "Plan already decided"
    : draftedCount === 0
      ? "No drafts — approve will accept the planner's defaults"
      : `Submit ${draftedCount} drafted choice(s) and approve the plan`;

  return (
    <div className="px-4 py-3 border-b border-slate-800 bg-slate-900/40 flex items-center gap-4 flex-wrap">
      <div className="text-xs text-slate-400">
        <span className="text-slate-100 font-medium">{pendingCount}</span>{" "}
        pending ·{" "}
        <span className="text-slate-100 font-medium">{draftedCount}</span>{" "}
        drafted
        {unrecommendedCount > 0 && (
          <>
            {" · "}
            <span className="text-amber-300">{unrecommendedCount}</span>{" "}
            without a default recommendation
          </>
        )}
      </div>
      <input
        type="text"
        value={notes}
        onChange={(e) => onNotesChange(e.target.value)}
        placeholder="Reviewer notes (optional)"
        disabled={serverDecided}
        className="flex-1 min-w-[200px] px-2 py-1 rounded border border-slate-700 bg-slate-950 text-slate-200 text-xs disabled:opacity-40"
      />
      <div className="flex gap-2">
        <button
          type="button"
          onClick={onApplyRecommended}
          disabled={serverDecided}
          className="text-xs px-3 py-1.5 rounded border border-slate-700 text-slate-200 hover:bg-slate-800 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          Apply recommended to all
        </button>
        <button
          type="button"
          onClick={onModify}
          disabled={serverDecided}
          title="Submit drafts (if any) and ask planner to revise"
          className="text-xs px-3 py-1.5 rounded border border-amber-700 text-amber-200 hover:bg-amber-900/30 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          Modify ({draftedCount})
        </button>
        <button
          type="button"
          onClick={onReject}
          disabled={serverDecided}
          title="Reject the plan — drafts will NOT be submitted"
          className="text-xs px-3 py-1.5 rounded border border-rose-700 text-rose-200 hover:bg-rose-900/30 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          Reject
        </button>
        <button
          type="button"
          onClick={onApproveAll}
          disabled={approveDisabled}
          title={approveReason}
          className="text-xs px-3 py-1.5 rounded bg-emerald-700 hover:bg-emerald-600 text-white disabled:opacity-40 disabled:cursor-not-allowed font-medium"
        >
          Approve all ({draftedCount})
        </button>
      </div>
    </div>
  );
}
