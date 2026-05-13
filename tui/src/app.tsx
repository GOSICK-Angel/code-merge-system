import React, { useEffect, useRef, useState } from "react";
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
import type { ScreenId } from "./state/types.js";

const STATUS_SCREEN_MAP: Record<string, ScreenId> = {
  plan_reviewing: "plan_review",
  plan_revising: "plan_review",
  completed: "report",
  failed: "report",
};

interface AppProps {
  wsUrl: string;
}

export function App({ wsUrl }: AppProps) {
  const { exit } = useApp();
  const setConnectionStatus = useAppStore((s) => s.setConnectionStatus);
  const setActiveScreen = useAppStore((s) => s.setActiveScreen);
  const status = useAppStore((s) => s.status);
  const prevStatus = useRef(status);
  const [showHelp, setShowHelp] = useState(false);

  const hasDecisionRequests =
    Object.keys(useAppStore((s) => s.humanDecisionRequests)).length > 0;

  useEffect(() => {
    if (status !== prevStatus.current) {
      prevStatus.current = status;
      if (status === "awaiting_human") {
        setActiveScreen(hasDecisionRequests ? "decisions" : "plan_review");
      } else {
        const target = STATUS_SCREEN_MAP[status] ?? "dashboard";
        setActiveScreen(target);
      }
    }
  }, [status, hasDecisionRequests, setActiveScreen]);

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
