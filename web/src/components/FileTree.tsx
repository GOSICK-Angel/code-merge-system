import type {
  ConflictDraft,
} from "../store/conflictDraftStore";
import type { HumanDecisionRequest } from "../types/state";

interface Props {
  requests: HumanDecisionRequest[];
  drafts: Record<string, ConflictDraft>;
  selectedFile: string | null;
  onSelect: (filePath: string) => void;
}

function badgeColor(decision: string | null): string {
  switch (decision) {
    case "take_current":
      return "bg-sky-700 text-sky-100";
    case "take_target":
      return "bg-indigo-700 text-indigo-100";
    case "semantic_merge":
      return "bg-violet-700 text-violet-100";
    case "manual_patch":
      return "bg-amber-700 text-amber-100";
    case "skip":
      return "bg-slate-700 text-slate-300";
    default:
      return "bg-slate-800 text-slate-500";
  }
}

export function FileTree({
  requests,
  drafts,
  selectedFile,
  onSelect,
}: Props): JSX.Element {
  const sorted = [...requests].sort((a, b) => {
    if (a.priority !== b.priority) return b.priority - a.priority;
    return a.file_path.localeCompare(b.file_path);
  });
  const pending = sorted.filter((r) => r.human_decision === null);
  const decided = sorted.filter((r) => r.human_decision !== null);

  function renderRow(r: HumanDecisionRequest): JSX.Element {
    const draft = drafts[r.file_path];
    const submitted = r.human_decision;
    const effective = submitted ?? draft?.decision ?? null;
    const isSelected = selectedFile === r.file_path;
    return (
      <li key={r.file_path}>
        <button
          type="button"
          onClick={() => onSelect(r.file_path)}
          className={`w-full text-left px-2 py-1.5 rounded text-xs font-mono flex items-center gap-2 hover:bg-slate-800 ${
            isSelected
              ? "bg-slate-800 border border-sky-700"
              : "border border-transparent"
          }`}
        >
          <span className="text-slate-500 w-4 text-right">{r.priority}</span>
          <span className="flex-1 truncate text-slate-100">{r.file_path}</span>
          {effective && (
            <span
              className={`text-[9px] px-1.5 py-0.5 rounded uppercase tracking-wide ${badgeColor(
                effective,
              )}`}
              title={
                submitted
                  ? `Submitted: ${submitted}`
                  : `Draft (not submitted): ${draft?.decision}`
              }
            >
              {submitted ? effective : `~${effective}`}
            </span>
          )}
        </button>
      </li>
    );
  }

  return (
    <nav className="w-72 flex-shrink-0 border-r border-slate-800 overflow-y-auto">
      <div className="px-3 py-2 border-b border-slate-800">
        <div className="text-xs font-medium text-slate-400 uppercase tracking-wider">
          Pending ({pending.length})
        </div>
      </div>
      <ul className="px-1 py-1 space-y-0.5">{pending.map(renderRow)}</ul>
      {decided.length > 0 && (
        <>
          <div className="px-3 py-2 border-y border-slate-800 mt-2">
            <div className="text-xs font-medium text-slate-500 uppercase tracking-wider">
              Decided ({decided.length})
            </div>
          </div>
          <ul className="px-1 py-1 space-y-0.5">{decided.map(renderRow)}</ul>
        </>
      )}
    </nav>
  );
}
