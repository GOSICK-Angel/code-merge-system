import React, { createContext, useContext, useCallback } from "react";
import { useAppStore } from "../state/store.js";
import { sendCommand } from "../state/ws-client.js";

interface ConnectionContextValue {
  status: "connecting" | "connected" | "disconnected";
  send: (command: { type: string; payload?: unknown }) => void;
}

const ConnectionCtx = createContext<ConnectionContextValue>({
  status: "connecting",
  send: () => {},
});

export function ConnectionProvider({ children }: { children: React.ReactNode }) {
  const status = useAppStore((s) => s.connectionStatus);

  const send = useCallback((command: { type: string; payload?: unknown }) => {
    sendCommand(command);
  }, []);

  return (
    <ConnectionCtx.Provider value={{ status, send }}>
      {children}
    </ConnectionCtx.Provider>
  );
}

export function useConnection(): ConnectionContextValue {
  return useContext(ConnectionCtx);
}
