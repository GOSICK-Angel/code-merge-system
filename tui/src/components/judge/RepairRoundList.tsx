import React from "react";
import { Box, Text } from "ink";
import type { JudgeVerdict } from "../../state/types.js";

interface RepairRoundListProps {
  verdicts: { round: number; verdict: string; issues_count: number; veto: boolean }[];
}

export function RepairRoundList({ verdicts }: RepairRoundListProps) {
  if (verdicts.length === 0) {
    return <Text color="gray">No review rounds yet</Text>;
  }

  return (
    <Box flexDirection="column">
      <Text bold>Review Rounds</Text>
      {verdicts.map((v) => (
        <Box key={v.round} gap={1}>
          <Text color="gray">R{v.round}</Text>
          <Text
            color={
              v.verdict === "pass" ? "green" :
              v.verdict === "fail" ? "red" : "yellow"
            }
            bold
          >
            {v.verdict.toUpperCase()}
          </Text>
          <Text color="gray">{v.issues_count} issues</Text>
          {v.veto && <Text color="red">VETO</Text>}
        </Box>
      ))}
    </Box>
  );
}
