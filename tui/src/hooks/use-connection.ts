import { useAppStore } from "../state/store.js";

export function useConnectionStatus() {
  return useAppStore((s) => s.connectionStatus);
}
