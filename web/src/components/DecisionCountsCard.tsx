interface Props {
  counts: Record<string, number> | undefined;
}

const SOURCE_LABEL: Record<string, string> = {
  auto_planner: "Auto (planner)",
  auto_executor: "Auto (executor)",
  human: "Human",
  batch_human: "Batch human",
};

const SOURCE_COLOR: Record<string, string> = {
  auto_planner: "text-emerald-400",
  auto_executor: "text-sky-400",
  human: "text-rose-400",
  batch_human: "text-amber-400",
};

export function DecisionCountsCard({ counts }: Props): JSX.Element {
  const entries = Object.entries(counts ?? {});
  const total = entries.reduce((acc, [, n]) => acc + n, 0);

  return (
    <div className="bg-slate-900 border border-slate-800 rounded p-3">
      <div className="text-xs text-slate-500 uppercase tracking-wider mb-1">
        Decisions ({total})
      </div>
      {entries.length === 0 ? (
        <div className="text-sm text-slate-500 italic">None yet</div>
      ) : (
        <ul className="space-y-0.5">
          {entries.map(([src, n]) => (
            <li key={src} className="flex justify-between text-xs">
              <span className="text-slate-400">
                {SOURCE_LABEL[src] ?? src}
              </span>
              <span className={`font-mono ${SOURCE_COLOR[src] ?? "text-slate-200"}`}>
                {n}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
