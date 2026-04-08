import React, { useEffect, useState } from "react";
import { Box, useApp, useInput } from "ink";
import { ThemeProvider } from "./context/ThemeContext.js";
import { ConnectionProvider } from "./context/ConnectionContext.js";
import { KeybindingProvider } from "./context/KeybindingContext.js";
import { ScreenRouter } from "./screens/ScreenRouter.js";
import { ConnectionBanner } from "./components/ConnectionBanner.js";
import { HelpOverlay } from "./components/HelpOverlay.js";
import { ErrorBoundary } from "./components/ErrorBoundary.js";
import { useAppStore } from "./state/store.js";
import { connectWS } from "./state/ws-client.js";

interface AppProps {
  wsUrl: string;
}

export function App({ wsUrl }: AppProps) {
  const { exit } = useApp();
  const setConnectionStatus = useAppStore((s) => s.setConnectionStatus);
  const [showHelp, setShowHelp] = useState(false);

  useEffect(() => {
    const cleanup = connectWS(wsUrl, {
      onOpen: () => setConnectionStatus("connected"),
      onClose: () => setConnectionStatus("disconnected"),
    });
    return cleanup;
  }, [wsUrl, setConnectionStatus]);

  useInput((input, key) => {
    if (input === "q" && !key.ctrl) {
      exit();
    }
    if (input === "?") {
      setShowHelp((prev) => !prev);
    }
  });

  return (
    <ThemeProvider>
      <ConnectionProvider>
        <KeybindingProvider>
          <ErrorBoundary>
            <Box flexDirection="column" width="100%">
              <ConnectionBanner />
              {showHelp ? (
                <HelpOverlay visible={true} />
              ) : (
                <ScreenRouter />
              )}
            </Box>
          </ErrorBoundary>
        </KeybindingProvider>
      </ConnectionProvider>
    </ThemeProvider>
  );
}
