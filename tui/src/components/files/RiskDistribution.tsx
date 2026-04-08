import React from "react";
import { Box, Text } from "ink";
import { useAppStore } from "../../state/store.js";
import { selectRiskCounts } from "../../state/selectors.js";

export function RiskDistribution() {
  const counts = useAppStore(selectRiskCounts);

  const items = [
    { label: "Safe", count: counts.auto_safe, color: "green" },
    { label: "Risky", count: counts.auto_risky, color: "yellow" },
    { label: "Human", count: counts.human_required, color: "red" },
    { label: "Deleted", count: counts.deleted_only, color: "gray" },
  ];

  return (
    <Box flexDirection="column" paddingX={1}>
      <Text bold color="white">
        Risk Distribution
      </Text>
      {items.map((item) => (
        <Box key={item.label} gap={1}>
          <Text color={item.color}>
            {item.label}:
          </Text>
          <Text color="white">{item.count}</Text>
        </Box>
      ))}
    </Box>
  );
}
