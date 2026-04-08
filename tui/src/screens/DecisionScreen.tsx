import React, { useState } from "react";
import { Box, Text, useInput } from "ink";
import { StatusBar } from "../components/status/StatusBar.js";
import { DecisionPrompt } from "../components/decisions/DecisionPrompt.js";
import { BatchDecisionBar } from "../components/decisions/BatchDecisionBar.js";
import { Divider } from "../ink/Divider.js";
import { KeyHint } from "../ink/KeyHint.js";
import { useAppStore } from "../state/store.js";
import type { HumanDecisionRequest } from "../state/types.js";

export function DecisionScreen() {
  const requests = useAppStore((s) => s.humanDecisionRequests);
  const [selectedIdx, setSelectedIdx] = useState(0);
  const [showDetail, setShowDetail] = useState(false);

  const requestList: HumanDecisionRequest[] = Object.values(requests).sort(
    (a, b) => a.priority - b.priority
  );

  useInput((input, key) => {
    if (showDetail) {
      if (key.escape) setShowDetail(false);
      return;
    }
    if (key.upArrow) {
      setSelectedIdx((prev) => Math.max(0, prev - 1));
    } else if (key.downArrow) {
      setSelectedIdx((prev) => Math.min(requestList.length - 1, prev + 1));
    } else if (key.return && requestList[selectedIdx]) {
      setShowDetail(true);
    }
  });

  const selectedRequest = requestList[selectedIdx];

  if (showDetail && selectedRequest) {
    return (
      <Box flexDirection="column">
        <StatusBar />
        <DecisionPrompt request={selectedRequest} isActive={true} />
        <Divider />
        <KeyHint bindings={[
          { key: "↑↓", label: "Select" },
          { key: "⏎", label: "Submit" },
          { key: "Esc", label: "Back" },
        ]} />
      </Box>
    );
  }

  return (
    <Box flexDirection="column">
      <StatusBar />
      <BatchDecisionBar isActive={!showDetail} />
      <Divider />
      <Box flexDirection="column" paddingX={1}>
        <Text bold>Pending Decisions ({requestList.length})</Text>
        {requestList.map((req, i) => {
          const isSelected = i === selectedIdx;
          const decided = req.human_decision !== null;
          return (
            <Box key={req.file_path} gap={1}>
              <Text color={isSelected ? "cyan" : "gray"}>
                {isSelected ? "▸" : " "}
              </Text>
              <Text color={decided ? "gray" : isSelected ? "cyan" : "white"} dimColor={decided}>
                {req.file_path}
              </Text>
              <Text color={decided ? "green" : "yellow"}>
                {decided ? `✓ ${req.human_decision}` : "pending"}
              </Text>
              <Text color="gray">
                ({Math.round(req.analyst_confidence * 100)}%)
              </Text>
            </Box>
          );
        })}
      </Box>
      <Divider />
      <KeyHint bindings={[
        { key: "↑↓", label: "Select" },
        { key: "⏎", label: "Review" },
        { key: "Esc", label: "Back" },
      ]} />
    </Box>
  );
}
