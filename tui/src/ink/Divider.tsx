import React from "react";
import { Text } from "ink";

interface DividerProps {
  width?: number;
  char?: string;
  color?: string;
}

export function Divider({ width = 60, char = "─", color = "gray" }: DividerProps) {
  return <Text color={color}>{char.repeat(width)}</Text>;
}
