import { useEffect, useRef } from "react";
import { createWsClient, type WsClient } from "./client";
import { useRunStore } from "../store/runStore";

/**
 * Shared WS client hook.
 *
 * Hosts the single ``createWsClient`` instance and pipes inbound frames
 * into ``runStore``. Views call this from their top-level container; any
 * child component that needs to send a frame reads ``clientRef.current``
 * out of the returned ref.
 *
 * Mount order matters — call exactly once at the App root so the
 * connection persists across L1↔L3 view switches (a per-view client
 * would tear down + reconnect on every transition, wiping the activity
 * replay buffer).
 *
 * Stability invariant: this hook's ``useEffect`` MUST run exactly once
 * per mount (deps ``[]``). Earlier revisions listed each zustand setter
 * in the deps array, which made the client tear-down + reconnect any
 * time a selector identity drifted — losing in-flight outbound frames
 * during the reconnect backoff (``web/src/ws/client.ts`` only queues
 * when the same socket is mid-handshake, not across rebuilds). To keep
 * effect-deps empty we read the latest store via
 * ``useRunStore.getState()`` inside the handler closure.
 */
export function useWsClient(): React.MutableRefObject<WsClient | null> {
  const clientRef = useRef<WsClient | null>(null);

  useEffect(() => {
    const client = createWsClient({
      onState: (s) => useRunStore.getState().setConn(s),
      onMessage: (msg) => {
        const store = useRunStore.getState();
        switch (msg.type) {
          case "state_snapshot":
          case "state_patch":
            store.applySnapshot(msg.payload);
            break;
          case "agent_activity":
            store.appendActivity(msg.payload);
            break;
          case "agent_activity_replay":
            store.replaceActivity(msg.payload.events);
            break;
          case "cancel_error":
            store.setCancelError(msg.payload);
            break;
          case "setup_snapshot":
            store.applySetupSnapshot(msg.payload);
            break;
          case "setup_ready":
            store.applySetupReady(msg.payload);
            break;
          case "setup_error":
            store.applySetupError(msg.payload);
            break;
          case "setup_test_result":
            store.applySetupTestResult(msg.payload);
            break;
          case "command_error":
            // Surface as a setup_error when reason matches, otherwise
            // log — there is no general command-error toast yet.
            if (msg.payload.reason === "setup_required") {
              store.applySetupError({
                reason: msg.payload.reason,
                details: `command ${msg.payload.command} blocked`,
              });
            } else {
              console.warn("Command error:", msg.payload);
            }
            break;
        }
      },
      onDrop: (event) => useRunStore.getState().recordOutboundDrop(event),
      onFlush: (event) => useRunStore.getState().recordOutboundFlush(event),
    });
    clientRef.current = client;
    return () => {
      client.close();
      clientRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return clientRef;
}
