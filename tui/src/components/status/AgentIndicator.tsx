import React from "react";
import { Box, Text } from "ink";
import { useAppStore } from "../../state/store.js";
import { Spinner } from "../../ink/Spinner.js";

export function AgentIndicator() {
  const activity = useAppStore((s) => s.agentActivity);
  const status = useAppStore((s) => s.status);

  if (status === "completed" || status === "failed") {
    return (
      <Box paddingX={1}>
        <Text color={status === "completed" ? "green" : "red"} bold>
          {status === "completed" ? "✓ Complete" : "✗ Failed"}
        </Text>
      </Box>
    );
  }

  if (!activity) {
    return (
      <Box paddingX={1}>
        <Text color="gray">Idle</Text>
      </Box>
    );
  }

  return (
    <Box paddingX={1} flexDirection="column">
      <Box gap={1}>
        <Spinner />
        <Text bold color="cyan">
          {activity.agent}
        </Text>
      </Box>
      <Text color="gray">{activity.action}</Text>
    </Box>
  );
}
