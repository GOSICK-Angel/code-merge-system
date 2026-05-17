/**
 * Setup view smoke + interaction coverage:
 * - Renders form once setup context arrives + pre-fills defaults
 * - Submit fires `setup.submit` with sanitized payload (trimmed,
 *   empty thresholds → null, empty workflow → null)
 * - Local validation blocks submit when required fields are blank
 *   AND the server hasn't reported an existing API key on disk
 * - `setup_error` from the server surfaces in the form
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render } from "@testing-library/react";
import { useRunStore } from "../store/runStore";
import { Setup } from "./Setup";
import type { OutboundMessage } from "../ws/messages";
import type { SetupContext } from "../types/state";

const sendSpy = vi.fn<(msg: OutboundMessage) => void>();

const baseContext: SetupContext = {
  current_branch: "feat/x",
  suggested_target: "origin/main",
  api_key_hints: [
    { name: "ANTHROPIC_API_KEY", masked: "", source: "" },
    { name: "OPENAI_API_KEY", masked: "", source: "" },
    { name: "GITHUB_TOKEN", masked: "", source: "" },
  ],
  fork_divergence_count: 0,
  has_existing_config: false,
  existing_config_summary: null,
  forks_profile_threshold: 30,
};

beforeEach(() => {
  sendSpy.mockClear();
  useRunStore.setState({
    conn: "open",
    mode: "setup",
    snapshot: null,
    activity: [],
    lastCancelError: null,
    setupContext: baseContext,
    setupStatus: "idle",
    setupReady: null,
    setupError: null,
  });
});

function makeClientRef(): React.MutableRefObject<{
  send: (msg: OutboundMessage) => void;
  close: () => void;
} | null> {
  return {
    current: { send: sendSpy, close: vi.fn() },
  };
}

describe("Setup", () => {
  it("waiting state renders when no context yet", () => {
    useRunStore.setState({ setupContext: null });
    const ref = makeClientRef();
    const { getByText } = render(
      <Setup
        clientRef={
          ref as unknown as React.MutableRefObject<
            ReturnType<typeof makeClientRef>["current"]
          >
        }
      />,
    );
    expect(getByText(/waiting for server/i)).toBeTruthy();
  });

  it("pre-fills target and fork from context", () => {
    const ref = makeClientRef();
    const { getByLabelText } = render(
      <Setup
        clientRef={
          ref as unknown as React.MutableRefObject<
            ReturnType<typeof makeClientRef>["current"]
          >
        }
      />,
    );
    const target = getByLabelText(/target branch/i) as HTMLInputElement;
    const fork = getByLabelText(/fork ref/i) as HTMLInputElement;
    expect(target.value).toBe("origin/main");
    expect(fork.value).toBe("feat/x");
  });

  it("blocks submit when no key supplied and nothing on disk", () => {
    const ref = makeClientRef();
    const { getByText } = render(
      <Setup
        clientRef={
          ref as unknown as React.MutableRefObject<
            ReturnType<typeof makeClientRef>["current"]
          >
        }
      />,
    );
    act(() => {
      fireEvent.click(getByText(/SAVE & START/));
    });
    expect(sendSpy).not.toHaveBeenCalled();
    expect(getByText(/ANTHROPIC_API_KEY is required/)).toBeTruthy();
  });

  it("submits sanitized payload when form is valid", () => {
    // Pretend both required keys are already on disk so the user can
    // submit without retyping them.
    useRunStore.setState({
      setupContext: {
        ...baseContext,
        api_key_hints: [
          { name: "ANTHROPIC_API_KEY", masked: "sk-ant-****", source: "shell" },
          { name: "OPENAI_API_KEY", masked: "sk-oa-****", source: "project_env" },
          { name: "GITHUB_TOKEN", masked: "", source: "" },
        ],
      },
    });
    const ref = makeClientRef();
    const { getByText, getByLabelText } = render(
      <Setup
        clientRef={
          ref as unknown as React.MutableRefObject<
            ReturnType<typeof makeClientRef>["current"]
          >
        }
      />,
    );
    fireEvent.change(getByLabelText(/target branch/i), {
      target: { value: " upstream/main " },
    });
    act(() => {
      fireEvent.click(getByText(/SAVE & START/));
    });

    expect(sendSpy).toHaveBeenCalledTimes(1);
    const msg = sendSpy.mock.calls[0][0];
    expect(msg.type).toBe("setup.submit");
    if (msg.type !== "setup.submit") return;
    // trimmed
    expect(msg.payload.target_branch).toBe("upstream/main");
    expect(msg.payload.fork_ref).toBe("feat/x");
    // no api keys typed → empty record (existing on-disk values stay)
    expect(msg.payload.api_keys).toEqual({});
    // no advanced fields edited → thresholds null, workflow null
    expect(msg.payload.thresholds).toBeNull();
    expect(msg.payload.workflow).toBeNull();
    expect(msg.payload.dry_run).toBe(false);
    expect(msg.payload.init_forks_profile).toBe(false);
  });

  it("surfaces server-side setup_error in the form", () => {
    useRunStore.setState({
      setupStatus: "error",
      setupError: { reason: "apply_failed", details: "disk full" },
    });
    const ref = makeClientRef();
    const { getByText } = render(
      <Setup
        clientRef={
          ref as unknown as React.MutableRefObject<
            ReturnType<typeof makeClientRef>["current"]
          >
        }
      />,
    );
    expect(getByText("apply_failed")).toBeTruthy();
    expect(getByText("disk full")).toBeTruthy();
  });

  it("renders 'config saved' overlay after setup_ready", () => {
    useRunStore.setState({
      setupStatus: "ready",
      setupReady: {
        config_path: "/tmp/repo/.merge/config.yaml",
        dry_run: false,
        workflow: null,
        init_forks_profile: false,
      },
    });
    const ref = makeClientRef();
    const { getByText } = render(
      <Setup
        clientRef={
          ref as unknown as React.MutableRefObject<
            ReturnType<typeof makeClientRef>["current"]
          >
        }
      />,
    );
    expect(getByText(/CONFIG SAVED/)).toBeTruthy();
    expect(getByText(/\/tmp\/repo\/.merge\/config\.yaml/)).toBeTruthy();
  });
});
