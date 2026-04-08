import React from "react";
import { Box, Text } from "ink";

interface DiffViewProps {
  diff: string;
  maxLines?: number;
}

export function DiffView({ diff, maxLines }: DiffViewProps) {
  let lines = diff.split("\n");
  if (maxLines && lines.length > maxLines) {
    lines = lines.slice(0, maxLines);
    lines.push(`... (${diff.split("\n").length - maxLines} more lines)`);
  }

  return (
    <Box flexDirection="column">
      {lines.map((line, i) => {
        let color: string | undefined;
        if (line.startsWith("+++") || line.startsWith("---")) {
          color = "white";
        } else if (line.startsWith("@@")) {
          color = "cyan";
        } else if (line.startsWith("+")) {
          color = "green";
        } else if (line.startsWith("-")) {
          color = "red";
        } else {
          color = "gray";
        }

        return (
          <Text key={i} color={color}>
            {line}
          </Text>
        );
      })}
    </Box>
  );
}
