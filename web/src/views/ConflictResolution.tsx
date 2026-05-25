import { useEffect, useMemo } from "react";
import type { WsClient } from "../ws/client";
import {
  type ConflictDraft,
  useConflictDraftStore,
  validateDraft,
} from "../store/conflictDraftStore";
import { useRunStore } from "../store/runStore";
import {
  type HumanDecisionRequest,
  type MergeDecisionValue,
  SELECTABLE_DECISIONS,
} from "../types/state";
import { Card, Pill } from "../components/brutalist";

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

interface TreeNode {
  path: string;
  depth: number;
  isDir: boolean;
  name: string;
}

function buildTree(requests: HumanDecisionRequest[]): TreeNode[] {
  const seen = new Set<string>();
  const lines: TreeNode[] = [];
  const sorted = [...requests].sort((a, b) =>
    a.file_path.localeCompare(b.file_path),
  );
  for (const r of sorted) {
    const parts = r.file_path.split("/");
    for (let i = 0; i < parts.length; i += 1) {
      const sub = parts.slice(0, i + 1).join("/");
      if (seen.has(sub)) continue;
      seen.add(sub);
      lines.push({
        path: sub,
        depth: i,
        isDir: i < parts.length - 1,
        name: parts[i],
      });
    }
  }
  return lines;
}

// `fork` / `up` (not add/del): the preview is a diff between the fork's and
// upstream's version of the same region — both sides changed it. Framing the
// rows as deletions/additions (−/+, red/green) would wrongly imply fork
// removed and upstream added, so each row is tagged by which side it belongs
// to instead.
// forkLn / upLn are always normalised so column 1 is the fork side and
// column 2 the upstream side, regardless of which direction the underlying
// diff was generated in.
interface DiffLine {
  kind: "hunk" | "up" | "fork" | "ctx";
  forkLn: number | null;
  upLn: number | null;
  text: string;
}

// The preview is a real unified diff whose `--- X` / `+++ Y` header names the
// two sides (e.g. `--- fork` / `+++ upstream`). take_target reads
// fork→upstream; take_current is reversed, so we read the header rather than
// assuming a direction.
function diffSides(preview: string): { oldSide: string; newSide: string } {
  let oldSide = "fork";
  let newSide = "upstream";
  for (const raw of preview.split("\n")) {
    if (raw.startsWith("--- ")) {
      oldSide = raw.slice(4).split(":")[0].trim() || oldSide;
    } else if (raw.startsWith("+++ ")) {
      newSide = raw.slice(4).split(":")[0].trim() || newSide;
      break;
    }
  }
  return { oldSide, newSide };
}

function parseUnifiedDiff(preview: string): DiffLine[] {
  const { oldSide } = diffSides(preview);
  const oldIsFork = oldSide.toLowerCase().includes("fork");
  const out: DiffLine[] = [];
  let oldLn = 0;
  let newLn = 0;
  for (const raw of preview.split("\n")) {
    if (raw.startsWith("--- ") || raw.startsWith("+++ ")) continue;
    if (raw.startsWith("@@")) {
      const m = /@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(raw);
      if (m) {
        oldLn = Number.parseInt(m[1], 10);
        newLn = Number.parseInt(m[2], 10);
      }
      out.push({ kind: "hunk", forkLn: null, upLn: null, text: raw });
      continue;
    }
    if (raw.startsWith("-")) {
      out.push({
        kind: oldIsFork ? "fork" : "up",
        forkLn: oldIsFork ? oldLn : null,
        upLn: oldIsFork ? null : oldLn,
        text: raw.slice(1),
      });
      oldLn += 1;
    } else if (raw.startsWith("+")) {
      out.push({
        kind: oldIsFork ? "up" : "fork",
        forkLn: oldIsFork ? null : newLn,
        upLn: oldIsFork ? newLn : null,
        text: raw.slice(1),
      });
      newLn += 1;
    } else {
      const text = raw.startsWith(" ") ? raw.slice(1) : raw;
      out.push({
        kind: "ctx",
        forkLn: oldIsFork ? oldLn : newLn,
        upLn: oldIsFork ? newLn : oldLn,
        text,
      });
      oldLn += 1;
      newLn += 1;
    }
  }
  return out;
}

// Prefer take_target (fork→upstream); fall back to take_current (reversed,
// handled by the orientation check in parseUnifiedDiff).
function pickPreview(r: HumanDecisionRequest): string | null {
  const byDecision = (d: MergeDecisionValue): string | null =>
    r.options.find((o) => o.decision === d && o.preview_content)
      ?.preview_content ?? null;
  return byDecision("take_target") ?? byDecision("take_current") ?? null;
}

function diffRows(lines: DiffLine[]): JSX.Element[] {
  return lines.map((l, i) => (
    <div key={i} className={`row ${l.kind}`}>
      <span className="ln">{l.forkLn ?? ""}</span>
      <span className="ln">{l.upLn ?? ""}</span>
      <span className="code">{l.text}</span>
    </div>
  ));
}

export function ConflictResolution({ clientRef }: Props): JSX.Element {
  const snapshot = useRunStore((s) => s.snapshot);

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
  const tree = useMemo(() => buildTree(requests), [requests]);

  useEffect(() => {
    if (selectedFile && requests.some((r) => r.file_path === selectedFile))
      return;
    if (pending.length > 0) selectFile(pending[0].file_path);
    else if (requests.length > 0) selectFile(requests[0].file_path);
  }, [pending, requests, selectedFile, selectFile]);

  const current = useMemo(
    () => requests.find((r) => r.file_path === selectedFile) ?? null,
    [requests, selectedFile],
  );
  const currentDraft = current ? drafts[current.file_path] : undefined;

  const sendSingle = (filePath: string, draft: ConflictDraft) => {
    clientRef.current?.send({
      type: "submit_decision",
      payload: {
        filePath,
        decision: draft.decision,
        reviewer_notes: draft.reviewer_notes || null,
        custom_content: draft.custom_content || null,
      },
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
      reviewer_notes: draft!.reviewer_notes || null,
      custom_content: draft!.custom_content || null,
    }));
    if (items.length === 0) return;
    clientRef.current?.send({
      type: "submit_conflict_decisions_batch",
      payload: { items },
    });
  };

  const applyRecommendedClicked = () =>
    applyRecommendedToAll(
      pending.map((r) => ({
        file_path: r.file_path,
        recommendation: recommendedSubmittable(r),
      })),
    );

  const submitCurrent = () => {
    if (!current || !currentDraft) return;
    if (validateDraft(currentDraft) !== null) return;
    sendSingle(current.file_path, currentDraft);
  };

  const draftCount = Object.keys(drafts).length;
  const submitAllLabel = `Submit all drafts (${draftCount})`;

  const decisionOptions = current?.options ?? [];
  const useFallbackOptions = decisionOptions.length === 0;

  const preview = current ? pickPreview(current) : null;
  const diffLines = preview ? parseUnifiedDiff(preview) : [];

  return (
    <div>
      <div
        className="row between mb-2"
        style={{ alignItems: "flex-end" }}
      >
        <div>
          <h1>Conflict resolution</h1>
          <div className="subhead">
            {current ? (
              <>
                <code style={{ color: "var(--fg-0)" }}>
                  {current.file_path}
                </code>
                <span className="dim">
                  {" "}
                  · {current.conflict_points.length} conflict point
                  {current.conflict_points.length === 1 ? "" : "s"}
                  {current.analyst_confidence !== null && (
                    <>
                      {" · "}
                      <span style={{ color: "var(--accent)" }}>
                        analyst conf{" "}
                        {Math.round((current.analyst_confidence ?? 0) * 100)}%
                      </span>
                    </>
                  )}
                </span>
              </>
            ) : (
              <span className="dim">no conflict file selected</span>
            )}
          </div>
        </div>
        <div className="row">
          {(() => {
            // Pill tracks ``snapshot.status`` so a stale snapshot
            // lingering after the orchestrator advanced doesn't keep
            // claiming AWAITING_HUMAN. The pending-count suffix is only
            // meaningful inside the awaiting_human branch.
            const status = (snapshot?.status ?? "—").toUpperCase();
            const inAwaitingHuman = snapshot?.status === "awaiting_human";
            if (!inAwaitingHuman) {
              return <Pill tone="">{status}</Pill>;
            }
            return (
              <Pill tone="red" live>
                {status} · {pending.length}
              </Pill>
            );
          })()}
          {current && (
            <Pill tone="amber">PRIORITY {current.priority}</Pill>
          )}
        </div>
      </div>

      <div
        className="row mb-2"
        style={{ gap: 8, flexWrap: "wrap" }}
      >
        <button
          type="button"
          className="btn"
          onClick={applyRecommendedClicked}
          disabled={recommendedCount === 0}
        >
          APPLY RECOMMENDED ({recommendedCount})
        </button>
        <button
          type="button"
          className="btn primary"
          onClick={submitAllDrafts}
          disabled={submitDisabledReason !== null}
          title={submitDisabledReason ?? "Submit all drafts"}
        >
          {submitAllLabel}
        </button>
        <span className="dim" style={{ fontSize: 11, marginLeft: 8 }}>
          {draftCount} draft{draftCount === 1 ? "" : "s"} · {pending.length}{" "}
          pending file{pending.length === 1 ? "" : "s"}
        </span>
      </div>

      <div className="conflict-grid">
        <Card
          title="› CONFLICT QUEUE"
          hint={`${requests.length} file${requests.length === 1 ? "" : "s"}`}
          pad={false}
          style={{ overflow: "hidden" }}
        >
          <div className="tree">
            {tree.length === 0 ? (
              <div
                className="dim"
                style={{ fontSize: 11, padding: "12px 14px" }}
              >
                no conflict files
              </div>
            ) : (
              tree.map((n) => {
                const isFile = !n.isDir;
                const sel = isFile && n.path === selectedFile;
                const req = isFile
                  ? requests.find((r) => r.file_path === n.path)
                  : null;
                const drafted = req ? drafts[req.file_path] : undefined;
                return (
                  <div
                    key={n.path}
                    className={`node ${n.isDir ? "dir" : ""} ${sel ? "sel" : ""} indent-${Math.min(n.depth, 4)}`}
                    onClick={() => {
                      if (isFile) selectFile(n.path);
                    }}
                    role={isFile ? "button" : undefined}
                  >
                    <span className="nm">
                      {n.isDir ? (
                        <span className="dimmer">▾ </span>
                      ) : (
                        <span style={{ color: "var(--fg-3)" }}>· </span>
                      )}
                      {n.name}
                    </span>
                    {isFile && req?.human_decision && (
                      <span
                        style={{
                          color: "var(--green)",
                          fontSize: 9,
                          letterSpacing: "0.1em",
                        }}
                      >
                        ✓
                      </span>
                    )}
                    {isFile && !req?.human_decision && drafted && (
                      <span
                        style={{
                          color: "var(--amber)",
                          fontSize: 9,
                          letterSpacing: "0.1em",
                        }}
                      >
                        DRAFT
                      </span>
                    )}
                    {isFile && !req?.human_decision && !drafted && (
                      <span
                        className="dim"
                        style={{ fontSize: 9 }}
                      >
                        OPEN
                      </span>
                    )}
                  </div>
                );
              })
            )}
          </div>
        </Card>

        <div className="col">
          {current && current.conflict_points.length > 0 && (
            <Card
              title="› CONFLICT POINTS"
              hint={`${current.conflict_points.length} point${current.conflict_points.length === 1 ? "" : "s"}`}
            >
              <div
                className="row"
                style={{ gap: 8, flexWrap: "wrap" }}
              >
                {current.conflict_points.map((cp, i) => (
                  <div
                    key={cp.conflict_id ?? i}
                    className="btn"
                    style={{
                      flexDirection: "column",
                      alignItems: "flex-start",
                      padding: 10,
                      cursor: "default",
                      borderColor:
                        cp.severity === "high"
                          ? "var(--red-dim)"
                          : cp.severity === "medium"
                            ? "var(--orange-dim)"
                            : "var(--line)",
                    }}
                  >
                    <div
                      className="row"
                      style={{
                        width: "100%",
                        justifyContent: "space-between",
                        marginBottom: 4,
                      }}
                    >
                      <span style={{ fontWeight: 600 }}>
                        {cp.conflict_id ?? `#${i + 1}`} ·{" "}
                        {cp.line_range || "—"}
                      </span>
                      <Pill
                        tone={
                          cp.severity === "high"
                            ? "red"
                            : cp.severity === "medium"
                              ? "orange"
                              : "amber"
                        }
                      >
                        {cp.severity}
                      </Pill>
                    </div>
                    <span
                      className="dim"
                      style={{
                        fontSize: 10,
                        textTransform: "none",
                        letterSpacing: 0,
                      }}
                    >
                      {cp.conflict_type}
                    </span>
                  </div>
                ))}
              </div>
            </Card>
          )}

          {current && (
            <Card title="› CHANGE INTENT · fork vs upstream" hint="analyst summary">
              {current.analyst_recommendation && (
                <div className="reco-banner">
                  <span className="upcase">LLM recommendation</span>
                  <span className="reco-val">
                    {current.analyst_recommendation.toUpperCase()}
                  </span>
                  {current.analyst_confidence !== null && (
                    <span className="reco-conf">
                      {Math.round((current.analyst_confidence ?? 0) * 100)}% conf
                    </span>
                  )}
                </div>
              )}
              <div className="intent-split">
                <div className="intent-block fork">
                  <div className="intent-label">FORK · current</div>
                  <div className="intent-body">
                    {current.fork_change_summary || "—"}
                  </div>
                </div>
                <div className="intent-block upstream">
                  <div className="intent-label">UPSTREAM · incoming</div>
                  <div className="intent-body">
                    {current.upstream_change_summary || "—"}
                  </div>
                </div>
              </div>
              {current.analyst_rationale && (
                <div className="intent-rationale">
                  <div className="intent-label">why this recommendation</div>
                  <div className="intent-body dim">
                    {current.analyst_rationale}
                  </div>
                </div>
              )}
            </Card>
          )}

          <Card
            title="› CONFLICTING CODE · fork vs upstream"
            hint={current?.file_path ?? "—"}
            pad={false}
          >
            {!current ? (
              <div className="dim" style={{ fontSize: 11, padding: 16 }}>
                select a file to view its conflicting code
              </div>
            ) : preview ? (
              <div className="diff">
                <div className="head">
                  <span className="legend">
                    <i className="sw fork" />
                    <span className="branch">FORK</span>
                    <span className="dimmer">current</span>
                  </span>
                  <span className="legend">
                    <i className="sw up" />
                    <span className="branch">UPSTREAM</span>
                    <span className="dimmer">incoming</span>
                  </span>
                  <span className="legend-note">
                    both sides changed these lines — line nums: fork │ upstream
                  </span>
                </div>
                <div className="table">{diffRows(diffLines)}</div>
              </div>
            ) : (
              <div className="dim" style={{ fontSize: 11, padding: 16 }}>
                no code was captured for this conflict — see the fork /
                upstream change intent above to decide.
              </div>
            )}
          </Card>
        </div>

        <Card
          title="› DECISION"
          hint={current?.request_id ?? "—"}
        >
          {current ? (
            <>
              {current.human_decision && (
                <div
                  className="reco-banner"
                  style={{
                    borderColor: "var(--green)",
                    background:
                      "color-mix(in oklch, var(--green), transparent 90%)",
                  }}
                >
                  <span className="upcase">✓ decision submitted</span>
                  <span
                    className="reco-val"
                    style={{ color: "var(--green)" }}
                  >
                    {current.human_decision.toUpperCase()}
                  </span>
                </div>
              )}

              {current.analyst_recommendation && (
                <div style={{ padding: "10px 0" }}>
                  <div className="upcase">analyst recommendation</div>
                  <div
                    style={{
                      color: "var(--green)",
                      fontFamily: "var(--mono)",
                      fontSize: 13,
                      fontWeight: 600,
                      marginTop: 2,
                    }}
                  >
                    {current.analyst_recommendation.toUpperCase()}
                  </div>
                  <div className="confidence-bar">
                    <div className="b">
                      <div
                        className="f"
                        style={{
                          width: `${(current.analyst_confidence ?? 0) * 100}%`,
                        }}
                      />
                    </div>
                    <div className="v">
                      {Math.round((current.analyst_confidence ?? 0) * 100)}%
                    </div>
                  </div>
                  <div
                    className="dim"
                    style={{ fontSize: 11, lineHeight: 1.6 }}
                  >
                    {current.analyst_rationale}
                  </div>
                </div>
              )}

              <div className="hl-t" style={{ paddingTop: 12 }}>
                <div className="upcase mb-1">choose action</div>
                <div className="decision-panel">
                  {useFallbackOptions
                    ? SELECTABLE_DECISIONS.map((d) => (
                        <div
                          key={d}
                          className={`opt ${currentDraft?.decision === d ? "sel" : ""} ${
                            d === current.analyst_recommendation
                              ? "suggested"
                              : ""
                          }`}
                          onClick={() =>
                            setDraftDecision(current.file_path, d)
                          }
                        >
                          <div className="check" />
                          <div>
                            <div className="key">{d}</div>
                          </div>
                        </div>
                      ))
                    : decisionOptions.map((o) => {
                        const dec = o.decision as MergeDecisionValue;
                        const isSuggested =
                          dec === current.analyst_recommendation;
                        return (
                          <div
                            key={o.option_key}
                            className={`opt ${currentDraft?.decision === dec ? "sel" : ""} ${
                              isSuggested ? "suggested" : ""
                            }`}
                            onClick={() =>
                              setDraftDecision(current.file_path, dec)
                            }
                          >
                            <div className="check" />
                            <div>
                              <div className="key">
                                {o.option_key}
                              </div>
                              <div className="desc">{o.description}</div>
                              {o.risk_warning && (
                                <div className="warn">
                                  ⚠ {o.risk_warning}
                                </div>
                              )}
                            </div>
                          </div>
                        );
                      })}
                </div>

                {currentDraft?.decision === "manual_patch" && (
                  <textarea
                    value={currentDraft.custom_content}
                    onChange={(e) =>
                      setDraftCustomContent(
                        current.file_path,
                        e.target.value,
                      )
                    }
                    placeholder="paste manual patch (unified diff) ..."
                    style={{
                      width: "100%",
                      minHeight: 80,
                      background: "var(--bg-0)",
                      border: "1px solid var(--line)",
                      color: "var(--fg-1)",
                      fontFamily: "var(--mono)",
                      fontSize: 11,
                      padding: 8,
                      marginTop: 8,
                      resize: "vertical",
                    }}
                  />
                )}

                <textarea
                  value={currentDraft?.reviewer_notes ?? ""}
                  onChange={(e) =>
                    setDraftNotes(current.file_path, e.target.value)
                  }
                  disabled={!currentDraft}
                  placeholder={
                    currentDraft
                      ? "optional reviewer notes ..."
                      : "pick a decision first to enable notes"
                  }
                  style={{
                    width: "100%",
                    minHeight: 44,
                    background: "var(--bg-0)",
                    border: "1px solid var(--line)",
                    color: "var(--fg-1)",
                    fontFamily: "var(--mono)",
                    fontSize: 11,
                    padding: 8,
                    marginTop: 8,
                    resize: "vertical",
                  }}
                />

                <div className="row mt-1" style={{ gap: 6 }}>
                  <button
                    type="button"
                    className="btn primary grow"
                    onClick={submitCurrent}
                    disabled={
                      !currentDraft ||
                      validateDraft(currentDraft) !== null
                    }
                    style={{ justifyContent: "center" }}
                  >
                    {current.human_decision ? "Resubmit decision" : "Submit decision"}
                  </button>
                  {currentDraft && (
                    <button
                      type="button"
                      className="btn"
                      onClick={() => clearDraft(current.file_path)}
                    >
                      CLEAR
                    </button>
                  )}
                </div>
                <div
                  className="dim mt-1"
                  style={{ fontSize: 10.5 }}
                >
                  ws_send: <code>submit_decision</code> · request_id=
                  <code>{current.request_id ?? "—"}</code>
                </div>
              </div>
            </>
          ) : (
            <div
              className="dim"
              style={{ fontSize: 11, padding: "12px 0" }}
            >
              select a file from the queue to start
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
