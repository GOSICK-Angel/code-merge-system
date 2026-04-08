import React, { useDeferredValue } from "react";
import { Box, Text, useInput } from "ink";
import { useAppStore } from "../../state/store.js";
import { selectFilteredFiles } from "../../state/selectors.js";
import { FileRow } from "./FileRow.js";

interface FileListProps {
  height?: number;
  isActive?: boolean;
}

export function FileList({ height = 15, isActive = true }: FileListProps) {
  const files = useAppStore(selectFilteredFiles);
  const deferredFiles = useDeferredValue(files);
  const selectedIndex = useAppStore((s) => s.selectedFileIndex);
  const setSelectedFileIndex = useAppStore((s) => s.setSelectedFileIndex);
  const setSelectedFile = useAppStore((s) => s.setSelectedFile);
  const setActiveScreen = useAppStore((s) => s.setActiveScreen);

  useInput(
    (input, key) => {
      if (key.upArrow) {
        const next = Math.max(0, selectedIndex - 1);
        setSelectedFileIndex(next);
      } else if (key.downArrow) {
        const next = Math.min(deferredFiles.length - 1, selectedIndex + 1);
        setSelectedFileIndex(next);
      } else if (key.return) {
        const file = deferredFiles[selectedIndex];
        if (file) {
          setSelectedFile(file.file_path);
          setActiveScreen("file_detail");
        }
      }
    },
    { isActive }
  );

  if (deferredFiles.length === 0) {
    return (
      <Box paddingX={1}>
        <Text color="gray">No files to display</Text>
      </Box>
    );
  }

  const scrollOffset = Math.max(0, Math.min(selectedIndex - Math.floor(height / 2), deferredFiles.length - height));
  const visible = deferredFiles.slice(
    Math.max(0, scrollOffset),
    Math.max(0, scrollOffset) + height
  );

  return (
    <Box flexDirection="column">
      <Text bold color="white">
        Files ({deferredFiles.length})
      </Text>
      {visible.map((file, i) => (
        <FileRow
          key={file.file_path}
          file={file}
          isSelected={i + Math.max(0, scrollOffset) === selectedIndex}
        />
      ))}
      {deferredFiles.length > height && (
        <Text color="gray" dimColor>
          ↕ {Math.max(0, scrollOffset) + 1}-{Math.min(Math.max(0, scrollOffset) + height, deferredFiles.length)}/{deferredFiles.length}
        </Text>
      )}
    </Box>
  );
}
