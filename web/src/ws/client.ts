import type { InboundMessage, OutboundMessage } from "./messages";

export function resolveWsUrl(): string {
  const params = new URLSearchParams(window.location.search);
  const wsPort = params.get("ws") ?? "8765";
  return `ws://${window.location.hostname}:${wsPort}`;
}

export type ConnState = "connecting" | "open" | "closed";

export type DropReason = "not_open" | "queue_overflow" | "send_failed";

export interface OutboundDropEvent {
  reason: DropReason;
  type: OutboundMessage["type"];
  queuedCount: number;
  at: number;
}

export interface OutboundFlushEvent {
  flushedCount: number;
  remainingCount: number;
  at: number;
}

export interface WsClient {
  send(msg: OutboundMessage): void;
  close(): void;
  pendingCount(): number;
}

export interface WsClientHandlers {
  onState: (state: ConnState) => void;
  onMessage: (msg: InboundMessage) => void;
  onDrop?: (event: OutboundDropEvent) => void;
  onFlush?: (event: OutboundFlushEvent) => void;
}

const INITIAL_BACKOFF_MS = 500;
const MAX_BACKOFF_MS = 30_000;
const MAX_QUEUE_LEN = 32;

/**
 * Plan v1.1 §P1-3 wire safety — outbound buffer.
 *
 * Previously ``send`` dropped messages with only a ``console.warn``
 * whenever the socket was not OPEN (CONNECTING during initial dial,
 * CLOSED during the reconnect backoff). The Plan Review APPROVE ALL
 * flow lost frames silently when the user clicked during a reconnect
 * window, leaving the orchestrator parked at ``AWAITING_HUMAN``.
 *
 * Now we queue outbound frames and flush on ``onopen``. The queue is
 * deduped by ``msg.type`` so a re-click only replaces the prior pending
 * frame instead of stacking duplicates. ``onDrop`` surfaces overflow /
 * post-close drops to the UI layer so the user gets a visible signal
 * (vs. the prior silent-warn behaviour).
 */
export function createWsClient(handlers: WsClientHandlers): WsClient {
  let ws: WebSocket | null = null;
  let backoff = INITIAL_BACKOFF_MS;
  let manuallyClosed = false;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  const queue: OutboundMessage[] = [];

  function enqueue(msg: OutboundMessage): void {
    // Dedup by type — last writer wins. The outbound contracts are
    // idempotent at this granularity: ``submit_plan_review`` etc. carry
    // the full decision payload, so replacing an older queued frame
    // with the latest click is the intent the user expects.
    const idx = queue.findIndex((m) => m.type === msg.type);
    if (idx >= 0) {
      queue.splice(idx, 1);
    }
    queue.push(msg);
    if (queue.length > MAX_QUEUE_LEN) {
      const dropped = queue.shift();
      if (dropped) {
        handlers.onDrop?.({
          reason: "queue_overflow",
          type: dropped.type,
          queuedCount: queue.length,
          at: Date.now(),
        });
      }
    }
  }

  function flushQueue(): void {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    if (queue.length === 0) return;
    let flushed = 0;
    while (queue.length > 0) {
      const msg = queue[0];
      try {
        ws.send(JSON.stringify(msg));
        queue.shift();
        flushed += 1;
      } catch (err) {
        // Socket flipped out from under us mid-flush (rare). Leave the
        // frame at the head of the queue so the next ``onopen`` retries
        // it, and report the drop so the UI knows we couldn't deliver
        // synchronously.
        console.error("WS flush failed:", err);
        handlers.onDrop?.({
          reason: "send_failed",
          type: msg.type,
          queuedCount: queue.length,
          at: Date.now(),
        });
        break;
      }
    }
    if (flushed > 0) {
      handlers.onFlush?.({
        flushedCount: flushed,
        remainingCount: queue.length,
        at: Date.now(),
      });
    }
  }

  function open(): void {
    handlers.onState("connecting");
    ws = new WebSocket(resolveWsUrl());

    ws.onopen = () => {
      backoff = INITIAL_BACKOFF_MS;
      handlers.onState("open");
      flushQueue();
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
        try {
          ws.send(JSON.stringify(msg));
          return;
        } catch (err) {
          console.error("WS send failed, queuing:", err);
          enqueue(msg);
          handlers.onDrop?.({
            reason: "send_failed",
            type: msg.type,
            queuedCount: queue.length,
            at: Date.now(),
          });
          return;
        }
      }
      if (manuallyClosed) {
        // Sending after close() is a programming error — the UI should
        // never resurrect a disposed client. Surface the drop loudly
        // (no queue) so the bug is visible.
        handlers.onDrop?.({
          reason: "not_open",
          type: msg.type,
          queuedCount: queue.length,
          at: Date.now(),
        });
        console.warn("WS already closed; dropping message:", msg.type);
        return;
      }
      enqueue(msg);
      handlers.onDrop?.({
        reason: "not_open",
        type: msg.type,
        queuedCount: queue.length,
        at: Date.now(),
      });
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
    pendingCount() {
      return queue.length;
    },
  };
}
