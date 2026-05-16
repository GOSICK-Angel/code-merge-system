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

function diffRows(
  upstream: string,
  fork: string,
  lineRange?: string,
): JSX.Element[] {
  const rows: JSX.Element[] = [];
  const forkLines = fork.split("\n");
  const upLines = upstream.split("\n");
  rows.push(
    <div key="hunk" className="row hunk">
      <span className="ln"></span>
      <span className="ln"></span>
      <span className="code">
        ═══════ CONFLICT{lineRange ? ` @ ${lineRange}` : ""} ═══════
      </span>
    </div>,
  );
  forkLines.forEach((line, i) => {
    if (!line) return;
    rows.push(
      <div key={`f-${i}`} className="row del">
        <span className="ln">{i + 1}</span>
        <span className="ln"></span>
        <span className="code">- {line}</span>
      </div>,
    );
  });
  upLines.forEach((line, i) => {
    if (!line) return;
    rows.push(
      <div key={`u-${i}`} className="row add">
        <span className="ln"></span>
        <span className="ln">{i + 1}</span>
        <span className="code">+ {line}</span>
      </div>,
    );
  });
  if (rows.length === 1) {
    rows.push(
      <div key="empty" className="row ctx">
        <span className="ln"></span>
        <span className="ln"></span>
        <span className="code dim">(no diff content captured)</span>
      </div>,
    );
  }
  return rows;
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
          <Pill tone="red" live>
            AWAITING_HUMAN · {pending.length}
          </Pill>
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

          <Card
            title="› DIFF · upstream vs fork"
            hint={current?.file_path ?? "—"}
            pad={false}
          >
            {current ? (
              <div className="diff">
                <div className="head">
                  <span>
                    <span className="dimmer">a/</span>
                    <span className="branch">fork</span>
                  </span>
                  <span className="dim">→</span>
                  <span>
                    <span className="dimmer">b/</span>
                    <span className="branch">upstream</span>
                  </span>
                </div>
                <div className="table">
                  {diffRows(
                    current.upstream_change_summary,
                    current.fork_change_summary,
                    current.conflict_points[0]?.line_range,
                  )}
                </div>
              </div>
            ) : (
              <div
                className="dim"
                style={{ fontSize: 11, padding: 16 }}
              >
                select a file to view its three-way diff
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
                    Submit decision
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
