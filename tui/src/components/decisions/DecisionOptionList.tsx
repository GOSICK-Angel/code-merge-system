import React from "react";
import { Box, Text } from "ink";
import type { DecisionOption } from "../../state/types.js";

interface DecisionOptionListProps {
  options: DecisionOption[];
  selectedIndex: number;
}

export function DecisionOptionList({ options, selectedIndex }: DecisionOptionListProps) {
  return (
    <Box flexDirection="column">
      {options.map((opt, i) => {
        const isSelected = i === selectedIndex;
        return (
          <Box key={opt.option_key} gap={1}>
            <Text color={isSelected ? "cyan" : "gray"}>
              {isSelected ? "▸" : " "}
            </Text>
            <Text bold color={isSelected ? "cyan" : "white"}>
              [{opt.option_key}]
            </Text>
            <Text color={isSelected ? "white" : "gray"}>
              {opt.description}
            </Text>
            {opt.risk_warning && (
              <Text color="yellow">⚠ {opt.risk_warning}</Text>
            )}
          </Box>
        );
      })}
    </Box>
  );
}
