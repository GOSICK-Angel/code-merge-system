import React from "react";
import { Box, Text } from "ink";
import { useAppStore } from "../../state/store.js";
import { useElapsed } from "../../hooks/use-elapsed.js";
import { useConnectionStatus } from "../../hooks/use-connection.js";
import { formatDuration } from "../../utils/format.js";
import { Badge } from "../../ink/Badge.js";

export function StatusBar() {
  const status = useAppStore((s) => s.status);
  const runId = useAppStore((s) => s.runId);
  const createdAt = useAppStore((s) => s.createdAt);
  const connectionStatus = useConnectionStatus();
  const elapsed = useElapsed(createdAt);

  const statusVariant = (() => {
    switch (status) {
      case "completed":
        return "success" as const;
      case "failed":
        return "danger" as const;
      case "awaiting_human":
        return "warning" as const;
      default:
        return "info" as const;
    }
  })();

  const connColor =
    connectionStatus === "connected" ? "green" :
    connectionStatus === "connecting" ? "yellow" : "red";

  return (
    <Box
      flexDirection="row"
      justifyContent="space-between"
      borderStyle="single"
      borderColor="gray"
      paddingX={1}
    >
      <Box gap={1}>
        <Text bold color="white">
          CodeMerge
        </Text>
        <Text color="gray">|</Text>
        <Text color="gray">{runId ? runId.slice(0, 8) : "---"}</Text>
      </Box>
      <Box gap={1}>
        <Badge label={status.replace(/_/g, " ").toUpperCase()} variant={statusVariant} />
        <Text color="gray">|</Text>
        <Text color="white">{formatDuration(elapsed)}</Text>
        <Text color="gray">|</Text>
        <Text color={connColor}>●</Text>
      </Box>
    </Box>
  );
}
