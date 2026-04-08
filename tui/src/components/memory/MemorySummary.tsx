import React from "react";
import { Box, Text } from "ink";
import { useAppStore } from "../../state/store.js";

export function MemorySummary() {
  const memory = useAppStore((s) => s.memory);

  const phaseCount = Object.keys(memory.phase_summaries).length;
  const entryCount = memory.entries.length;

  if (phaseCount === 0 && entryCount === 0) {
    return null;
  }

  return (
    <Box flexDirection="column" paddingX={1}>
      <Text bold>Memory</Text>
      <Text color="gray">
        {phaseCount} phase summaries, {entryCount} entries
      </Text>
      {Object.entries(memory.phase_summaries).map(([phase, summary]) => (
        <Box key={phase} gap={1}>
          <Text color="cyan">{phase}:</Text>
          <Text color="gray">{summary.slice(0, 60)}</Text>
        </Box>
      ))}
    </Box>
  );
}
