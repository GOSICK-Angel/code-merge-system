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
import type { ConfigFieldNode, SetupContext } from "../types/state";

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
  has_project_env: false,
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
  pendingCount: () => number;
} | null> {
  return {
    current: { send: sendSpy, close: vi.fn(), pendingCount: () => 0 },
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
    // LLM assist defaults to complexity-driven auto.
    expect(msg.payload.llm_assist_mode).toBe("auto");
  });

  it("submits the chosen LLM assist mode", () => {
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
    const { getByTestId, getByText } = renderSetup();

    // The LLM assist selector lives in the collapsed ADVANCED panel.
    act(() => {
      fireEvent.click(getByText(/ADVANCED/));
    });
    act(() => {
      fireEvent.change(getByTestId("llm_assist_mode"), {
        target: { value: "off" },
      });
    });
    act(() => {
      fireEvent.click(getByText(/SAVE & START/));
    });

    const msg = sendSpy.mock.calls[0][0];
    if (msg.type !== "setup.submit") throw new Error("wrong type");
    expect(msg.payload.llm_assist_mode).toBe("off");
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

  it("ships a cross-provider fallback (other provider's first model) when both enabled", () => {
    useRunStore.setState({
      setupContext: {
        ...baseContext,
        anthropic_key_hint: { name: "ANTHROPIC_API_KEY", masked: "sk-a", source: "shell" },
        openai_key_hint: { name: "OPENAI_API_KEY", masked: "sk-o", source: "shell" },
      },
    });
    const { getByText, getByTestId } = renderSetup();
    // Card visible with the non-default provider pre-selected.
    expect(getByText(/CROSS-PROVIDER FALLBACK/)).toBeTruthy();
    expect((getByTestId("fallback_provider") as HTMLSelectElement).value).toBe(
      "openai",
    );
    expect((getByTestId("fallback_model") as HTMLSelectElement).value).toBe(
      "gpt-5.4",
    );

    act(() => {
      fireEvent.click(getByText(/SAVE & START/));
    });
    const msg = sendSpy.mock.calls[0][0];
    if (msg.type !== "setup.submit") throw new Error("wrong type");
    expect(msg.payload.fallback).toEqual({ provider: "openai", model: "gpt-5.4" });
  });

  it("lets the user pick a different fallback model", () => {
    useRunStore.setState({
      setupContext: {
        ...baseContext,
        anthropic_key_hint: { name: "ANTHROPIC_API_KEY", masked: "sk-a", source: "shell" },
        openai_key_hint: { name: "OPENAI_API_KEY", masked: "sk-o", source: "shell" },
      },
    });
    const { getByText, getByTestId } = renderSetup();
    act(() => {
      fireEvent.change(getByTestId("fallback_model"), {
        target: { value: "gpt-5.4-mini" },
      });
    });
    act(() => {
      fireEvent.click(getByText(/SAVE & START/));
    });
    const msg = sendSpy.mock.calls[0][0];
    if (msg.type !== "setup.submit") throw new Error("wrong type");
    expect(msg.payload.fallback).toEqual({
      provider: "openai",
      model: "gpt-5.4-mini",
    });
  });

  it("disabling the fallback toggle sends fallback=null", () => {
    useRunStore.setState({
      setupContext: {
        ...baseContext,
        anthropic_key_hint: { name: "ANTHROPIC_API_KEY", masked: "sk-a", source: "shell" },
        openai_key_hint: { name: "OPENAI_API_KEY", masked: "sk-o", source: "shell" },
      },
    });
    const { getByText, getByTestId } = renderSetup();
    act(() => {
      fireEvent.click(getByTestId("fallback_enabled")); // toggle OFF
    });
    act(() => {
      fireEvent.click(getByText(/SAVE & START/));
    });
    const msg = sendSpy.mock.calls[0][0];
    if (msg.type !== "setup.submit") throw new Error("wrong type");
    expect(msg.payload.fallback).toBeNull();
  });

  it("flipping the default provider flips the fallback target", () => {
    useRunStore.setState({
      setupContext: {
        ...baseContext,
        anthropic_key_hint: { name: "ANTHROPIC_API_KEY", masked: "sk-a", source: "shell" },
        openai_key_hint: { name: "OPENAI_API_KEY", masked: "sk-o", source: "shell" },
      },
    });
    const { getByTestId, container } = renderSetup();
    // default_provider radios render in [anthropic, openai] order — pick openai.
    const radios = container.querySelectorAll<HTMLInputElement>(
      'input[name="default_provider"]',
    );
    act(() => {
      fireEvent.click(radios[1]);
    });
    // Fallback now targets the new non-default provider (anthropic).
    expect((getByTestId("fallback_provider") as HTMLSelectElement).value).toBe(
      "anthropic",
    );
    expect((getByTestId("fallback_model") as HTMLSelectElement).value).toBe(
      "claude-opus-4-7",
    );
  });

  it("omits the fallback card and sends fallback=null with a single provider", () => {
    useRunStore.setState({
      setupContext: {
        ...baseContext,
        anthropic_key_hint: { name: "ANTHROPIC_API_KEY", masked: "sk-ant", source: "shell" },
      },
    });
    const { getByText, queryByText } = renderSetup();
    expect(queryByText(/CROSS-PROVIDER FALLBACK/)).toBeNull();
    act(() => {
      fireEvent.click(getByText(/SAVE & START/));
    });
    const msg = sendSpy.mock.calls[0][0];
    if (msg.type !== "setup.submit") throw new Error("wrong type");
    expect(msg.payload.fallback).toBeNull();
  });

  it("auto-fills per-model params (recommended defaults) and ships them on submit", () => {
    useRunStore.setState({
      setupContext: {
        ...baseContext,
        anthropic_key_hint: { name: "ANTHROPIC_API_KEY", masked: "sk-a", source: "shell" },
        openai_key_hint: { name: "OPENAI_API_KEY", masked: "sk-o", source: "shell" },
      },
    });
    const { getByText, getByTestId } = renderSetup();
    act(() => {
      fireEvent.click(getByText(/^› MODEL PARAMETERS/));
    });
    // anthropic opus → 8192; openai gpt-5.4 (reasoning) → 32768; haiku → 4096.
    expect(
      (getByTestId("mp-claude-opus-4-7-max_tokens") as HTMLInputElement).value,
    ).toBe("8192");
    expect((getByTestId("mp-gpt-5.4-max_tokens") as HTMLInputElement).value).toBe(
      "32768",
    );
    expect(
      (getByTestId("mp-claude-haiku-4-5-20251001-max_tokens") as HTMLInputElement)
        .value,
    ).toBe("4096");

    act(() => {
      fireEvent.click(getByText(/SAVE & START/));
    });
    const msg = sendSpy.mock.calls[0][0];
    if (msg.type !== "setup.submit") throw new Error("wrong type");
    expect(msg.payload.model_params["claude-opus-4-7"]).toEqual({
      max_tokens: 8192,
      temperature: 0.2,
      max_retries: 3,
    });
    expect(msg.payload.model_params["gpt-5.4"].max_tokens).toBe(32768);
  });

  it("editing a model's params flows into model_params on submit", () => {
    useRunStore.setState({
      setupContext: {
        ...baseContext,
        anthropic_key_hint: { name: "ANTHROPIC_API_KEY", masked: "sk-a", source: "shell" },
      },
    });
    const { getByText, getByTestId } = renderSetup();
    act(() => {
      fireEvent.click(getByText(/^› MODEL PARAMETERS/));
    });
    act(() => {
      fireEvent.change(getByTestId("mp-claude-opus-4-7-max_tokens"), {
        target: { value: "12000" },
      });
    });
    act(() => {
      fireEvent.change(getByTestId("mp-claude-opus-4-7-temperature"), {
        target: { value: "0.4" },
      });
    });
    act(() => {
      fireEvent.click(getByText(/SAVE & START/));
    });
    const msg = sendSpy.mock.calls[0][0];
    if (msg.type !== "setup.submit") throw new Error("wrong type");
    expect(msg.payload.model_params["claude-opus-4-7"]).toEqual({
      max_tokens: 12000,
      temperature: 0.4,
      max_retries: 3,
    });
  });

  it("blocks submit on an out-of-range model param", () => {
    useRunStore.setState({
      setupContext: {
        ...baseContext,
        anthropic_key_hint: { name: "ANTHROPIC_API_KEY", masked: "sk-a", source: "shell" },
      },
    });
    const { getByText, getByTestId } = renderSetup();
    act(() => {
      fireEvent.click(getByText(/^› MODEL PARAMETERS/));
    });
    act(() => {
      fireEvent.change(getByTestId("mp-claude-opus-4-7-max_tokens"), {
        target: { value: "10" }, // below 512
      });
    });
    act(() => {
      fireEvent.click(getByText(/SAVE & START/));
    });
    expect(sendSpy).not.toHaveBeenCalled();
    expect(getByText(/max_tokens must be between 512 and 200000/)).toBeTruthy();
  });

  it("removing a model from the provider textarea drops its param row", () => {
    useRunStore.setState({
      setupContext: {
        ...baseContext,
        anthropic_key_hint: { name: "ANTHROPIC_API_KEY", masked: "sk-a", source: "shell" },
      },
    });
    const { getByText, getByTestId, queryByTestId } = renderSetup();
    act(() => {
      fireEvent.click(getByText(/^› MODEL PARAMETERS/));
    });
    expect(queryByTestId("model-param-claude-opus-4-7")).toBeTruthy();
    // Drop opus from the Anthropic models textarea.
    act(() => {
      fireEvent.change(getByTestId("anthropic_models"), {
        target: { value: "claude-haiku-4-5-20251001" },
      });
    });
    expect(queryByTestId("model-param-claude-opus-4-7")).toBeNull();
    expect(queryByTestId("model-param-claude-haiku-4-5-20251001")).toBeTruthy();
  });

  it("test-connection button sends setup.test_connection and renders per-model results", () => {
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
    const { getByTestId } = renderSetup();

    // Anthropic auto-enabled (key on disk) with recommended models
    // pre-filled, so the probe button is active.
    act(() => {
      fireEvent.click(getByTestId("anthropic_test"));
    });
    expect(sendSpy).toHaveBeenCalledTimes(1);
    const msg = sendSpy.mock.calls[0][0];
    if (msg.type !== "setup.test_connection") throw new Error("wrong type");
    expect(msg.payload.provider).toBe("anthropic");
    expect(msg.payload.models).toEqual(
      baseContext.provider_recommended_models.anthropic,
    );

    // Server replies — the panel renders one row per model with the
    // ok/fail verdict.
    act(() => {
      useRunStore.getState().applySetupTestResult({
        provider: "anthropic",
        error: null,
        results: [
          {
            model: "claude-opus-4-7",
            ok: true,
            latency_ms: 142,
            detail: "ok",
          },
          {
            model: "claude-haiku-4-5-20251001",
            ok: false,
            latency_ms: null,
            detail: "auth_permanent: 401",
          },
        ],
      });
    });
    const panel = getByTestId("anthropic_test_result");
    expect(panel.textContent).toContain("claude-opus-4-7");
    expect(panel.textContent).toContain("142ms");
    expect(panel.textContent).toContain("auth_permanent: 401");
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

// ---- Schema-driven FULL CONFIGURATION (Web config UI Phase 1 + 2) ---------

function cfgLeaf(
  name: string,
  path: string,
  kind: ConfigFieldNode["kind"],
  def: unknown,
): ConfigFieldNode {
  return {
    name,
    path,
    kind,
    default: def,
    description: null,
    required: false,
    curated: false,
    enum: null,
    minimum: null,
    maximum: null,
    children: [],
  };
}

const schemaFixture: ConfigFieldNode = {
  name: "",
  path: "",
  kind: "object",
  default: null,
  description: null,
  required: false,
  curated: false,
  enum: null,
  minimum: null,
  maximum: null,
  children: [
    cfgLeaf("max_files_per_run", "max_files_per_run", "int", 500),
    cfgLeaf("customizations", "customizations", "yaml", []),
  ],
};

function contextWithSchema(): SetupContext {
  return {
    ...baseContext,
    anthropic_key_hint: {
      name: "ANTHROPIC_API_KEY",
      masked: "sk-ant-****",
      source: "shell",
    },
    config_schema: schemaFixture,
    config_values: {},
  };
}

describe("Setup — FULL CONFIGURATION schema editor", () => {
  it("scalar + YAML edits flow into config_overrides on submit", () => {
    useRunStore.setState({ setupContext: contextWithSchema() });
    const { getByText, getByTestId } = renderSetup();

    act(() => {
      fireEvent.click(getByText(/FULL CONFIGURATION/));
    });
    act(() => {
      fireEvent.change(getByTestId("cfg-max_files_per_run"), {
        target: { value: "42" },
      });
    });
    act(() => {
      fireEvent.change(getByTestId("yaml-customizations"), {
        target: { value: "- name: t1" },
      });
    });
    act(() => {
      fireEvent.click(getByText(/SAVE & START/));
    });

    expect(sendSpy).toHaveBeenCalledTimes(1);
    const msg = sendSpy.mock.calls[0][0];
    if (msg.type !== "setup.submit") throw new Error("wrong type");
    expect(msg.payload.config_overrides).toEqual({
      max_files_per_run: 42,
      customizations: [{ name: "t1" }],
    });
  });

  it("blocks submit on invalid YAML and reports the offending path", () => {
    useRunStore.setState({ setupContext: contextWithSchema() });
    const { getByText, getByTestId } = renderSetup();

    act(() => {
      fireEvent.click(getByText(/FULL CONFIGURATION/));
    });
    act(() => {
      fireEvent.change(getByTestId("yaml-customizations"), {
        target: { value: "key: [unclosed" },
      });
    });
    act(() => {
      fireEvent.click(getByText(/SAVE & START/));
    });

    expect(sendSpy).not.toHaveBeenCalled();
    expect(getByText(/Fix invalid YAML/)).toBeTruthy();
  });

  it("search filters the field list", () => {
    useRunStore.setState({ setupContext: contextWithSchema() });
    const { getByText, getByTestId, queryByTestId } = renderSetup();

    act(() => {
      fireEvent.click(getByText(/FULL CONFIGURATION/));
    });
    expect(queryByTestId("cfg-max_files_per_run")).toBeTruthy();
    act(() => {
      fireEvent.change(getByTestId("config-search"), {
        target: { value: "customizations" },
      });
    });
    // The non-matching scalar is filtered out; the matching yaml stays.
    expect(queryByTestId("cfg-max_files_per_run")).toBeNull();
    expect(queryByTestId("yaml-customizations")).toBeTruthy();
  });

  it("a modified field shows a reset control that restores the default", () => {
    useRunStore.setState({ setupContext: contextWithSchema() });
    const { getByText, getByTestId, queryByText } = renderSetup();

    act(() => {
      fireEvent.click(getByText(/FULL CONFIGURATION/));
    });
    // Default 500 → not modified → no reset affordance yet.
    expect(queryByText(/reset all/i)).toBeNull();
    act(() => {
      fireEvent.change(getByTestId("cfg-max_files_per_run"), {
        target: { value: "7" },
      });
    });
    expect(getByText(/1 modified/)).toBeTruthy();
    act(() => {
      fireEvent.click(getByText(/reset all/i));
    });
    expect((getByTestId("cfg-max_files_per_run") as HTMLInputElement).value).toBe(
      "500",
    );
  });
});

// ---- FULL CONFIGURATION llm-block provider/model coupling -----------------

function llmLeaf(
  name: string,
  path: string,
  kind: ConfigFieldNode["kind"],
  def: unknown,
  enumVals: string[] | null = null,
): ConfigFieldNode {
  return {
    name,
    path,
    kind,
    default: def,
    description: null,
    required: false,
    curated: false,
    enum: enumVals,
    minimum: null,
    maximum: null,
    children: [],
  };
}

// Mirrors the LLMConfig subtree of the real schema (the only non-curated
// model fields). provider is an enum, model/fallback_model are str leaves
// the UI upgrades to provider-coupled dropdowns.
const llmSchemaFixture: ConfigFieldNode = {
  name: "",
  path: "",
  kind: "object",
  default: null,
  description: null,
  required: false,
  curated: false,
  enum: null,
  minimum: null,
  maximum: null,
  children: [
    {
      name: "llm",
      path: "llm",
      kind: "object",
      default: null,
      description: null,
      required: false,
      curated: false,
      enum: null,
      minimum: null,
      maximum: null,
      children: [
        llmLeaf("provider", "llm.provider", "enum", "anthropic", [
          "anthropic",
          "openai",
        ]),
        llmLeaf("model", "llm.model", "str", "claude-opus-4-6"),
        llmLeaf("fallback_model", "llm.fallback_model", "str", null),
      ],
    },
  ],
};

function contextWithLlmSchema(): SetupContext {
  return {
    ...baseContext,
    // Both providers on disk → both enabled, so the provider picker can
    // offer a switch target.
    anthropic_key_hint: {
      name: "ANTHROPIC_API_KEY",
      masked: "sk-ant-****",
      source: "shell",
    },
    openai_key_hint: {
      name: "OPENAI_API_KEY",
      masked: "sk-oai-****",
      source: "shell",
    },
    config_schema: llmSchemaFixture,
    config_values: {},
  };
}

describe("Setup — FULL CONFIGURATION llm provider/model coupling", () => {
  function openLlmSection(getByText: ReturnType<typeof renderSetup>["getByText"], getByTestId: ReturnType<typeof renderSetup>["getByTestId"]) {
    act(() => {
      fireEvent.click(getByText(/FULL CONFIGURATION/));
    });
    // Searching expands every matching group (forceOpen) so the llm
    // leaves render without hunting for the collapse toggle.
    act(() => {
      fireEvent.change(getByTestId("config-search"), {
        target: { value: "llm" },
      });
    });
  }

  it("renders llm.model / fallback_model as dropdowns sourced from the configured models", () => {
    useRunStore.setState({ setupContext: contextWithLlmSchema() });
    const { getByText, getByTestId } = renderSetup();
    openLlmSection(getByText, getByTestId);

    const modelSel = getByTestId("cfg-llm.model") as HTMLSelectElement;
    expect(modelSel.tagName).toBe("SELECT");
    const modelOpts = Array.from(modelSel.options).map((o) => o.value);
    // anthropic catalogue from provider_recommended_models
    expect(modelOpts).toContain("claude-opus-4-7");
    expect(modelOpts).toContain("claude-haiku-4-5-20251001");
    // schema default ("claude-opus-4-6") is not in the list → kept as a
    // stale option rather than silently dropped.
    expect(modelOpts).toContain("claude-opus-4-6");

    // fallback_model is CROSS-provider: it lists models from every enabled
    // provider (so a fallback can target a different provider than the
    // primary), plus the (none) option.
    const fbSel = getByTestId("cfg-llm.fallback_model") as HTMLSelectElement;
    expect(fbSel.tagName).toBe("SELECT");
    const fbOpts = Array.from(fbSel.options).map((o) => o.value);
    expect(fbOpts).toContain(""); // (none) — fallback is optional
    expect(fbOpts).toContain("claude-opus-4-7"); // anthropic
    expect(fbOpts).toContain("gpt-5.4"); // openai — cross-provider
    expect(fbOpts).toContain("gpt-5.4-mini");
  });

  it("provider picker is limited to enabled providers; switching it re-snaps model but leaves the cross-provider fallback", () => {
    useRunStore.setState({ setupContext: contextWithLlmSchema() });
    const { getByText, getByTestId } = renderSetup();
    openLlmSection(getByText, getByTestId);

    const providerSel = getByTestId("cfg-llm.provider") as HTMLSelectElement;
    expect(providerSel.tagName).toBe("SELECT");
    expect(Array.from(providerSel.options).map((o) => o.value).sort()).toEqual([
      "anthropic",
      "openai",
    ]);

    // Pin a cross-provider fallback (openai) while the primary is anthropic.
    act(() => {
      fireEvent.change(getByTestId("cfg-llm.fallback_model"), {
        target: { value: "gpt-5.4" },
      });
    });

    act(() => {
      fireEvent.change(providerSel, { target: { value: "openai" } });
    });

    // Primary model snapped to the first openai model; options now openai's.
    const modelSel = getByTestId("cfg-llm.model") as HTMLSelectElement;
    expect(modelSel.value).toBe("gpt-5.4");
    const modelOpts = Array.from(modelSel.options).map((o) => o.value);
    expect(modelOpts).toContain("gpt-5.4");
    expect(modelOpts).toContain("gpt-5.4-mini");
    // Cross-provider fallback is independent of the primary provider, so the
    // switch must NOT clear or re-snap it.
    expect((getByTestId("cfg-llm.fallback_model") as HTMLSelectElement).value).toBe(
      "gpt-5.4",
    );
  });

  it("editing the model picker flows into config_overrides on submit", () => {
    useRunStore.setState({ setupContext: contextWithLlmSchema() });
    const { getByText, getByTestId } = renderSetup();
    openLlmSection(getByText, getByTestId);

    act(() => {
      fireEvent.change(getByTestId("cfg-llm.model"), {
        target: { value: "claude-haiku-4-5-20251001" },
      });
    });
    act(() => {
      fireEvent.click(getByText(/SAVE & START/));
    });

    expect(sendSpy).toHaveBeenCalledTimes(1);
    const msg = sendSpy.mock.calls[0][0];
    if (msg.type !== "setup.submit") throw new Error("wrong type");
    expect(
      (msg.payload.config_overrides.llm as Record<string, unknown>).model,
    ).toBe("claude-haiku-4-5-20251001");
  });

  it("a cross-provider fallback selection flows into config_overrides", () => {
    useRunStore.setState({ setupContext: contextWithLlmSchema() });
    const { getByText, getByTestId } = renderSetup();
    openLlmSection(getByText, getByTestId);

    // Primary provider is anthropic; pick an openai model as the fallback.
    act(() => {
      fireEvent.change(getByTestId("cfg-llm.fallback_model"), {
        target: { value: "gpt-5.4-mini" },
      });
    });
    act(() => {
      fireEvent.click(getByText(/SAVE & START/));
    });

    const msg = sendSpy.mock.calls[0][0];
    if (msg.type !== "setup.submit") throw new Error("wrong type");
    expect(
      (msg.payload.config_overrides.llm as Record<string, unknown>).fallback_model,
    ).toBe("gpt-5.4-mini");
  });
});
