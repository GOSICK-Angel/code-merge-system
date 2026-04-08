import React from "react";
import { Box, Text } from "ink";
import TextInput from "ink-text-input";
import { useAppStore } from "../state/store.js";

interface SearchBarProps {
  isActive: boolean;
}

export function SearchBar({ isActive }: SearchBarProps) {
  const searchQuery = useAppStore((s) => s.searchQuery);
  const setSearchQuery = useAppStore((s) => s.setSearchQuery);

  if (!isActive && !searchQuery) return null;

  return (
    <Box gap={1} paddingX={1}>
      <Text color="cyan">/</Text>
      {isActive ? (
        <TextInput
          value={searchQuery}
          onChange={setSearchQuery}
          placeholder="Search files..."
        />
      ) : (
        <Text color="white">{searchQuery}</Text>
      )}
      {searchQuery && (
        <Text color="gray">(Esc to clear)</Text>
      )}
    </Box>
  );
}
