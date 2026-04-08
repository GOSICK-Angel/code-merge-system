import React from "react";
import { Box, Text } from "ink";

interface Binding {
  key: string;
  label: string;
}

interface KeyHintProps {
  bindings: Binding[];
}

export function KeyHint({ bindings }: KeyHintProps) {
  return (
    <Box flexDirection="row" gap={1}>
      {bindings.map((b) => (
        <Box key={b.key}>
          <Text color="cyan" bold>
            [{b.key}]
          </Text>
          <Text color="gray">{b.label}</Text>
        </Box>
      ))}
    </Box>
  );
}
