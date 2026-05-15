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
 */
export function useWsClient(): React.MutableRefObject<WsClient | null> {
  const setConn = useRunStore((s) => s.setConn);
  const applySnapshot = useRunStore((s) => s.applySnapshot);
  const appendActivity = useRunStore((s) => s.appendActivity);
  const replaceActivity = useRunStore((s) => s.replaceActivity);
  const setCancelError = useRunStore((s) => s.setCancelError);

  const clientRef = useRef<WsClient | null>(null);

  useEffect(() => {
    const client = createWsClient({
      onState: setConn,
      onMessage: (msg) => {
        switch (msg.type) {
          case "state_snapshot":
          case "state_patch":
            applySnapshot(msg.payload);
            break;
          case "agent_activity":
            appendActivity(msg.payload);
            break;
          case "agent_activity_replay":
            replaceActivity(msg.payload.events);
            break;
          case "cancel_error":
            setCancelError(msg.payload);
            break;
        }
      },
    });
    clientRef.current = client;
    return () => {
      client.close();
      clientRef.current = null;
    };
  }, [setConn, applySnapshot, appendActivity, replaceActivity, setCancelError]);

  return clientRef;
}
