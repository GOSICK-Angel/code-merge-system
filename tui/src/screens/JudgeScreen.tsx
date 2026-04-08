import React from "react";
import { Box, Text } from "ink";
import { StatusBar } from "../components/status/StatusBar.js";
import { VerdictBadge } from "../components/judge/VerdictBadge.js";
import { RepairRoundList } from "../components/judge/RepairRoundList.js";
import { IssueList } from "../components/judge/IssueList.js";
import { Divider } from "../ink/Divider.js";
import { KeyHint } from "../ink/KeyHint.js";
import { useAppStore } from "../state/store.js";

export function JudgeScreen() {
  const verdict = useAppStore((s) => s.judgeVerdict);
  const repairRounds = useAppStore((s) => s.judgeRepairRounds);
  const verdictLog = useAppStore((s) =>
    s.messages
      .filter((m) => m.type === "state_transition")
      .filter((m) => m.to === "judge_reviewing" || m.from === "judge_reviewing")
  );

  return (
    <Box flexDirection="column">
      <StatusBar />
      <Box flexDirection="column" paddingX={1}>
        <Text bold>Judge Review</Text>
        {verdict ? (
          <Box flexDirection="column" gap={0}>
            <Box gap={1}>
              <Text>Verdict:</Text>
              <VerdictBadge verdict={verdict.verdict} />
            </Box>
            <Text color="gray">{verdict.summary}</Text>
            {verdict.veto_triggered && (
              <Box gap={1}>
                <Text color="red" bold>VETO:</Text>
                <Text color="red">{verdict.veto_reason}</Text>
              </Box>
            )}
            <Box gap={1}>
              <Text>Repair rounds:</Text>
              <Text bold>{repairRounds}</Text>
            </Box>
            <Divider />
            <IssueList issues={verdict.issues} />
            {verdict.repair_instructions.length > 0 && (
              <Box flexDirection="column">
                <Text bold>Repair Instructions</Text>
                {verdict.repair_instructions.map((r, i) => (
                  <Box key={i} gap={1}>
                    <Text color={r.is_repairable ? "cyan" : "gray"}>
                      {r.is_repairable ? "🔧" : "📋"}
                    </Text>
                    <Text>{r.instruction}</Text>
                  </Box>
                ))}
              </Box>
            )}
          </Box>
        ) : (
          <Text color="gray">No judge verdict yet</Text>
        )}
      </Box>
      <Divider />
      <KeyHint bindings={[{ key: "Esc", label: "Back" }]} />
    </Box>
  );
}
