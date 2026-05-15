import * as Dialog from "@radix-ui/react-dialog";
import {
  type PlanReviewDraft,
  usePlanReviewDraftStore,
} from "../store/planReviewDraftStore";
import type { PendingUserDecision } from "../types/state";

interface Props {
  item: PendingUserDecision | null;
  open: boolean;
  onClose: () => void;
}

/**
 * Single-item decision drawer.
 *
 * Crucially, **all writes here are draft-only** (zustand state). The
 * drawer never sends a WS frame — the user has to hit Approve/Modify in
 * the top BatchActionBar to flush the buffered choices. See
 * ``planReviewDraftStore.ts`` for the rationale (plan v1.1 §P1-3).
 */
export function PlanDecisionDrawer({ item, open, onClose }: Props): JSX.Element | null {
  const drafts = usePlanReviewDraftStore((s) => s.drafts);
  const setDraft = usePlanReviewDraftStore((s) => s.setDraft);
  const setDraftInput = usePlanReviewDraftStore((s) => s.setDraftInput);
  const clearDraft = usePlanReviewDraftStore((s) => s.clearDraft);

  if (!item) return null;
  const draft: PlanReviewDraft | undefined = drafts[item.item_id];
  const serverChoice = item.user_choice;

  return (
    <Dialog.Root open={open} onOpenChange={(v) => !v && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-slate-950/70 backdrop-blur-sm z-40" />
        <Dialog.Content className="fixed right-0 top-0 bottom-0 w-[min(720px,90vw)] bg-slate-900 border-l border-slate-800 shadow-2xl z-50 flex flex-col">
          <header className="px-5 py-4 border-b border-slate-800">
            <Dialog.Title className="text-sm font-semibold text-slate-100">
              {item.file_path}
            </Dialog.Title>
            <Dialog.Description className="text-xs text-slate-400 mt-1">
              {item.description}
            </Dialog.Description>
          </header>
          <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
            {item.current_classification && (
              <div className="text-xs">
                <span className="text-slate-500 uppercase tracking-wider">
                  Current classification:{" "}
                </span>
                <code className="text-amber-300">
                  {item.current_classification}
                </code>
              </div>
            )}
            {item.risk_context && (
              <section className="space-y-1">
                <div className="text-xs text-slate-500 uppercase tracking-wider">
                  Risk context
                </div>
                <p className="text-xs text-slate-300 leading-relaxed border-l-2 border-amber-700 pl-2">
                  {item.risk_context}
                </p>
              </section>
            )}
            {item.conflict_preview && (
              <section className="space-y-1">
                <div className="text-xs text-slate-500 uppercase tracking-wider">
                  Conflict preview
                </div>
                <pre className="text-[11px] font-mono text-slate-300 bg-slate-950 border border-slate-800 rounded p-2 whitespace-pre-wrap break-words">
                  {item.conflict_preview}
                </pre>
              </section>
            )}
            <section className="space-y-2">
              <div className="text-xs text-slate-500 uppercase tracking-wider">
                Options ({item.options.length})
              </div>
              {item.options.length === 0 ? (
                <p className="text-xs text-slate-500 italic">
                  No options offered — free-form input only.
                </p>
              ) : (
                <ul className="space-y-1.5">
                  {item.options.map((opt) => {
                    const selected = draft?.user_choice === opt.key;
                    const disabled = serverChoice !== null;
                    return (
                      <li key={opt.key}>
                        <button
                          type="button"
                          onClick={() => setDraft(item.item_id, opt.key)}
                          disabled={disabled}
                          className={`w-full text-left text-xs px-3 py-2 rounded border transition ${
                            selected
                              ? "border-sky-500 bg-sky-900/40 text-sky-100"
                              : "border-slate-700 hover:bg-slate-800 text-slate-200"
                          } disabled:opacity-40 disabled:cursor-not-allowed`}
                        >
                          <div className="font-medium">{opt.label}</div>
                          {opt.description && (
                            <div className="text-[11px] text-slate-400 mt-1">
                              {opt.description}
                            </div>
                          )}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </section>
            <label className="block text-xs">
              <span className="text-slate-500 uppercase tracking-wider">
                Per-item notes (optional)
              </span>
              <textarea
                value={draft?.user_input ?? ""}
                onChange={(e) => setDraftInput(item.item_id, e.target.value)}
                disabled={serverChoice !== null}
                rows={3}
                className="mt-1 w-full px-2 py-1.5 rounded border border-slate-700 bg-slate-950 text-slate-300 text-[11px] disabled:opacity-40"
              />
            </label>
          </div>
          <footer className="px-5 py-3 border-t border-slate-800 flex items-center justify-between text-xs">
            {serverChoice !== null ? (
              <span className="text-emerald-400">
                Submitted: <code>{serverChoice}</code>
              </span>
            ) : draft ? (
              <span className="text-slate-400">
                Drafted: <code className="text-sky-300">{draft.user_choice}</code>{" "}
                (not yet submitted — hit Approve all in the top bar)
              </span>
            ) : (
              <span className="text-slate-500 italic">No draft yet</span>
            )}
            <div className="flex gap-2">
              {draft && serverChoice === null && (
                <button
                  type="button"
                  onClick={() => clearDraft(item.item_id)}
                  className="px-3 py-1.5 rounded border border-slate-700 text-slate-300 hover:bg-slate-800"
                >
                  Clear draft
                </button>
              )}
              <Dialog.Close asChild>
                <button
                  type="button"
                  className="px-3 py-1.5 rounded bg-slate-800 hover:bg-slate-700 text-slate-200"
                >
                  Close
                </button>
              </Dialog.Close>
            </div>
          </footer>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
