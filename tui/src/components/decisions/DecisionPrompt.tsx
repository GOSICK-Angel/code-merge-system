import React, { useState } from "react";
import { Box, Text, useInput } from "ink";
import type { HumanDecisionRequest } from "../../state/types.js";
import { DecisionOptionList } from "./DecisionOptionList.js";
import { Badge } from "../../ink/Badge.js";
import { useConnection } from "../../context/ConnectionContext.js";

interface DecisionPromptProps {
  request: HumanDecisionRequest;
  isActive?: boolean;
}

export function DecisionPrompt({ request, isActive = true }: DecisionPromptProps) {
  const [selectedOption, setSelectedOption] = useState(0);
  const { send } = useConnection();

  useInput(
    (input, key) => {
      if (key.upArrow) {
        setSelectedOption((prev) => Math.max(0, prev - 1));
      } else if (key.downArrow) {
        setSelectedOption((prev) =>
          Math.min(request.options.length - 1, prev + 1)
        );
      } else if (key.return) {
        const option = request.options[selectedOption];
        if (option) {
          send({
            type: "submit_decision",
            payload: {
              filePath: request.file_path,
              decision: option.decision,
            },
          });
        }
      }
    },
    { isActive }
  );

  const confPct = Math.round(request.analyst_confidence * 100);
  const confColor = confPct >= 80 ? "green" : confPct >= 50 ? "yellow" : "red";

  return (
    <Box flexDirection="column" paddingX={1} gap={0}>
      <Box gap={1}>
        <Text bold>{request.file_path}</Text>
        {request.human_decision && (
          <Badge label={`decided: ${request.human_decision}`} variant="success" />
        )}
      </Box>
      <Text color="gray">{request.context_summary}</Text>
      <Box gap={1}>
        <Text>Upstream:</Text>
        <Text color="gray">{request.upstream_change_summary}</Text>
      </Box>
      <Box gap={1}>
        <Text>Fork:</Text>
        <Text color="gray">{request.fork_change_summary}</Text>
      </Box>
      <Box gap={1}>
        <Text>Recommendation:</Text>
        <Text bold>{String(request.analyst_recommendation)}</Text>
        <Text color={confColor}>({confPct}%)</Text>
      </Box>
      <Text color="gray">{request.analyst_rationale}</Text>
      {request.conflict_points.length > 0 && (
        <Box flexDirection="column">
          <Text bold>Conflicts:</Text>
          {request.conflict_points.map((cp, i) => (
            <Text key={i} color="yellow">
              • [{cp.severity}] {cp.description} {cp.line_range}
            </Text>
          ))}
        </Box>
      )}
      {!request.human_decision && (
        <>
          <Text bold>Choose action:</Text>
          <DecisionOptionList
            options={request.options}
            selectedIndex={selectedOption}
          />
        </>
      )}
    </Box>
  );
}
