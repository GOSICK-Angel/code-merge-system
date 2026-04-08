import WebSocket from "ws";
import { useAppStore } from "./store.js";

interface WSCallbacks {
  onOpen?: () => void;
  onClose?: () => void;
}

let ws: WebSocket | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

export function connectWS(url: string, callbacks: WSCallbacks): () => void {
  const store = useAppStore.getState;

  function connect() {
    if (ws && ws.readyState === WebSocket.OPEN) return;

    ws = new WebSocket(url);

    ws.on("open", () => {
      callbacks.onOpen?.();
    });

    ws.on("message", (raw: WebSocket.Data) => {
      try {
        const msg = JSON.parse(raw.toString());
        handleMessage(msg);
      } catch {
        // ignore unparseable messages
      }
    });

    ws.on("close", () => {
      callbacks.onClose?.();
      scheduleReconnect();
    });

    ws.on("error", () => {
      // error events are followed by close
    });
  }

  function scheduleReconnect() {
    if (reconnectTimer) clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(() => {
      store().setConnectionStatus("connecting");
      connect();
    }, 3000);
  }

  function handleMessage(msg: { type: string; payload?: unknown }) {
    const { applySnapshot, applyPatch, setAgentActivity } = useAppStore.getState();
    switch (msg.type) {
      case "state_snapshot":
        applySnapshot(msg.payload as Record<string, unknown>);
        break;
      case "state_patch":
        applyPatch(msg.payload as Record<string, unknown>);
        break;
      case "agent_activity":
        setAgentActivity(msg.payload as { agent: string; action: string });
        break;
      case "phase_started":
        applyPatch({
          currentPhase: (msg.payload as { phase: string }).phase as never,
        });
        break;
      default:
        break;
    }
  }

  connect();

  return () => {
    if (reconnectTimer) clearTimeout(reconnectTimer);
    if (ws) {
      ws.removeAllListeners();
      ws.close();
      ws = null;
    }
  };
}

export function sendCommand(command: { type: string; payload?: unknown }) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(command));
  }
}
