import React from "react";
import { Box, Text } from "ink";
import type { GateResult } from "../../state/types.js";

interface GateResultRowProps {
  result: GateResult;
}

export function GateResultRow({ result }: GateResultRowProps) {
  return (
    <Box gap={1}>
      <Text color={result.passed ? "green" : "red"}>
        {result.passed ? "✓" : "✗"}
      </Text>
      <Text bold>{result.gate_name}</Text>
      {!result.passed && result.output && (
        <Text color="gray" dimColor>
          {result.output.slice(0, 80)}
        </Text>
      )}
    </Box>
  );
}
