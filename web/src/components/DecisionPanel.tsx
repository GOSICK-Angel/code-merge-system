import {
  type ConflictDraft,
  validateDraft,
} from "../store/conflictDraftStore";
import type {
  DecisionOption,
  HumanDecisionRequest,
  MergeDecisionValue,
} from "../types/state";
import { SELECTABLE_DECISIONS } from "../types/state";

interface Props {
  request: HumanDecisionRequest;
  draft: ConflictDraft | undefined;
  onPickDecision: (decision: MergeDecisionValue) => void;
  onNotesChange: (notes: string) => void;
  onCustomContentChange: (content: string) => void;
  onSubmit: () => void;
  onClear: () => void;
}

function decisionLabel(d: MergeDecisionValue): string {
  switch (d) {
    case "take_current":
      return "Take current (fork)";
    case "take_target":
      return "Take target (upstream)";
    case "semantic_merge":
      return "Semantic merge";
    case "manual_patch":
      return "Manual patch";
    case "skip":
      return "Skip";
    case "escalate_human":
      return "Escalate";
  }
}

function optionFor(
  request: HumanDecisionRequest,
  decision: MergeDecisionValue,
): DecisionOption | undefined {
  return request.options.find((o) => o.decision === decision);
}

export function DecisionPanel({
  request,
  draft,
  onPickDecision,
  onNotesChange,
  onCustomContentChange,
  onSubmit,
  onClear,
}: Props): JSX.Element {
  const submitted = request.human_decision;
  const error = draft ? validateDraft(draft) : null;
  const canSubmit = draft !== undefined && error === null;

  return (
    <section className="flex flex-col gap-3 p-4 border-t border-slate-800 bg-slate-900/50">
      <div className="flex items-baseline justify-between">
        <h2 className="text-xs font-medium text-slate-400 uppercase tracking-wider">
          Decision
        </h2>
        {request.analyst_recommendation && (
          <div className="text-[11px] text-slate-400">
            Analyst recommends{" "}
            <code className="text-amber-300">
              {request.analyst_recommendation}
            </code>
            {request.analyst_confidence !== null && (
              <span>
                {" · confidence "}
                <code className="text-slate-200">
                  {request.analyst_confidence.toFixed(2)}
                </code>
              </span>
            )}
          </div>
        )}
      </div>

      {request.analyst_rationale && (
        <p className="text-[11px] text-slate-400 leading-relaxed border-l-2 border-amber-700 pl-2">
          {request.analyst_rationale}
        </p>
      )}

      <div className="grid grid-cols-2 lg:grid-cols-5 gap-2">
        {SELECTABLE_DECISIONS.map((d) => {
          const opt = optionFor(request, d);
          const selected = draft?.decision === d;
          const disabled = submitted !== null;
          return (
            <button
              key={d}
              type="button"
              onClick={() => onPickDecision(d)}
              disabled={disabled}
              title={opt?.description ?? decisionLabel(d)}
              className={`text-left text-xs px-3 py-2 rounded border transition ${
                selected
                  ? "border-sky-500 bg-sky-900/40 text-sky-100"
                  : "border-slate-700 hover:bg-slate-800 text-slate-200"
              } disabled:opacity-40 disabled:cursor-not-allowed`}
            >
              <div className="font-medium">{decisionLabel(d)}</div>
              {opt?.risk_warning && (
                <div className="text-[10px] mt-1 text-amber-400">
                  ⚠ {opt.risk_warning}
                </div>
              )}
            </button>
          );
        })}
      </div>

      {draft?.decision === "manual_patch" && (
        <label className="block text-xs">
          <span className="text-slate-400">Custom patch *</span>
          <textarea
            value={draft.custom_content}
            onChange={(e) => onCustomContentChange(e.target.value)}
            rows={6}
            placeholder="Paste a unified diff or full file content"
            className="mt-1 w-full px-2 py-1.5 rounded border border-slate-700 bg-slate-950 text-slate-100 font-mono text-[11px] focus:border-sky-600 focus:outline-none"
          />
        </label>
      )}

      <label className="block text-xs">
        <span className="text-slate-400">Reviewer notes (optional)</span>
        <textarea
          value={draft?.reviewer_notes ?? ""}
          onChange={(e) => onNotesChange(e.target.value)}
          disabled={!draft}
          rows={2}
          className="mt-1 w-full px-2 py-1.5 rounded border border-slate-700 bg-slate-950 text-slate-300 text-[11px] disabled:opacity-40"
        />
      </label>

      {error && <div className="text-xs text-rose-400">{error}</div>}

      <div className="flex justify-end gap-2">
        {submitted ? (
          <span className="text-xs text-emerald-400 self-center">
            Already submitted: <code>{submitted}</code>
          </span>
        ) : (
          <>
            <button
              type="button"
              onClick={onClear}
              disabled={!draft}
              className="text-xs px-3 py-1.5 rounded border border-slate-700 text-slate-300 hover:bg-slate-800 disabled:opacity-40"
            >
              Clear draft
            </button>
            <button
              type="button"
              onClick={onSubmit}
              disabled={!canSubmit}
              className="text-xs px-3 py-1.5 rounded bg-sky-700 hover:bg-sky-600 text-white disabled:opacity-40 disabled:cursor-not-allowed font-medium"
            >
              Submit decision
            </button>
          </>
        )}
      </div>
    </section>
  );
}
