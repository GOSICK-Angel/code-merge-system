import type { InboundMessage, OutboundMessage } from "./messages";

export function resolveWsUrl(): string {
  const params = new URLSearchParams(window.location.search);
  const wsPort = params.get("ws") ?? "8765";
  return `ws://${window.location.hostname}:${wsPort}`;
}

export type ConnState = "connecting" | "open" | "closed";

export interface WsClient {
  send(msg: OutboundMessage): void;
  close(): void;
}

export interface WsClientHandlers {
  onState: (state: ConnState) => void;
  onMessage: (msg: InboundMessage) => void;
}

const INITIAL_BACKOFF_MS = 500;
const MAX_BACKOFF_MS = 30_000;

export function createWsClient(handlers: WsClientHandlers): WsClient {
  let ws: WebSocket | null = null;
  let backoff = INITIAL_BACKOFF_MS;
  let manuallyClosed = false;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  function open(): void {
    handlers.onState("connecting");
    ws = new WebSocket(resolveWsUrl());

    ws.onopen = () => {
      backoff = INITIAL_BACKOFF_MS;
      handlers.onState("open");
    };
    ws.onclose = () => {
      handlers.onState("closed");
      if (manuallyClosed) return;
      reconnectTimer = setTimeout(open, backoff);
      backoff = Math.min(backoff * 2, MAX_BACKOFF_MS);
    };
    ws.onerror = (e) => {
      console.error("WS error:", e);
    };
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data) as InboundMessage;
        handlers.onMessage(msg);
      } catch (err) {
        console.error("Failed to parse WS message:", err);
      }
    };
  }

  open();

  return {
    send(msg) {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(msg));
      } else {
        console.warn("WS not open; dropping message:", msg.type);
      }
    },
    close() {
      manuallyClosed = true;
      if (reconnectTimer !== null) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      if (
        ws &&
        (ws.readyState === WebSocket.OPEN ||
          ws.readyState === WebSocket.CONNECTING)
      ) {
        ws.close();
      }
    },
  };
}
