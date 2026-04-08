import React from "react";
import { Box, Text, useInput } from "ink";
import { StatusBar } from "../components/status/StatusBar.js";
import { PlanSummary } from "../components/plan/PlanSummary.js";
import { BatchList } from "../components/plan/BatchList.js";
import { LayerGraph } from "../components/plan/LayerGraph.js";
import { Divider } from "../ink/Divider.js";
import { KeyHint } from "../ink/KeyHint.js";
import { useAppStore } from "../state/store.js";
import { useConnection } from "../context/ConnectionContext.js";

export function PlanReviewScreen() {
  const status = useAppStore((s) => s.status);
  const { send } = useConnection();
  const planReviewLog = useAppStore((s) => s.planReviewLog);

  const canReview = status === "awaiting_human";

  useInput((input) => {
    if (canReview && input === "a") {
      send({ type: "submit_plan_review", payload: { decision: "approve" } });
    } else if (canReview && input === "r") {
      send({ type: "submit_plan_review", payload: { decision: "reject" } });
    }
  });

  return (
    <Box flexDirection="column">
      <StatusBar />
      <Box flexDirection="row">
        <Box flexDirection="column" flexGrow={1}>
          <PlanSummary />
          <Divider />
          <BatchList />
        </Box>
        <Box flexDirection="column" width={30}>
          <LayerGraph />
          {planReviewLog.length > 0 && (
            <Box flexDirection="column" paddingX={1}>
              <Text bold>Review Rounds</Text>
              {planReviewLog.map((round) => (
                <Box key={round.round_number} gap={1}>
                  <Text color="gray">R{round.round_number}</Text>
                  <Text
                    color={round.verdict_result === "APPROVED" ? "green" : "yellow"}
                  >
                    {round.verdict_result}
                  </Text>
                  <Text color="gray">({round.issues_count} issues)</Text>
                </Box>
              ))}
            </Box>
          )}
        </Box>
      </Box>
      <Divider />
      {canReview ? (
        <KeyHint
          bindings={[
            { key: "a", label: "Approve" },
            { key: "r", label: "Reject" },
            { key: "Esc", label: "Back" },
          ]}
        />
      ) : (
        <KeyHint bindings={[{ key: "Esc", label: "Back" }]} />
      )}
    </Box>
  );
}
