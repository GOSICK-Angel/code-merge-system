import { useEffect, useMemo, useState } from "react";
import type { WsClient } from "../ws/client";
import type { OutboundMessage } from "../ws/messages";
import { useRunStore } from "../store/runStore";
import { Card, Pill } from "../components/brutalist";
import type { PillTone } from "../components/brutalist";
import type {
  JudgeIssuePayload,
  JudgeResolution,
  JudgeVerdict as JudgeVerdictType,
} from "../types/state";

interface Props {
  clientRef: React.MutableRefObject<WsClient | null>;
}

function severityTone(s: string): PillTone {
  if (s === "critical") return "red";
  if (s === "high") return "orange";
  if (s === "medium") return "amber";
  if (s === "low") return "green";
  return "teal";
}

function groupByFile(
  issues: JudgeIssuePayload[],
): Array<{ file_path: string; issues: JudgeIssuePayload[] }> {
  const map = new Map<string, JudgeIssuePayload[]>();
  for (const issue of issues) {
    const existing = map.get(issue.file_path);
    if (existing) existing.push(issue);
    else map.set(issue.file_path, [issue]);
  }
  return Array.from(map.entries()).map(([file_path, group]) => ({
    file_path,
    issues: group,
  }));
}

export function JudgeVerdict({ clientRef }: Props): JSX.Element {
  const snapshot = useRunStore((s) => s.snapshot);
  const conn = useRunStore((s) => s.conn);
  const verdict: JudgeVerdictType | null = snapshot?.judgeVerdict ?? null;
  const resolution: JudgeResolution | null = snapshot?.judgeResolution ?? null;
  const rerunRound = snapshot?.rerunRound ?? 0;
  const maxRerunRounds = snapshot?.maxRerunRounds ?? 0;

  const [pendingAction, setPendingAction] = useState<JudgeResolution | null>(
    null,
  );
  const [wsWarning, setWsWarning] = useState(false);

  // When WS reconnects and server hasn't confirmed our action, allow retry
  useEffect(() => {
    if (conn === "open" && resolution === null) {
      setPendingAction(null);
      setWsWarning(false);
    }
  }, [conn, resolution]);

  const send = (msg: OutboundMessage) => clientRef.current?.send(msg);
  const submit = (r: JudgeResolution) => {
    if (resolution !== null) return;
    if (conn !== "open") {
      setWsWarning(true);
      return;
    }
    setWsWarning(false);
    setPendingAction(r);
    send({ type: "submit_judge_resolution", payload: { resolution: r } });
  };

  const groupedIssues = useMemo(
    () => groupByFile(verdict?.issues ?? []),
    [verdict],
  );

  if (!verdict) {
    return (
      <div
        className="dim"
        style={{ padding: 24, fontSize: 12, textAlign: "center" }}
      >
        no judge verdict in state yet
      </div>
    );
  }

  const decided = resolution !== null || pendingAction !== null;
  const effectiveResolution = resolution ?? pendingAction;
  const issuesByCount = {
    critical: verdict.critical_issues_count,
    high: verdict.high_issues_count,
    medium: verdict.issues.filter((i) => i.severity === "medium").length,
    low: verdict.issues.filter((i) => i.severity === "low").length,
  };

  // build a 4-cell rounds display: round 0 = first verdict, plus 3 repair
  // budgets capped at maxRerunRounds (or 3 default for visual)
  const roundsBudget = Math.max(maxRerunRounds, 3);
  const rounds = Array.from({ length: roundsBudget + 1 }, (_, i) => {
    let status: "done" | "cur" | "pending" = "pending";
    if (i < rerunRound) status = "done";
    else if (i === rerunRound) status = "cur";
    const label =
      i === 0
        ? verdict.veto_triggered
          ? "veto"
          : verdict.verdict
        : i === rerunRound
          ? decided && effectiveResolution === "rerun"
            ? "in progress"
            : "open"
          : i === rerunRound + 1
            ? "queued"
            : "pending";
    return { i, status, label };
  });

  return (
    <div>
      <div
        className="row between mb-2"
        style={{ alignItems: "flex-end" }}
      >
        <div>
          <h1>Judge verdict</h1>
          <div className="subhead">
            Reviewed{" "}
            <b style={{ color: "var(--fg-0)" }}>
              {verdict.reviewed_files_count.toLocaleString()}
            </b>{" "}
            files · {verdict.passed_files.length.toLocaleString()} passed ·{" "}
            {verdict.failed_files.length} failed ·{" "}
            {verdict.conditional_files.length} conditional
          </div>
        </div>
        <div className="row">
          <Pill
            tone={verdict.veto_triggered ? "red" : "amber"}
            live={!decided}
          >
            {verdict.veto_triggered ? "VETO" : verdict.verdict.toUpperCase()}
          </Pill>
          <Pill tone="teal">
            CONF {Math.round(verdict.overall_confidence * 100)}%
          </Pill>
        </div>
      </div>

      <div
        className={`verdict-banner ${verdict.veto_triggered ? "veto" : ""}`}
      >
        <div>
          <div className="title">
            VERDICT:{" "}
            <span className="v">
              {verdict.verdict.toUpperCase().replace("_", " ")}
            </span>
          </div>
          <div className="sub">
            {verdict.veto_triggered && verdict.veto_reason
              ? `⛔ ${verdict.veto_reason}`
              : verdict.summary}
          </div>
        </div>
        <div className="row" style={{ gap: 10 }}>
          <button
            type="button"
            className="btn primary"
            onClick={() => submit("accept")}
            disabled={decided}
          >
            ✓ ACCEPT
          </button>
          <button
            type="button"
            className="btn"
            onClick={() => submit("rerun")}
            disabled={
              decided ||
              (maxRerunRounds > 0 && rerunRound >= maxRerunRounds)
            }
            title={
              maxRerunRounds > 0 && rerunRound >= maxRerunRounds
                ? "Rerun budget exhausted"
                : "Clear failed-file decisions and rerun auto-merge"
            }
          >
            ↻ RERUN ({rerunRound}/{maxRerunRounds || "∞"})
          </button>
          <button
            type="button"
            className="btn danger"
            onClick={() => submit("abort")}
            disabled={decided}
          >
            ⌥ ABORT
          </button>
        </div>
      </div>

      {wsWarning && (
        <div
          style={{
            marginBottom: 12,
            padding: "8px 14px",
            background: "color-mix(in oklch, var(--red), transparent 85%)",
            border: "1px solid var(--red)",
            fontSize: 11.5,
            color: "var(--fg-1)",
          }}
        >
          ⚠ WebSocket is not connected — action was not sent. The merge
          backend may not be running. Retrying connection…
        </div>
      )}

      <Card title="› REPAIR ROUNDS" hint={`max ${maxRerunRounds || "∞"}`}>
        <div className="rounds">
          {rounds.map((r) => (
            <div
              key={r.i}
              className={`r ${
                r.status === "done"
                  ? "done"
                  : r.status === "cur"
                    ? "cur"
                    : ""
              }`}
            >
              <div>
                <div className="lbl">ROUND {r.i}</div>
                <div style={{ fontSize: 10, marginTop: 2 }}>{r.label}</div>
              </div>
            </div>
          ))}
        </div>
        <div className="dim" style={{ fontSize: 11 }}>
          {decided ? (
            <>
              resolution =&nbsp;<code>{effectiveResolution}</code>
              {resolution === null && (
                <span className="dim"> (awaiting server ack)</span>
              )}
            </>
          ) : (
            "Pick an action above to resume the orchestrator."
          )}
        </div>
      </Card>

      <div className="judge-grid mt-2">
        <div style={{ minWidth: 0 }}>
          <Card
            title="› ISSUES BY SEVERITY"
            hint={`${issuesByCount.critical}C · ${issuesByCount.high}H · ${issuesByCount.medium}M · ${issuesByCount.low}L`}
            style={{ overflow: "hidden" }}
          >
            <div
              className="dim"
              style={{ fontSize: 10, marginBottom: 8, letterSpacing: "0.06em" }}
            >
              Read-only analysis — use ACCEPT / RERUN / ABORT above to act.
            </div>
            <div className="row mb-2" style={{ gap: 8 }}>
              <Pill tone="red">CRITICAL · {issuesByCount.critical}</Pill>
              <Pill tone="orange">HIGH · {issuesByCount.high}</Pill>
              <Pill tone="amber">MEDIUM · {issuesByCount.medium}</Pill>
              <Pill tone="green">LOW · {issuesByCount.low}</Pill>
            </div>
            {groupedIssues.length === 0 ? (
              <div
                className="dim"
                style={{ fontSize: 11, padding: "12px 0" }}
              >
                no issues recorded
              </div>
            ) : (
              groupedIssues.flatMap((group) =>
                group.issues.map((iss, idx) => (
                  <div
                    key={iss.issue_id ?? `${group.file_path}-${idx}`}
                    className={`issue ${String(iss.severity).toLowerCase()}`}
                  >
                    <div className="top">
                      <div className="row" style={{ gap: 8 }}>
                        <Pill tone={severityTone(String(iss.severity))}>
                          {String(iss.severity)}
                        </Pill>
                        <code
                          className="dim"
                          style={{ fontSize: 10 }}
                        >
                          {iss.issue_id ?? `iss-${idx}`} · {iss.issue_type}
                        </code>
                        {iss.must_fix_before_merge && (
                          <span
                            style={{
                              color: "var(--red)",
                              fontSize: 10,
                              letterSpacing: "0.1em",
                            }}
                          >
                            MUST_FIX
                          </span>
                        )}
                      </div>
                      {iss.affected_lines.length > 0 && (
                        <span
                          className="dim"
                          style={{ fontSize: 10, fontFamily: "var(--mono)" }}
                        >
                          lines {iss.affected_lines.slice(0, 4).join(", ")}
                          {iss.affected_lines.length > 4 ? "…" : ""}
                        </span>
                      )}
                    </div>
                    <div className="fp">{iss.file_path}</div>
                    <div className="desc mt-1">{iss.description}</div>
                    {iss.suggested_fix && (
                      <div className="fix">{iss.suggested_fix}</div>
                    )}
                  </div>
                )),
              )
            )}
          </Card>
        </div>

        <div className="col">
          <Card
            title="› FAILED FILES"
            hint={`${verdict.failed_files.length + verdict.conditional_files.length} files`}
            style={{ minWidth: 0, overflow: "hidden" }}
          >
            <div
              className="dim"
              style={{ fontSize: 10, marginBottom: 8, letterSpacing: "0.06em" }}
            >
              {verdict.failed_files.length > 0
                ? "RERUN to retry auto-merge; ACCEPT to force-merge as-is."
                : "CONDITIONAL files passed with caveats — ACCEPT to proceed."}
            </div>
            {verdict.failed_files.length === 0 &&
            verdict.conditional_files.length === 0 ? (
              <div
                className="dim"
                style={{ fontSize: 11, padding: "12px 0" }}
              >
                no failed or conditional files
              </div>
            ) : (
              <div
                style={{ maxHeight: 340, overflowY: "auto", overflowX: "hidden" }}
              >
                {verdict.failed_files.map((f) => (
                  <div
                    key={`fail-${f}`}
                    style={{
                      padding: "10px 0",
                      borderBottom: "1px solid var(--line)",
                      fontFamily: "var(--mono)",
                      fontSize: 11.5,
                    }}
                  >
                    <div className="row between">
                      <span
                        style={{
                          color: "var(--fg-0)",
                          wordBreak: "break-all",
                          minWidth: 0,
                          flex: 1,
                        }}
                      >
                        {f}
                      </span>
                      <Pill tone="red">FAILED</Pill>
                    </div>
                  </div>
                ))}
                {verdict.conditional_files.map((f) => (
                  <div
                    key={`cond-${f}`}
                    style={{
                      padding: "10px 0",
                      borderBottom: "1px solid var(--line)",
                      fontFamily: "var(--mono)",
                      fontSize: 11.5,
                    }}
                  >
                    <div className="row between">
                      <span
                        style={{
                          color: "var(--fg-0)",
                          wordBreak: "break-all",
                          minWidth: 0,
                          flex: 1,
                        }}
                      >
                        {f}
                      </span>
                      <Pill tone="amber">CONDITIONAL</Pill>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Card>

          {verdict.repair_instructions.length > 0 && (
            <Card
              title="› REPAIR INSTRUCTIONS"
              hint={`${verdict.repair_instructions.length}`}
              style={{ minWidth: 0, overflow: "hidden" }}
            >
              {verdict.repair_instructions.map((r, idx) => (
                <div
                  key={r.source_issue_id ?? idx}
                  className="hairline"
                  style={{
                    padding: 10,
                    marginBottom: 8,
                    background: "var(--bg-2)",
                    fontSize: 11.5,
                  }}
                >
                  <div
                    className="row between"
                    style={{ marginBottom: 4 }}
                  >
                    <code style={{ color: "var(--fg-0)" }}>
                      {r.file_path}
                    </code>
                    <span
                      style={{
                        fontSize: 10,
                        color: r.is_repairable
                          ? "var(--green)"
                          : "var(--red)",
                        letterSpacing: "0.08em",
                      }}
                    >
                      {r.is_repairable ? "REPAIRABLE" : "MANUAL"}
                    </span>
                  </div>
                  <div style={{ color: "var(--fg-1)" }}>{r.instruction}</div>
                </div>
              ))}
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
