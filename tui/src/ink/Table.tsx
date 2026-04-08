import React from "react";
import { Box, Text } from "ink";
import { padRight } from "../utils/format.js";

export interface Column {
  key: string;
  header: string;
  width: number;
  color?: string;
}

export interface Row {
  [key: string]: string;
}

interface TableProps {
  columns: Column[];
  data: Row[];
  selectedIndex?: number;
}

export function Table({ columns, data, selectedIndex }: TableProps) {
  return (
    <Box flexDirection="column">
      <Box flexDirection="row">
        {columns.map((col) => (
          <Text key={col.key} bold color="white">
            {padRight(col.header, col.width)}
          </Text>
        ))}
      </Box>
      <Box flexDirection="row">
        {columns.map((col) => (
          <Text key={col.key} color="gray">
            {padRight("─".repeat(col.width - 1), col.width)}
          </Text>
        ))}
      </Box>
      {data.map((row, i) => {
        const isSelected = i === selectedIndex;
        return (
          <Box key={i} flexDirection="row">
            {isSelected && <Text color="cyan">{"▸ "}</Text>}
            {!isSelected && <Text>{"  "}</Text>}
            {columns.map((col) => (
              <Text key={col.key} color={isSelected ? "cyan" : col.color}>
                {padRight(row[col.key] ?? "", col.width)}
              </Text>
            ))}
          </Box>
        );
      })}
    </Box>
  );
}
