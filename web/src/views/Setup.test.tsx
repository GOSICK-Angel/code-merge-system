/**
 * Setup view coverage for the flexible-provider revision:
 * - Single-provider mode: only Anthropic enabled → submit allowed
 *   without retyping the key when ctx says it's on disk; no default
 *   provider picker shown.
 * - Both-provider mode: default provider radio appears and is
 *   required.
 * - Per-agent override: picking provider=openai for `planner_judge`
 *   surfaces in the outgoing `agent_choices`.
 * - Validation: enabling a provider without a key (and none on disk)
 *   blocks submit with a specific error.
 * - `setup_error` from the server still surfaces.
 * - `config saved` overlay rendered after `setup_ready`.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render } from "@testing-library/react";
import { useRunStore } from "../store/runStore";
import { Setup } from "./Setup";
import type { OutboundMessage } from "../ws/messages";
import type { SetupContext } from "../types/state";

const sendSpy = vi.fn<(msg: OutboundMessage) => void>();

const baseAgentInventory = [
  { name: "planner", blurb: "produces the merge plan" },
  { name: "planner_judge", blurb: "reviews / negotiates the plan" },
  { name: "conflict_analyst", blurb: "analyses conflict semantics" },
  { name: "executor", blurb: "applies patches" },
  { name: "judge", blurb: "post-merge verdict" },
  { name: "human_interface", blurb: "summarises human prompts" },
];

const baseContext: SetupContext = {
  current_branch: "feat/x",
  suggested_target: "origin/main",
  fork_divergence_count: 0,
  has_existing_config: false,
  existing_config_summary: null,
  forks_profile_threshold: 30,
  anthropic_key_hint: { name: "ANTHROPIC_API_KEY", masked: "", source: "" },
  openai_key_hint: { name: "OPENAI_API_KEY", masked: "", source: "" },
  github_token_hint: { name: "GITHUB_TOKEN", masked: "", source: "" },
  anthropic_base_url: null,
  openai_base_url: null,
  provider_recommended_models: {
    anthropic: ["claude-opus-4-7", "claude-haiku-4-5-20251001"],
    openai: ["gpt-5.4", "gpt-5.4-mini"],
  },
  agent_inventory: baseAgentInventory,
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

function renderSetup() {
  const ref = makeClientRef();
  return render(
    <Setup
      clientRef={
        ref as unknown as React.MutableRefObject<
          ReturnType<typeof makeClientRef>["current"]
        >
      }
    />,
  );
}

describe("Setup — flexible providers", () => {
  it("waiting state renders when no context yet", () => {
    useRunStore.setState({ setupContext: null });
    const { getByText } = renderSetup();
    expect(getByText(/waiting for server/i)).toBeTruthy();
  });

  it("pre-fills target and fork from context", () => {
    const { getByLabelText } = renderSetup();
    expect((getByLabelText(/target branch/i) as HTMLInputElement).value).toBe(
      "origin/main",
    );
    expect((getByLabelText(/fork ref/i) as HTMLInputElement).value).toBe(
      "feat/x",
    );
  });

  it("auto-enables anthropic and skips default-provider picker when only one provider has a key on disk", () => {
    useRunStore.setState({
      setupContext: {
        ...baseContext,
        anthropic_key_hint: {
          name: "ANTHROPIC_API_KEY",
          masked: "sk-ant-****",
          source: "shell",
        },
      },
    });
    const { queryByText, getByText } = renderSetup();
    // No DEFAULT PROVIDER card when only one provider is enabled.
    expect(queryByText(/DEFAULT PROVIDER/i)).toBeNull();

    act(() => {
      fireEvent.click(getByText(/SAVE & START/));
    });
    expect(sendSpy).toHaveBeenCalledTimes(1);
    const msg = sendSpy.mock.calls[0][0];
    if (msg.type !== "setup.submit") throw new Error("wrong type");
    expect(msg.payload.anthropic.enabled).toBe(true);
    expect(msg.payload.openai.enabled).toBe(false);
    expect(msg.payload.default_provider).toBe("anthropic");
    // No agent overrides → empty map; backend will inherit default.
    expect(msg.payload.agent_choices).toEqual({});
  });

  it("blocks submit when both providers disabled or missing keys", () => {
    // base context: no keys on disk, providers disabled by default
    const { getByText } = renderSetup();
    act(() => {
      fireEvent.click(getByText(/SAVE & START/));
    });
    expect(sendSpy).not.toHaveBeenCalled();
    expect(getByText(/at least one provider/i)).toBeTruthy();
  });

  it("requires default provider when both anthropic and openai are enabled", () => {
    useRunStore.setState({
      setupContext: {
        ...baseContext,
        anthropic_key_hint: { name: "ANTHROPIC_API_KEY", masked: "sk-a-****", source: "shell" },
        openai_key_hint: { name: "OPENAI_API_KEY", masked: "sk-o-****", source: "shell" },
      },
    });
    const { getByText, queryByText } = renderSetup();
    // both pre-enabled from disk hints → DEFAULT PROVIDER card visible
    expect(queryByText(/DEFAULT PROVIDER/i)).toBeTruthy();
    // form pre-picks anthropic as default — submit should work
    act(() => {
      fireEvent.click(getByText(/SAVE & START/));
    });
    expect(sendSpy).toHaveBeenCalledTimes(1);
    const msg = sendSpy.mock.calls[0][0];
    if (msg.type !== "setup.submit") throw new Error("wrong type");
    expect(msg.payload.default_provider).toBe("anthropic");
    expect(msg.payload.anthropic.enabled).toBe(true);
    expect(msg.payload.openai.enabled).toBe(true);
  });

  it("agent override flows through to agent_choices", () => {
    useRunStore.setState({
      setupContext: {
        ...baseContext,
        anthropic_key_hint: { name: "ANTHROPIC_API_KEY", masked: "sk-a", source: "shell" },
        openai_key_hint: { name: "OPENAI_API_KEY", masked: "sk-o", source: "shell" },
      },
    });
    const { getByText, container } = renderSetup();
    // Expand AGENT OVERRIDES
    act(() => {
      fireEvent.click(getByText(/AGENT OVERRIDES/));
    });
    // Find the planner_judge row and set its provider select to openai.
    const selects = container.querySelectorAll(
      "[data-testid='agent-overrides'] select",
    );
    expect(selects.length).toBe(baseAgentInventory.length);
    // planner_judge is the second row (index 1)
    const plannerJudgeSelect = selects[1] as HTMLSelectElement;
    act(() => {
      fireEvent.change(plannerJudgeSelect, { target: { value: "openai" } });
    });

    act(() => {
      fireEvent.click(getByText(/SAVE & START/));
    });
    expect(sendSpy).toHaveBeenCalledTimes(1);
    const msg = sendSpy.mock.calls[0][0];
    if (msg.type !== "setup.submit") throw new Error("wrong type");
    expect(msg.payload.agent_choices.planner_judge).toEqual({
      provider: "openai",
      model: "",
    });
    // other agents not overridden — empty
    expect(msg.payload.agent_choices.planner).toBeUndefined();
  });

  it("surfaces server-side setup_error in the form", () => {
    useRunStore.setState({
      setupStatus: "error",
      setupError: { reason: "apply_failed", details: "disk full" },
    });
    const { getByText } = renderSetup();
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
    const { getByText } = renderSetup();
    expect(getByText(/CONFIG SAVED/)).toBeTruthy();
    expect(getByText(/\/tmp\/repo\/.merge\/config\.yaml/)).toBeTruthy();
  });
});
