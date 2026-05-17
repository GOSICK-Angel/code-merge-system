import { useCallback } from "react";
import type { MutableRefObject } from "react";
import { useRunStore } from "../store/runStore";
import type { SetupPayload } from "../types/state";
import type { WsClient } from "./client";

/**
 * Setup view's WS contract surface.
 *
 * Reads ``setupContext`` / ``setupStatus`` / ``setupError`` /
 * ``setupReady`` from the run store (populated by ``useWsClient`` when
 * the bridge emits ``setup_snapshot`` / ``setup_ready`` /
 * ``setup_error``), and exposes ``submit`` / ``refresh`` callbacks that
 * forward the corresponding ``setup.submit`` / ``setup.detect`` frames
 * over the shared WebSocket.
 *
 * ``submit`` optimistically flips the store into ``"submitting"`` so the
 * UI can disable the button immediately; the terminal state arrives
 * asynchronously via ``setup_ready`` (success) or ``setup_error``
 * (validation / I/O failure on the server). The view does **not** flip
 * itself back from ``"submitting"`` on a transient ws drop — the
 * reconnect logic re-fires ``setup.detect`` and the server re-sends
 * ``setup_snapshot`` which resets the form state cleanly.
 */
export interface UseSetupReturn {
  context: ReturnType<typeof useRunStore.getState>["setupContext"];
  status: ReturnType<typeof useRunStore.getState>["setupStatus"];
  error: ReturnType<typeof useRunStore.getState>["setupError"];
  ready: ReturnType<typeof useRunStore.getState>["setupReady"];
  submit: (payload: SetupPayload) => void;
  refresh: () => void;
}

export function useSetup(
  clientRef: MutableRefObject<WsClient | null>,
): UseSetupReturn {
  const context = useRunStore((s) => s.setupContext);
  const status = useRunStore((s) => s.setupStatus);
  const error = useRunStore((s) => s.setupError);
  const ready = useRunStore((s) => s.setupReady);
  const markSetupSubmitting = useRunStore((s) => s.markSetupSubmitting);

  const submit = useCallback(
    (payload: SetupPayload) => {
      const client = clientRef.current;
      if (client === null) {
        console.warn("useSetup.submit: ws client not ready");
        return;
      }
      markSetupSubmitting();
      client.send({ type: "setup.submit", payload });
    },
    [clientRef, markSetupSubmitting],
  );

  const refresh = useCallback(() => {
    const client = clientRef.current;
    if (client === null) return;
    client.send({ type: "setup.detect", payload: {} });
  }, [clientRef]);

  return { context, status, error, ready, submit, refresh };
}
