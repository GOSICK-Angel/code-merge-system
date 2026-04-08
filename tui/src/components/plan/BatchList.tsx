import React from "react";
import { Box, Text } from "ink";
import { useAppStore } from "../../state/store.js";
import { Badge, riskToBadgeVariant } from "../../ink/Badge.js";

export function BatchList() {
  const plan = useAppStore((s) => s.mergePlan);

  if (!plan || plan.phases.length === 0) {
    return (
      <Box paddingX={1}>
        <Text color="gray">No batches</Text>
      </Box>
    );
  }

  return (
    <Box flexDirection="column" paddingX={1}>
      <Text bold>Batches ({plan.phases.length})</Text>
      {plan.phases.map((batch) => (
        <Box key={batch.batch_id} gap={1}>
          <Text color="gray">{batch.batch_id.slice(0, 6)}</Text>
          <Badge
            label={batch.risk_level.replace(/_/g, " ")}
            variant={riskToBadgeVariant(batch.risk_level)}
          />
          <Text>{batch.file_paths.length} files</Text>
          {batch.layer_id !== null && (
            <Text color="gray">L{batch.layer_id}</Text>
          )}
        </Box>
      ))}
    </Box>
  );
}
