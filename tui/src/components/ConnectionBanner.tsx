import React from "react";
import { Box, Text } from "ink";
import { useConnectionStatus } from "../hooks/use-connection.js";
import { Spinner } from "../ink/Spinner.js";

export function ConnectionBanner() {
  const status = useConnectionStatus();

  if (status === "connected") return null;

  if (status === "connecting") {
    return (
      <Box
        borderStyle="single"
        borderColor="yellow"
        paddingX={1}
        justifyContent="center"
      >
        <Spinner label="Connecting to merge backend..." />
      </Box>
    );
  }

  return (
    <Box
      borderStyle="single"
      borderColor="red"
      paddingX={1}
      justifyContent="center"
    >
      <Text color="red" bold>
        Disconnected from merge backend. Reconnecting...
      </Text>
    </Box>
  );
}
