import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  createWsClient,
  resolveWsUrl,
  type OutboundDropEvent,
  type OutboundFlushEvent,
} from "./client";

describe("resolveWsUrl", () => {
  const original = { ...window.location };

  beforeEach(() => {
    Object.defineProperty(window, "location", {
      writable: true,
      value: { ...original },
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  function setLocation(search: string, hostname = "localhost"): void {
    Object.defineProperty(window, "location", {
      writable: true,
      value: { ...original, search, hostname },
    });
  }

  it("defaults to port 8765 when ?ws= is absent", () => {
    setLocation("");
    expect(resolveWsUrl()).toBe("ws://localhost:8765");
  });

  it("respects ?ws=<port> from query string", () => {
    setLocation("?ws=9000");
    expect(resolveWsUrl()).toBe("ws://localhost:9000");
  });

  it("uses the current hostname", () => {
    setLocation("?ws=8765", "example.com");
    expect(resolveWsUrl()).toBe("ws://example.com:8765");
  });
});

/**
 * Fake WebSocket honouring the same readyState transitions the real
 * browser API exposes — enough surface for the outbound queue to make
 * its CONNECTING → OPEN decision and for ``send`` to be observable.
 */
class FakeWebSocket {
  static instances: FakeWebSocket[] = [];

  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;

  readyState: number = FakeWebSocket.CONNECTING;
  url: string;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: ((e: unknown) => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  send(raw: string): void {
    if (this.readyState !== FakeWebSocket.OPEN) {
      throw new Error("FakeWebSocket.send while not OPEN");
    }
    this.sent.push(raw);
  }

  close(): void {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.();
  }

  // Test hook — flip to OPEN and fire the onopen callback like the real
  // browser does on a successful handshake.
  fireOpen(): void {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }
}

describe("createWsClient outbound buffer", () => {
  const realWebSocket = globalThis.WebSocket;

  beforeEach(() => {
    FakeWebSocket.instances = [];
    // @ts-expect-error — assigning a test double over the global.
    globalThis.WebSocket = FakeWebSocket;
  });

  afterEach(() => {
    globalThis.WebSocket = realWebSocket;
    vi.restoreAllMocks();
  });

  it("queues frames sent before the socket reaches OPEN and flushes on onopen", () => {
    const drops: OutboundDropEvent[] = [];
    const flushes: OutboundFlushEvent[] = [];
    const client = createWsClient({
      onState: () => {},
      onMessage: () => {},
      onDrop: (e) => drops.push(e),
      onFlush: (e) => flushes.push(e),
    });

    expect(FakeWebSocket.instances.length).toBe(1);
    const ws = FakeWebSocket.instances[0];
    expect(ws.readyState).toBe(FakeWebSocket.CONNECTING);

    client.send({
      type: "submit_plan_review",
      payload: { decision: "approve" },
    });
    client.send({
      type: "submit_user_plan_decisions",
      payload: { items: [] },
    });

    expect(ws.sent).toEqual([]);
    expect(client.pendingCount()).toBe(2);
    expect(drops.map((d) => d.reason)).toEqual(["not_open", "not_open"]);
    expect(drops.map((d) => d.type)).toEqual([
      "submit_plan_review",
      "submit_user_plan_decisions",
    ]);

    ws.fireOpen();

    expect(ws.sent.length).toBe(2);
    const parsed = ws.sent.map((s) => JSON.parse(s));
    expect(parsed[0].type).toBe("submit_plan_review");
    expect(parsed[1].type).toBe("submit_user_plan_decisions");
    expect(client.pendingCount()).toBe(0);
    expect(flushes).toEqual([
      { flushedCount: 2, remainingCount: 0, at: expect.any(Number) },
    ]);
  });

  it("dedupes queued frames by type — latest payload wins", () => {
    const client = createWsClient({
      onState: () => {},
      onMessage: () => {},
    });
    const ws = FakeWebSocket.instances[0];

    client.send({
      type: "submit_plan_review",
      payload: { decision: "approve" },
    });
    client.send({
      type: "submit_plan_review",
      payload: { decision: "reject" },
    });

    expect(client.pendingCount()).toBe(1);

    ws.fireOpen();

    expect(ws.sent.length).toBe(1);
    expect(JSON.parse(ws.sent[0]).payload.decision).toBe("reject");
  });

  it("sends directly without buffering when socket is already OPEN", () => {
    const drops: OutboundDropEvent[] = [];
    const client = createWsClient({
      onState: () => {},
      onMessage: () => {},
      onDrop: (e) => drops.push(e),
    });
    const ws = FakeWebSocket.instances[0];
    ws.fireOpen();

    client.send({
      type: "submit_plan_review",
      payload: { decision: "approve" },
    });

    expect(ws.sent.length).toBe(1);
    expect(client.pendingCount()).toBe(0);
    expect(drops).toEqual([]);
  });

  it("emits onDrop and skips queue when the client has been manually closed", () => {
    const drops: OutboundDropEvent[] = [];
    const client = createWsClient({
      onState: () => {},
      onMessage: () => {},
      onDrop: (e) => drops.push(e),
    });
    client.close();

    client.send({
      type: "submit_plan_review",
      payload: { decision: "approve" },
    });

    expect(client.pendingCount()).toBe(0);
    expect(drops.length).toBe(1);
    expect(drops[0].reason).toBe("not_open");
  });
});
