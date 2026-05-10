import React, { useEffect, useRef, useState } from "react";
import { Box, Text, useInput } from "ink";
import { StatusBar } from "../components/status/StatusBar.js";
import { PlanSummary } from "../components/plan/PlanSummary.js";
import { BatchList } from "../components/plan/BatchList.js";
import { PlanDecisionWizard } from "../components/decisions/PlanDecisionWizard.js";
import { Divider } from "../ink/Divider.js";
import { KeyHint } from "../ink/KeyHint.js";
import { ScrollBox } from "../ink/ScrollBox.js";
import { useAppStore } from "../state/store.js";
import { useConnection } from "../context/ConnectionContext.js";
import { useKeybindingContext } from "../context/KeybindingContext.js";
import type { PlanReviewRound, ReviewConclusion } from "../state/types.js";

type ViewMode = "overview" | "negotiation" | "decisions";

const ACTION_COLORS: Record<string, string> = {
  accept: "green",
  reject: "red",
  discuss: "yellow",
};

const ACTION_LABELS: Record<string, string> = {
  accept: "ACCEPT",
  reject: "REJECT",
  discuss: "DISCUSS",
};

function NegotiationRound({ round }: { round: PlanReviewRound }) {
  const verdictColor =
    round.verdict_result === "approved" ? "green" : "yellow";

  return (
    <Box flexDirection="column" paddingX={1} marginBottom={1}>
      <Box gap={1}>
        <Text bold>Round {round.round_number}</Text>
        <Text color={verdictColor}>[{round.verdict_result}]</Text>
        <Text color="gray">{round.issues_count} issues</Text>
      </Box>

      {round.verdict_summary && (
        <Box paddingLeft={2}>
          <Text color="gray" wrap="wrap">
            Judge: {round.verdict_summary}
          </Text>
        </Box>
      )}

      {round.issues_detail && round.issues_detail.length > 0 && (
        <Box flexDirection="column" paddingLeft={2} marginTop={0}>
          <Text bold color="magenta">
            Judge Issues:
          </Text>
          {round.issues_detail.map((iss, i) => (
            <Box key={i} paddingLeft={1}>
              <Text>
                <Text color="white">{iss.file_path}</Text>
                <Text color="gray">
                  {" "}
                  {iss.current} → {iss.suggested}
                </Text>
                <Text color="gray"> — {iss.reason}</Text>
              </Text>
            </Box>
          ))}
        </Box>
      )}

      {round.planner_responses && round.planner_responses.length > 0 && (
        <Box flexDirection="column" paddingLeft={2} marginTop={0}>
          <Text bold color="cyan">
            Planner Responses:
          </Text>
          {round.planner_responses.map((pr, i) => (
            <Box key={i} paddingLeft={1}>
              <Text>
                <Text color={ACTION_COLORS[pr.action] ?? "gray"} bold>
                  [{ACTION_LABELS[pr.action] ?? pr.action}]
                </Text>
                <Text color="white"> {pr.file_path}</Text>
                <Text color="gray"> — {pr.reason}</Text>
                {pr.counter_proposal && (
                  <Text color="yellow">
                    {" "}
                    | Proposal: {pr.counter_proposal}
                  </Text>
                )}
              </Text>
            </Box>
          ))}
        </Box>
      )}

      {round.plan_diff && round.plan_diff.length > 0 && (
        <Box flexDirection="column" paddingLeft={2} marginTop={0}>
          <Text bold color="blue">
            Plan Diff:
          </Text>
          {round.plan_diff.map((d, i) => (
            <Box key={i} paddingLeft={1}>
              <Text>
                <Text color="white">{d.file_path}</Text>
                <Text color="red"> {d.old_risk}</Text>
                <Text color="gray"> → </Text>
                <Text color="green">{d.new_risk}</Text>
              </Text>
            </Box>
          ))}
        </Box>
      )}

      {round.planner_revision_summary && (
        <Box paddingLeft={2}>
          <Text color="cyan">
            Summary: {round.planner_revision_summary}
          </Text>
        </Box>
      )}
    </Box>
  );
}

const CONCLUSION_COLORS: Record<string, string> = {
  approved: "green",
  max_rounds: "yellow",
  stalled: "yellow",
  llm_failure: "red",
  critical_replan: "red",
};

const CONCLUSION_ICONS: Record<string, string> = {
  approved: "✓",
  max_rounds: "⚠",
  stalled: "⚠",
  llm_failure: "✗",
  critical_replan: "↻",
};

const CONCLUSION_LABELS: Record<string, string> = {
  approved: "APPROVED",
  max_rounds: "MAX ROUNDS REACHED",
  stalled: "STALLED",
  llm_failure: "LLM FAILURE",
  critical_replan: "CRITICAL REPLAN",
};

function ConclusionBanner({
  conclusion,
  pendingCount,
}: {
  conclusion: ReviewConclusion;
  pendingCount: number;
}) {
  const color = CONCLUSION_COLORS[conclusion.reason] ?? "gray";
  const icon = CONCLUSION_ICONS[conclusion.reason] ?? "?";
  const label = CONCLUSION_LABELS[conclusion.reason] ?? conclusion.reason;

  return (
    <Box
      flexDirection="column"
      paddingX={1}
      marginBottom={0}
      borderStyle="single"
      borderColor={color}
    >
      <Box gap={1}>
        <Text color={color} bold>
          {icon} Review Result: {label}
        </Text>
        <Text color="gray">
          (round {conclusion.final_round}/{conclusion.max_rounds})
        </Text>
      </Box>
      <Box paddingLeft={2}>
        <Text wrap="wrap">{conclusion.summary}</Text>
      </Box>

      {conclusion.reason === "stalled" &&
        conclusion.rejection_details &&
        conclusion.rejection_details.length > 0 && (
          <Box flexDirection="column" paddingLeft={2} marginTop={0}>
            <Text bold color="red">
              Planner rejections:
            </Text>
            {conclusion.rejection_details.slice(0, 5).map((rd, i) => (
              <Box key={i} paddingLeft={1}>
                <Text>
                  <Text color="white">{rd.file_path}</Text>
                  <Text color="gray">
                    {" "}
                    (Judge → {rd.judge_suggested})
                  </Text>
                  <Text color="red"> — {rd.planner_reason}</Text>
                </Text>
              </Box>
            ))}
            {conclusion.rejection_details.length > 5 && (
              <Box paddingLeft={1}>
                <Text color="gray">
                  ... and {conclusion.rejection_details.length - 5} more (press
                  [n] for full negotiation log)
                </Text>
              </Box>
            )}
          </Box>
        )}

      {pendingCount > 0 && (
        <Box paddingLeft={2} marginTop={0}>
          <Text color="yellow" bold>
            → {pendingCount} items need your decision — press [d] to review
          </Text>
        </Box>
      )}
    </Box>
  );
}

export function PlanReviewScreen() {
  const status = useAppStore((s) => s.status);
  const { send } = useConnection();
  const planReviewLog = useAppStore((s) => s.planReviewLog);
  const reviewConclusion = useAppStore((s) => s.reviewConclusion);
  const pendingUserDecisions = useAppStore((s) => s.pendingUserDecisions);
  const messages = useAppStore((s) => s.messages);

  const [viewMode, setViewMode] = useState<ViewMode>(
    () => status === "awaiting_human" && pendingUserDecisions.length > 0 ? "decisions" : "overview"
  );

  useEffect(() => {
    if (status === "awaiting_human" && pendingUserDecisions.length > 0) {
      setViewMode("decisions");
    }
  }, [status, pendingUserDecisions.length]);

  const canReview = status === "awaiting_human";
  const hasDecisions = pendingUserDecisions.length > 0;
  const allDecided = hasDecisions && pendingUserDecisions.every((d) => d.user_choice);

  const viewModeRef = useRef<ViewMode>(viewMode);
  viewModeRef.current = viewMode;

  const { register, unregister } = useKeybindingContext();
  useEffect(() => {
    const handlerId = "plan_review_screen";
    register(handlerId, (input: string) => {
      if (viewModeRef.current === "decisions" && input >= "1" && input <= "9") {
        return true;
      }
      return false;
    });
    return () => unregister(handlerId);
  }, [register, unregister]);

  const reportMsg = messages.find(
    (m: { type: string }) => m.type === "plan_report"
  );
  const reportPath = reportMsg
    ? (reportMsg as { content?: string }).content
    : null;

  useInput((input, key) => {
    if (input === "n") {
      setViewMode("negotiation");
    } else if (input === "d" && hasDecisions) {
      setViewMode("decisions");
    } else if (input === "o") {
      setViewMode("overview");
    } else if (key.escape) {
      if (viewMode !== "overview") {
        setViewMode("overview");
      }
    }

    if (canReview && viewMode === "overview") {
      if (input === "a") {
        if (!hasDecisions || allDecided) {
          send({
            type: "submit_plan_review",
            payload: { decision: "approve" },
          });
        }
      } else if (input === "r") {
        send({
          type: "submit_plan_review",
          payload: { decision: "reject" },
        });
      }
    }
  });

  const bindings =
    viewMode === "overview"
      ? [
          { key: "n", label: "Negotiation" },
          ...(hasDecisions ? [{ key: "d", label: "Decisions" }] : []),
          ...(canReview
            ? [
                {
                  key: "a",
                  label: allDecided || !hasDecisions ? "Approve" : "Approve (decide first)",
                },
                { key: "r", label: "Reject" },
              ]
            : []),
          { key: "Esc", label: "Back" },
        ]
      : viewMode === "negotiation"
        ? [
            { key: "o", label: "Overview" },
            ...(hasDecisions ? [{ key: "d", label: "Decisions" }] : []),
            { key: "↑↓", label: "Scroll" },
            { key: "Esc", label: "Back" },
          ]
        : (() => {
            const maxOpts =
              pendingUserDecisions.length > 0
                ? Math.max(...pendingUserDecisions.map((d) => d.options.length))
                : 3;
            return [
              { key: "↑↓", label: "Select" },
              { key: `1-${maxOpts}`, label: "Quick pick" },
              { key: "⏎", label: "Confirm" },
              { key: "←", label: "Prev" },
              { key: "o", label: "Overview" },
            ];
          })();

  return (
    <Box flexDirection="column">
      <StatusBar />

      {reportPath && (
        <Box paddingX={1}>
          <Text color="green">Plan report: </Text>
          <Text color="white">{reportPath}</Text>
        </Box>
      )}

      <Box paddingX={1} gap={2}>
        <Text
          bold={viewMode === "overview"}
          color={viewMode === "overview" ? "white" : "gray"}
        >
          [o] Overview
        </Text>
        <Text
          bold={viewMode === "negotiation"}
          color={viewMode === "negotiation" ? "white" : "gray"}
        >
          [n] Negotiation ({planReviewLog.length} rounds)
        </Text>
        {hasDecisions && (
          <Text
            bold={viewMode === "decisions"}
            color={viewMode === "decisions" ? "white" : "gray"}
          >
            [d] Decisions ({pendingUserDecisions.filter((d) => d.user_choice).length}/
            {pendingUserDecisions.length})
          </Text>
        )}
      </Box>

      <Divider />

      {viewMode === "overview" && (
        <Box flexDirection="column">
          {reviewConclusion && (
            <ConclusionBanner
              conclusion={reviewConclusion}
              pendingCount={
                pendingUserDecisions.filter((d) => !d.user_choice).length
              }
            />
          )}
          <Box flexDirection="row">
          <Box flexDirection="column" flexGrow={1}>
            <PlanSummary />
            <Divider />
            <BatchList />
          </Box>
          <Box flexDirection="column" width={35}>
            {planReviewLog.length > 0 && (
              <Box flexDirection="column" paddingX={1}>
                <Text bold>Review Rounds</Text>
                {planReviewLog.map((round) => {
                  const accepted = (round.planner_responses ?? []).filter(
                    (r) => r.action === "accept"
                  ).length;
                  const rejected = (round.planner_responses ?? []).filter(
                    (r) => r.action === "reject"
                  ).length;
                  const discussed = (round.planner_responses ?? []).filter(
                    (r) => r.action === "discuss"
                  ).length;
                  const hasResponses =
                    accepted + rejected + discussed > 0;

                  return (
                    <Box key={round.round_number} flexDirection="column">
                      <Box gap={1}>
                        <Text color="gray">R{round.round_number}</Text>
                        <Text
                          color={
                            round.verdict_result === "approved"
                              ? "green"
                              : "yellow"
                          }
                        >
                          {round.verdict_result}
                        </Text>
                        <Text color="gray">
                          ({round.issues_count} issues)
                        </Text>
                      </Box>
                      {hasResponses && (
                        <Box paddingLeft={3}>
                          <Text color="green">{accepted}✓ </Text>
                          <Text color="red">{rejected}✗ </Text>
                          <Text color="yellow">{discussed}? </Text>
                        </Box>
                      )}
                    </Box>
                  );
                })}
              </Box>
            )}
          </Box>
        </Box>
        </Box>
      )}

      {viewMode === "negotiation" && (
        <Box flexDirection="column" paddingX={0}>
          <ScrollBox height={18} isActive={true}>
            {planReviewLog.length === 0
              ? [
                  <Text key="empty" color="gray">
                    No review rounds yet.
                  </Text>,
                ]
              : planReviewLog.map((round) => (
                  <NegotiationRound key={round.round_number} round={round} />
                ))}
          </ScrollBox>
        </Box>
      )}

      {viewMode === "decisions" && hasDecisions && (
        <PlanDecisionWizard items={pendingUserDecisions} isActive={true} />
      )}

      <Divider />
      <KeyHint bindings={bindings} />
    </Box>
  );
}
