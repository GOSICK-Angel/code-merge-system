interface Props {
  pendingCount: number;
  draftCount: number;
  recommendedCount: number;
  onApplyRecommendedToAll: () => void;
  onSubmitAllDrafts: () => void;
  submitDisabledReason: string | null;
}

export function BatchActionBar({
  pendingCount,
  draftCount,
  recommendedCount,
  onApplyRecommendedToAll,
  onSubmitAllDrafts,
  submitDisabledReason,
}: Props): JSX.Element {
  return (
    <div className="flex items-center justify-between px-4 py-2 border-b border-slate-800 bg-slate-900/40">
      <div className="text-xs text-slate-400">
        <span className="text-slate-100 font-medium">{pendingCount}</span> pending
        {" · "}
        <span className="text-slate-100 font-medium">{draftCount}</span> drafted
        {recommendedCount > 0 && (
          <>
            {" · "}
            <span className="text-amber-300">{recommendedCount}</span> have
            analyst recommendations
          </>
        )}
      </div>
      <div className="flex gap-2">
        <button
          type="button"
          onClick={onApplyRecommendedToAll}
          disabled={recommendedCount === 0}
          className="text-xs px-3 py-1.5 rounded border border-slate-700 text-slate-200 hover:bg-slate-800 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          Apply recommended to all
        </button>
        <button
          type="button"
          onClick={onSubmitAllDrafts}
          disabled={submitDisabledReason !== null}
          title={submitDisabledReason ?? "Submit every drafted decision"}
          className="text-xs px-3 py-1.5 rounded bg-emerald-700 hover:bg-emerald-600 text-white disabled:opacity-40 disabled:cursor-not-allowed font-medium"
        >
          Submit all drafts ({draftCount})
        </button>
      </div>
    </div>
  );
}
