import React from "react";
import { Box, Text } from "ink";
import { Divider } from "../ink/Divider.js";

interface HelpOverlayProps {
  visible: boolean;
}

const HELP_SECTIONS = [
  {
    title: "Navigation",
    bindings: [
      { key: "1-6", desc: "Switch screens (Dashboard, Plan, Decisions, File, Judge, Report)" },
      { key: "Esc", desc: "Return to Dashboard" },
      { key: "↑/↓", desc: "Navigate lists" },
      { key: "Enter", desc: "Open detail / confirm" },
      { key: "q", desc: "Quit" },
    ],
  },
  {
    title: "Search",
    bindings: [
      { key: "/", desc: "Start search (type to filter files)" },
      { key: "Esc", desc: "Clear search" },
    ],
  },
  {
    title: "Decisions",
    bindings: [
      { key: "Shift+A", desc: "Accept all analyst recommendations" },
      { key: "a", desc: "Approve plan (on Plan Review screen)" },
      { key: "r", desc: "Reject plan (on Plan Review screen)" },
    ],
  },
];

export function HelpOverlay({ visible }: HelpOverlayProps) {
  if (!visible) return null;

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor="cyan"
      paddingX={2}
      paddingY={1}
    >
      <Text bold color="cyan">
        Help — Keyboard Shortcuts
      </Text>
      <Divider width={50} color="cyan" />
      {HELP_SECTIONS.map((section) => (
        <Box key={section.title} flexDirection="column" marginTop={1}>
          <Text bold>{section.title}</Text>
          {section.bindings.map((b) => (
            <Box key={b.key} gap={1}>
              <Text color="cyan" bold>
                {b.key.padEnd(10)}
              </Text>
              <Text color="gray">{b.desc}</Text>
            </Box>
          ))}
        </Box>
      ))}
      <Box marginTop={1}>
        <Text color="gray">Press ? again to close</Text>
      </Box>
    </Box>
  );
}
