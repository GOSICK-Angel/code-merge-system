import React from "react";
import { Box, Text } from "ink";
import { StatusBar } from "../components/status/StatusBar.js";
import { GateHistory } from "../components/gates/GateHistory.js";
import { MemorySummary } from "../components/memory/MemorySummary.js";
import { Divider } from "../ink/Divider.js";
import { KeyHint } from "../ink/KeyHint.js";
import { ProgressBar } from "../ink/ProgressBar.js";
import { useAppStore } from "../state/store.js";
import { selectRiskCounts, selectDecidedCount, selectTotalDecisionCount } from "../state/selectors.js";

export function ReportScreen() {
  const status = useAppStore((s) => s.status);
  const fileDiffs = useAppStore((s) => s.fileDiffs);
  const fileDecisionRecords = useAppStore((s) => s.fileDecisionRecords);
  const errors = useAppStore((s) => s.errors);
  const riskCounts = useAppStore(selectRiskCounts);
  const decidedCount = useAppStore(selectDecidedCount);
  const totalDecisionCount = useAppStore(selectTotalDecisionCount);

  const totalFiles = fileDiffs.length;
  const processedFiles = Object.keys(fileDecisionRecords).length;
  const successFiles = Object.values(fileDecisionRecords).filter((r) => r.success).length;

  return (
    <Box flexDirection="column">
      <StatusBar />
      <Box flexDirection="column" paddingX={1}>
        <Text bold>
          {status === "completed" ? "Final Report" : "Progress Report"}
        </Text>
        <Divider />
        <Box flexDirection="column" gap={0}>
          <Text bold>Files</Text>
          <Box gap={1}>
            <Text>Total:</Text>
            <Text bold>{totalFiles}</Text>
          </Box>
          <Box gap={1}>
            <Text>Processed:</Text>
            <ProgressBar value={processedFiles} max={totalFiles} width={20} />
          </Box>
          <Box gap={1}>
            <Text>Successful:</Text>
            <Text color="green">{successFiles}</Text>
            <Text color="gray">/ {processedFiles}</Text>
          </Box>
        </Box>
        <Divider />
        <Box flexDirection="column" gap={0}>
          <Text bold>Risk Breakdown</Text>
          <Text color="green">  Auto-safe:      {riskCounts.auto_safe}</Text>
          <Text color="yellow">  Auto-risky:     {riskCounts.auto_risky}</Text>
          <Text color="red">  Human required: {riskCounts.human_required}</Text>
          <Text color="gray">  Deleted:        {riskCounts.deleted_only}</Text>
        </Box>
        {totalDecisionCount > 0 && (
          <>
            <Divider />
            <Box gap={1}>
              <Text bold>Human Decisions:</Text>
              <Text color="green">{decidedCount}</Text>
              <Text color="gray">/ {totalDecisionCount}</Text>
            </Box>
          </>
        )}
        {errors.length > 0 && (
          <>
            <Divider />
            <Text bold color="red">Errors ({errors.length})</Text>
            {errors.slice(0, 10).map((err, i) => (
              <Text key={i} color="red">
                [{err.phase}] {err.message}
              </Text>
            ))}
          </>
        )}
      </Box>
      <GateHistory />
      <MemorySummary />
      <Divider />
      <KeyHint bindings={[{ key: "Esc", label: "Back" }]} />
    </Box>
  );
}
