import { useMemo, useState } from "react";
import type { PendingUserDecision } from "../types/state";
import { type PlanReviewDraft } from "../store/planReviewDraftStore";

type SortMode = "default" | "by_file";

interface Props {
  items: PendingUserDecision[];
  drafts: Record<string, PlanReviewDraft>;
  selectedId: string | null;
  onSelect: (id: string) => void;
}

export function PendingDecisionsList({
  items,
  drafts,
  selectedId,
  onSelect,
}: Props): JSX.Element {
  const [sort, setSort] = useState<SortMode>("default");

  const sorted = useMemo(() => {
    if (sort === "by_file") {
      return [...items].sort((a, b) =>
        a.file_path.localeCompare(b.file_path),
      );
    }
    return items;
  }, [items, sort]);

  const pendingItems = sorted.filter((i) => i.user_choice === null);
  const decidedItems = sorted.filter((i) => i.user_choice !== null);

  function renderRow(item: PendingUserDecision): JSX.Element {
    const draft = drafts[item.item_id];
    const serverChoice = item.user_choice;
    const effective = serverChoice ?? draft?.user_choice ?? null;
    const isSelected = selectedId === item.item_id;
    return (
      <li key={item.item_id}>
        <button
          type="button"
          onClick={() => onSelect(item.item_id)}
          className={`w-full text-left px-2 py-1.5 rounded text-xs flex items-center gap-2 hover:bg-slate-800 ${
            isSelected
              ? "bg-slate-800 border border-sky-700"
              : "border border-transparent"
          }`}
        >
          <span className="text-slate-300 font-mono truncate flex-1">
            {item.file_path}
          </span>
          {effective && (
            <span
              className={`text-[9px] px-1.5 py-0.5 rounded uppercase tracking-wide ${
                serverChoice
                  ? "bg-emerald-800 text-emerald-100"
                  : "bg-sky-800 text-sky-100"
              }`}
              title={
                serverChoice
                  ? `Submitted: ${serverChoice}`
                  : `Draft (not yet submitted): ${draft?.user_choice}`
              }
            >
              {serverChoice ? effective : `~${effective}`}
            </span>
          )}
        </button>
      </li>
    );
  }

  return (
    <nav className="w-80 flex-shrink-0 border-r border-slate-800 flex flex-col">
      <header className="px-3 py-2 border-b border-slate-800 flex items-center justify-between">
        <div className="text-xs font-medium text-slate-400 uppercase tracking-wider">
          Pending decisions ({pendingItems.length})
        </div>
        <select
          value={sort}
          onChange={(e) => setSort(e.target.value as SortMode)}
          className="text-[10px] bg-slate-950 border border-slate-700 rounded px-1 py-0.5 text-slate-300"
        >
          <option value="default">Default order</option>
          <option value="by_file">By file path</option>
        </select>
      </header>
      <ul className="flex-1 overflow-y-auto p-2 space-y-0.5">
        {pendingItems.map(renderRow)}
        {decidedItems.length > 0 && (
          <>
            <li className="px-2 py-2 text-[10px] text-slate-500 uppercase tracking-wider border-t border-slate-800 mt-2">
              Decided ({decidedItems.length})
            </li>
            {decidedItems.map(renderRow)}
          </>
        )}
      </ul>
    </nav>
  );
}
