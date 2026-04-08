import React from "react";
import { Box, Text } from "ink";
import { useAppStore } from "../../state/store.js";
import { GateResultRow } from "./GateResultRow.js";

export function GateHistory() {
  const gateHistory = useAppStore((s) => s.gateHistory);

  if (gateHistory.length === 0) {
    return (
      <Box paddingX={1}>
        <Text color="gray">No gate results</Text>
      </Box>
    );
  }

  return (
    <Box flexDirection="column" paddingX={1}>
      <Text bold>Gate History</Text>
      {gateHistory.map((entry, i) => (
        <Box key={i} flexDirection="column">
          <Box gap={1}>
            <Text color="gray">{entry.phase}</Text>
            <Text color={entry.all_passed ? "green" : "red"}>
              {entry.all_passed ? "ALL PASS" : "FAILED"}
            </Text>
          </Box>
          {entry.results.map((r, j) => (
            <GateResultRow key={j} result={r} />
          ))}
        </Box>
      ))}
    </Box>
  );
}
