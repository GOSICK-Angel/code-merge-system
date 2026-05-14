import type { MergeStateSnapshot } from "../types/state";

interface Props {
  snapshot: MergeStateSnapshot | null;
}

export function RiskBadge({ snapshot }: Props): JSX.Element {
  if (!snapshot) {
    return (
      <div className="bg-slate-900 border border-slate-800 rounded p-3">
        <div className="text-xs text-slate-500 uppercase tracking-wider mb-1">
          Risk overview
        </div>
        <div className="text-sm text-slate-500 italic">No state yet</div>
      </div>
    );
  }

  const buckets: Record<string, number> = {};
  for (const fd of snapshot.fileDiffs) {
    buckets[fd.risk_level] = (buckets[fd.risk_level] ?? 0) + 1;
  }
  const entries = Object.entries(buckets);
  const total = snapshot.fileDiffs.length;
  const pending = Object.values(snapshot.humanDecisionRequests).filter(
    (r) => r.human_decision === null,
  ).length;

  return (
    <div className="bg-slate-900 border border-slate-800 rounded p-3">
      <div className="text-xs text-slate-500 uppercase tracking-wider mb-1">
        Risk overview ({total} files)
      </div>
      {entries.length === 0 ? (
        <div className="text-sm text-slate-500 italic">No diffs yet</div>
      ) : (
        <ul className="space-y-0.5">
          {entries.map(([lvl, n]) => (
            <li key={lvl} className="flex justify-between text-xs">
              <span className="text-slate-400 capitalize">{lvl}</span>
              <span className="font-mono text-slate-200">{n}</span>
            </li>
          ))}
        </ul>
      )}
      {pending > 0 && (
        <div className="mt-2 text-xs text-rose-400">
          {pending} file{pending === 1 ? "" : "s"} awaiting human decision
        </div>
      )}
    </div>
  );
}
