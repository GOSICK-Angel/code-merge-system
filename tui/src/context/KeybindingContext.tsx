import React, { createContext, useContext, useCallback, useRef } from "react";
import { useInput } from "ink";
import { useAppStore } from "../state/store.js";
import type { ScreenId } from "../state/types.js";

type KeyHandler = (input: string, key: Record<string, boolean>) => boolean;

interface KeybindingContextValue {
  register: (id: string, handler: KeyHandler) => void;
  unregister: (id: string) => void;
}

const KeybindingCtx = createContext<KeybindingContextValue>({
  register: () => {},
  unregister: () => {},
});

const SCREEN_KEYS: Record<string, ScreenId> = {
  "1": "dashboard",
  "2": "plan_review",
  "3": "decisions",
  "4": "file_detail",
  "5": "judge",
  "6": "report",
};

export function KeybindingProvider({ children }: { children: React.ReactNode }) {
  const handlersRef = useRef<Map<string, KeyHandler>>(new Map());
  const setActiveScreen = useAppStore((s) => s.setActiveScreen);

  const register = useCallback((id: string, handler: KeyHandler) => {
    handlersRef.current.set(id, handler);
  }, []);

  const unregister = useCallback((id: string) => {
    handlersRef.current.delete(id);
  }, []);

  useInput((input, key) => {
    // Let registered handlers consume the input first
    for (const handler of handlersRef.current.values()) {
      if (handler(input, key as Record<string, boolean>)) return;
    }

    // Global screen switching
    if (input in SCREEN_KEYS && !key.ctrl && !key.meta) {
      setActiveScreen(SCREEN_KEYS[input]!);
      return;
    }

    // Escape returns to dashboard
    if (key.escape) {
      setActiveScreen("dashboard");
    }
  });

  return (
    <KeybindingCtx.Provider value={{ register, unregister }}>
      {children}
    </KeybindingCtx.Provider>
  );
}

export function useKeybindingContext(): KeybindingContextValue {
  return useContext(KeybindingCtx);
}
