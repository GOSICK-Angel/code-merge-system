import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { resolveWsUrl } from "./client";

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
