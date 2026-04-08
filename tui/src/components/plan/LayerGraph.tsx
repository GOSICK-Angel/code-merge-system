import React from "react";
import { Box, Text } from "ink";
import { useAppStore } from "../../state/store.js";

export function LayerGraph() {
  const plan = useAppStore((s) => s.mergePlan);

  if (!plan || plan.layers.length === 0) {
    return (
      <Box paddingX={1}>
        <Text color="gray">No layers</Text>
      </Box>
    );
  }

  return (
    <Box flexDirection="column" paddingX={1}>
      <Text bold>Layer Dependencies</Text>
      {plan.layers.map((layer) => {
        const deps = layer.depends_on.length > 0
          ? ` ← [${layer.depends_on.join(", ")}]`
          : "";
        return (
          <Box key={layer.layer_id} gap={1}>
            <Text color="cyan">[{layer.layer_id}]</Text>
            <Text bold>{layer.name}</Text>
            <Text color="gray">{deps}</Text>
          </Box>
        );
      })}
    </Box>
  );
}
