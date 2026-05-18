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
  // Default the existing test suite to "has global config on this
  // device" so disk-hint-driven prefilling continues to apply; the
  // pristine-device path has its own dedicated test below.
  has_global_env: true,
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
  recommended_agent_models: {
    anthropic: {
      planner: "claude-opus-4-7",
      planner_judge: "claude-opus-4-7",
      conflict_analyst: "claude-opus-4-7",
      executor: "claude-opus-4-7",
      judge: "claude-opus-4-7",
      human_interface: "claude-haiku-4-5-20251001",
    },
    openai: {
      planner: "gpt-5.4",
      planner_judge: "gpt-5.4",
      conflict_analyst: "gpt-5.4",
      executor: "gpt-5.4",
      judge: "gpt-5.4",
      human_interface: "gpt-5.4-mini",
    },
  },
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
    // The models textarea pre-fills from ctx.provider_recommended_models
    // so first-run submission lists those models verbatim.
    expect(msg.payload.anthropic.models).toEqual(
      baseContext.provider_recommended_models.anthropic,
    );
    // Disabled provider still sends its pre-filled models textarea so
    // re-enabling later doesn't wipe it; backend validator only
    // requires models for ENABLED providers.
    expect(msg.payload.openai.enabled).toBe(false);
    // AGENT OVERRIDES always pre-fills every agent explicitly — no
    // implicit "inherit default" path on the wire any more.
    expect(Object.keys(msg.payload.agent_choices).sort()).toEqual(
      baseAgentInventory.map((e) => e.name).sort(),
    );
    // Every row pre-populated to (anthropic, recommended-for-agent).
    // human_interface picks haiku because it's in models and the
    // (anthropic, human_interface) recommendation points at it.
    expect(msg.payload.agent_choices.planner).toEqual({
      provider: "anthropic",
      model: "claude-opus-4-7",
    });
    expect(msg.payload.agent_choices.human_interface).toEqual({
      provider: "anthropic",
      model: "claude-haiku-4-5-20251001",
    });
  });

  it("blocks submit when an enabled provider has an empty models list", () => {
    useRunStore.setState({
      setupContext: {
        ...baseContext,
        anthropic_key_hint: {
          name: "ANTHROPIC_API_KEY",
          masked: "sk-ant",
          source: "shell",
        },
        provider_recommended_models: { anthropic: [], openai: [] },
      },
    });
    const { getByText } = renderSetup();
    act(() => {
      fireEvent.click(getByText(/SAVE & START/));
    });
    expect(sendSpy).not.toHaveBeenCalled();
    expect(getByText(/models list is empty/i)).toBeTruthy();
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

  it("pristine device (no global env + no project config) renders blank provider sections even when shell hints exist", () => {
    useRunStore.setState({
      setupContext: {
        ...baseContext,
        has_global_env: false,
        has_existing_config: false,
        // Shell env happens to leak keys — should still be ignored on
        // a pristine device so the form reads as "configure from
        // scratch".
        anthropic_key_hint: {
          name: "ANTHROPIC_API_KEY",
          masked: "sk-ant-leak",
          source: "shell",
        },
        openai_key_hint: {
          name: "OPENAI_API_KEY",
          masked: "sk-oai-leak",
          source: "shell",
        },
        anthropic_base_url: "https://leaked.example",
        openai_base_url: "https://leaked.example",
      },
    });
    const { getByTestId, getByText, queryByText } = renderSetup();

    // Models textareas are blank — recommended list is suppressed.
    const anthropicModels = getByTestId(
      "anthropic_models",
    ) as HTMLTextAreaElement;
    const openaiModels = getByTestId("openai_models") as HTMLTextAreaElement;
    expect(anthropicModels.value).toBe("");
    expect(openaiModels.value).toBe("");

    // The "existing key:" hint copy must not leak the shell-derived
    // masked value.
    expect(queryByText(/sk-ant-leak/)).toBeNull();
    expect(queryByText(/sk-oai-leak/)).toBeNull();

    // With both providers blank the form blocks submit at validation
    // (mirrors the "no key configured anywhere" message) — proves
    // the shell hint is *not* unlocking submit on a pristine device.
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

  it("agent override flows through to agent_choices with model auto-picked", () => {
    useRunStore.setState({
      setupContext: {
        ...baseContext,
        anthropic_key_hint: { name: "ANTHROPIC_API_KEY", masked: "sk-a", source: "shell" },
        openai_key_hint: { name: "OPENAI_API_KEY", masked: "sk-o", source: "shell" },
      },
    });
    const { getByTestId, getByText, container } = renderSetup();
    // Expand AGENT OVERRIDES — click the title span (not the hint text).
    act(() => {
      fireEvent.click(getByText(/^› AGENT OVERRIDES/));
    });
    // Two selects per row (provider, model). planner_judge is row 1.
    const overrides = getByTestId("agent-overrides");
    const rows = overrides.children;
    const plannerJudgeRow = rows[1] as HTMLElement;
    const [providerSelect, modelSelect] =
      plannerJudgeRow.querySelectorAll("select");
    expect(providerSelect).toBeDefined();
    expect(modelSelect).toBeDefined();

    act(() => {
      fireEvent.change(providerSelect, { target: { value: "openai" } });
    });

    // Switching provider auto-picks the first model of that provider so
    // the row is immediately submittable.
    expect((modelSelect as HTMLSelectElement).value).toBe("gpt-5.4");

    // Now switch model to gpt-5.4-mini explicitly.
    act(() => {
      fireEvent.change(modelSelect, { target: { value: "gpt-5.4-mini" } });
    });

    act(() => {
      fireEvent.click(getByText(/SAVE & START/));
    });
    expect(sendSpy).toHaveBeenCalledTimes(1);
    const msg = sendSpy.mock.calls[0][0];
    if (msg.type !== "setup.submit") throw new Error("wrong type");
    expect(msg.payload.agent_choices.planner_judge).toEqual({
      provider: "openai",
      model: "gpt-5.4-mini",
    });
    // Non-overridden rows still ship — they carry the pre-filled
    // (default_provider, recommended-for-agent) defaults.
    expect(msg.payload.agent_choices.planner).toEqual({
      provider: "anthropic",
      model: "claude-opus-4-7",
    });

    // models lists go through verbatim, parsed from the textarea
    // (pre-filled from ctx.provider_recommended_models).
    expect(msg.payload.anthropic.models).toEqual(
      baseContext.provider_recommended_models.anthropic,
    );
    expect(msg.payload.openai.models).toEqual(
      baseContext.provider_recommended_models.openai,
    );
    // Verify the data-testid exists on the textarea(s) too — confirms
    // the UI rendered the new "available models" widget.
    expect(
      container.querySelector("[data-testid='anthropic_models']"),
    ).toBeTruthy();
  });

  it("re-points agent rows whose model is removed from the provider's models textarea", () => {
    useRunStore.setState({
      setupContext: {
        ...baseContext,
        anthropic_key_hint: {
          name: "ANTHROPIC_API_KEY",
          masked: "sk-a",
          source: "shell",
        },
      },
    });
    const { container, getByText, getByTestId } = renderSetup();
    // Default planner row is (anthropic, claude-opus-4-7) — first
    // recommended model. Remove that line from the Anthropic Models
    // textarea, leaving only claude-haiku-4-5-20251001 available.
    const anthModels = getByTestId(
      "anthropic_models",
    ) as HTMLTextAreaElement;
    act(() => {
      fireEvent.change(anthModels, {
        target: { value: "claude-haiku-4-5-20251001" },
      });
    });
    // Without the fix, submit would error with
    // "Agent \"planner\" must pick a model from anthropic.models."
    act(() => {
      fireEvent.click(getByText(/SAVE & START/));
    });
    expect(sendSpy).toHaveBeenCalledTimes(1);
    const msg = sendSpy.mock.calls[0][0];
    if (msg.type !== "setup.submit") throw new Error("wrong type");
    // planner should have been migrated to the remaining model.
    expect(msg.payload.agent_choices.planner.provider).toBe("anthropic");
    expect(msg.payload.agent_choices.planner.model).toBe(
      "claude-haiku-4-5-20251001",
    );
    expect(msg.payload.anthropic.models).toEqual([
      "claude-haiku-4-5-20251001",
    ]);
    // No leftover stale reference to the removed model anywhere.
    for (const [, choice] of Object.entries(msg.payload.agent_choices)) {
      if (choice.provider === "anthropic") {
        expect(choice.model).not.toBe("claude-opus-4-7");
      }
    }
    expect(container.querySelector("[data-testid='anthropic_models']"))
      .toBeTruthy();
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
