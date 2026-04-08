import { useAppStore } from "../state/store.js";
import type { AppStore } from "../state/store.js";

export function useMergeState<T>(selector: (s: AppStore) => T): T {
  return useAppStore(selector);
}
