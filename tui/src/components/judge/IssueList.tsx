import React from "react";
import { Box, Text } from "ink";
import type { JudgeIssue } from "../../state/types.js";

interface IssueListProps {
  issues: JudgeIssue[];
  maxDisplay?: number;
}

export function IssueList({ issues, maxDisplay = 20 }: IssueListProps) {
  if (issues.length === 0) {
    return <Text color="gray">No issues</Text>;
  }

  const displayed = issues.slice(0, maxDisplay);

  return (
    <Box flexDirection="column">
      <Text bold>Issues ({issues.length})</Text>
      {displayed.map((issue, i) => {
        const sevColor =
          issue.severity === "critical" ? "red" :
          issue.severity === "major" ? "yellow" : "gray";
        return (
          <Box key={i} gap={1}>
            <Text color={sevColor}>[{issue.severity}]</Text>
            <Text color="gray">{issue.file_path}</Text>
            <Text>{issue.description}</Text>
          </Box>
        );
      })}
      {issues.length > maxDisplay && (
        <Text color="gray">... and {issues.length - maxDisplay} more</Text>
      )}
    </Box>
  );
}
