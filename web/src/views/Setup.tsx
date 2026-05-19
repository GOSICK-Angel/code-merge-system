import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ChangeEvent,
  type CSSProperties,
  type FormEvent,
  type MutableRefObject,
} from "react";
import { Card, Pill } from "../components/brutalist";
import { useSetup } from "../ws/useSetup";
import type { WsClient } from "../ws/client";
import type {
  AgentChoice,
  ProviderName,
  SetupContext,
  SetupPayload,
  ThresholdsPayload,
} from "../types/state";

interface Props {
  clientRef: MutableRefObject<WsClient | null>;
}

interface ProviderFormState {
  enabled: boolean;
  api_key: string;
  base_url: string;
  // Free-text — newline-separated model names. Parsed into a list on
  // submit; the UI keeps it as a string so the user can edit
  // incomplete lines without the textarea losing focus on each parse.
  models_text: string;
}

interface AgentRowState {
  // Every row always carries an explicit (provider, model). The form
  // pre-fills using ``recommended_agent_models[provider][agent]`` when
  // available, falling back to ``provider.models[0]``. No "(default)"
  // option — the user always sees what each agent will run.
  provider: ProviderName;
  model: string;
}

interface FormState {
  target_branch: string;
  fork_ref: string;
  project_context: string;
  anthropic: ProviderFormState;
  openai: ProviderFormState;
  github_token: string;
  default_provider: ProviderName | "";
  agents: Record<string, AgentRowState>;
  threshold_auto: string;
  threshold_low: string;
  threshold_high: string;
  request_timeout_seconds: string;
  dry_run: boolean;
  workflow: string;
  init_forks_profile: boolean;
}

const WORKFLOW_OPTIONS = [
  { value: "", label: "(default)" },
  { value: "standard", label: "standard" },
  { value: "careful", label: "careful" },
  { value: "fast", label: "fast" },
  { value: "analysis-only", label: "analysis-only" },
];

const inputStyle: CSSProperties = {
  width: "100%",
  background: "var(--bg-2)",
  border: "1px solid var(--line)",
  color: "var(--fg-0)",
  padding: "6px 10px",
  fontFamily: "var(--mono)",
  fontSize: 12,
  outline: "none",
  boxSizing: "border-box",
};

const labelStyle: CSSProperties = {
  display: "block",
  fontSize: 10,
  letterSpacing: "0.08em",
  textTransform: "uppercase",
  color: "var(--fg-2)",
  marginBottom: 4,
};

const rowStyle: CSSProperties = {
  display: "grid",
  gap: 12,
  gridTemplateColumns: "1fr 1fr",
};

const containerStyle: CSSProperties = {
  padding: "32px 24px",
  maxWidth: 820,
  margin: "0 auto",
  width: "100%",
  boxSizing: "border-box",
};

const PROVIDER_LABEL: Record<ProviderName, string> = {
  anthropic: "Anthropic",
  openai: "OpenAI",
};

function recommendedModelFor(
  ctx: SetupContext,
  provider: ProviderName,
  agentName: string,
  availableModels: string[],
): string {
  // Prefer the (provider, agent) recommendation when it's actually in
  // the configured model list — otherwise the dropdown wouldn't be
  // able to show it. Falls back to models[0] for a sane default.
  const recommended = ctx.recommended_agent_models?.[provider]?.[agentName];
  if (recommended && availableModels.includes(recommended)) return recommended;
  return availableModels[0] ?? "";
}


function deriveDefaults(ctx: SetupContext): FormState {
  const summary = ctx.existing_config_summary ?? {};
  const target =
    typeof summary.upstream_ref === "string" && summary.upstream_ref
      ? summary.upstream_ref
      : ctx.suggested_target;
  const fork =
    typeof summary.fork_ref === "string" && summary.fork_ref
      ? summary.fork_ref
      : ctx.current_branch;
  const project =
    typeof summary.project_context === "string" ? summary.project_context : "";
  const thresholds =
    summary.thresholds && typeof summary.thresholds === "object"
      ? (summary.thresholds as Record<string, number | undefined>)
      : {};

  // Pre-fill providers from disk hints: enable a provider if a key
  // exists in the resolved chain so the user can submit without
  // retyping. Base URLs come from the resolved chain too. The
  // models textarea is pre-filled with the recommended list so a
  // first-run user only has to click submit; existing config (if
  // any) overrides below.
  //
  // Exception — pristine device: when ``has_global_env`` is false
  // AND no ``.merge/config.yaml`` exists yet, treat the form as a
  // truly empty slate. Even if shell env happens to leak an
  // ANTHROPIC_API_KEY into ``*_key_hint`` we keep the section blank
  // so the reviewer sees they're configuring from scratch.
  const pristineDevice =
    !ctx.has_global_env && !ctx.has_existing_config && !ctx.has_project_env;
  const anthropicHasKey = !pristineDevice && !!ctx.anthropic_key_hint.masked;
  const openaiHasKey = !pristineDevice && !!ctx.openai_key_hint.masked;
  const recommendedAnthropic =
    ctx.provider_recommended_models.anthropic ?? [];
  const recommendedOpenai = ctx.provider_recommended_models.openai ?? [];
  const anthropic: ProviderFormState = {
    enabled: anthropicHasKey,
    api_key: "",
    base_url: pristineDevice ? "" : ctx.anthropic_base_url ?? "",
    models_text: pristineDevice ? "" : recommendedAnthropic.join("\n"),
  };
  const openai: ProviderFormState = {
    enabled: openaiHasKey,
    api_key: "",
    base_url: pristineDevice ? "" : ctx.openai_base_url ?? "",
    models_text: pristineDevice ? "" : recommendedOpenai.join("\n"),
  };

  // Compute the default provider first — every agent row needs an
  // explicit (provider, model) so we need to know which provider to
  // pre-fill against. If both are enabled, anthropic wins by default
  // (user can switch via the DEFAULT PROVIDER radio).
  let defaultProvider: ProviderName | "" = "";
  if (anthropic.enabled && !openai.enabled) defaultProvider = "anthropic";
  else if (openai.enabled && !anthropic.enabled) defaultProvider = "openai";
  else if (anthropic.enabled && openai.enabled) defaultProvider = "anthropic";

  // Existing config (reconfigure flow) — use whatever the on-disk
  // config has for each agent. Otherwise pre-fill every row with
  // (default_provider, recommended_or_models[0]).
  const existingAgents =
    summary.agents && typeof summary.agents === "object"
      ? (summary.agents as Record<string, { provider?: string; model?: string }>)
      : {};
  const agents: Record<string, AgentRowState> = {};
  const anthropicModels =
    ctx.provider_recommended_models.anthropic ?? [];
  const openaiModels = ctx.provider_recommended_models.openai ?? [];
  for (const entry of ctx.agent_inventory) {
    const existing = existingAgents[entry.name];
    const existingProvider =
      existing?.provider === "anthropic" || existing?.provider === "openai"
        ? existing.provider
        : null;
    if (existingProvider) {
      agents[entry.name] = {
        provider: existingProvider,
        model: existing?.model ?? "",
      };
      continue;
    }
    // No existing config for this agent — fall back to defaults.
    if (defaultProvider === "") {
      // Form has no enabled provider yet; the agent rows will become
      // valid as soon as the user enables one (agent dispatcher in
      // updateProvider re-derives). Use anthropic as a placeholder
      // shape so the row object always has the right type.
      const placeholderModels = anthropicModels.length
        ? anthropicModels
        : openaiModels;
      agents[entry.name] = {
        provider: anthropicModels.length ? "anthropic" : "openai",
        model: recommendedModelFor(
          ctx,
          anthropicModels.length ? "anthropic" : "openai",
          entry.name,
          placeholderModels,
        ),
      };
    } else {
      const availableModels =
        defaultProvider === "anthropic" ? anthropicModels : openaiModels;
      agents[entry.name] = {
        provider: defaultProvider,
        model: recommendedModelFor(
          ctx,
          defaultProvider,
          entry.name,
          availableModels,
        ),
      };
    }
  }

  return {
    target_branch: target,
    fork_ref: fork,
    project_context: project,
    anthropic,
    openai,
    github_token: "",
    default_provider: defaultProvider,
    agents,
    threshold_auto:
      thresholds.auto_merge_confidence === undefined
        ? ""
        : String(thresholds.auto_merge_confidence),
    threshold_low:
      thresholds.risk_score_low === undefined
        ? ""
        : String(thresholds.risk_score_low),
    threshold_high:
      thresholds.risk_score_high === undefined
        ? ""
        : String(thresholds.risk_score_high),
    request_timeout_seconds:
      typeof summary.request_timeout_seconds === "number"
        ? String(summary.request_timeout_seconds)
        : "",
    dry_run: false,
    workflow: "",
    init_forks_profile: false,
  };
}

function buildThresholds(form: FormState): ThresholdsPayload | null {
  const parse = (s: string): number | null => {
    if (s.trim() === "") return null;
    const n = Number(s);
    return Number.isFinite(n) ? n : null;
  };
  const result: ThresholdsPayload = {};
  const auto = parse(form.threshold_auto);
  const low = parse(form.threshold_low);
  const high = parse(form.threshold_high);
  if (auto !== null) result.auto_merge_confidence = auto;
  if (low !== null) result.risk_score_low = low;
  if (high !== null) result.risk_score_high = high;
  return Object.keys(result).length === 0 ? null : result;
}

function parseTimeout(s: string): number | null {
  if (s.trim() === "") return null;
  const n = parseInt(s, 10);
  return Number.isFinite(n) && n >= 5 ? n : null;
}

function parseModels(text: string): string[] {
  // Newline-primary, comma-secondary so users can paste either form.
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of text.split(/[\n,]/)) {
    const trimmed = raw.trim();
    if (!trimmed) continue;
    if (seen.has(trimmed)) continue;
    seen.add(trimmed);
    out.push(trimmed);
  }
  return out;
}

function buildPayload(form: FormState): SetupPayload {
  // Every agent ships with an explicit (provider, model) — there's no
  // "inherit" path on the wire any more. The backend resolver still
  // accepts a missing agent and falls back to default_provider for
  // backwards compatibility, but the new UI always populates.
  const agent_choices: Record<string, AgentChoice> = {};
  for (const [name, row] of Object.entries(form.agents)) {
    if (!row.model) continue; // defensive: don't ship rows with empty model
    agent_choices[name] = { provider: row.provider, model: row.model };
  }
  return {
    target_branch: form.target_branch.trim(),
    fork_ref: form.fork_ref.trim(),
    project_context: form.project_context,
    anthropic: {
      enabled: form.anthropic.enabled,
      api_key: form.anthropic.api_key.trim(),
      base_url: form.anthropic.base_url.trim() || null,
      models: parseModels(form.anthropic.models_text),
    },
    openai: {
      enabled: form.openai.enabled,
      api_key: form.openai.api_key.trim(),
      base_url: form.openai.base_url.trim() || null,
      models: parseModels(form.openai.models_text),
    },
    github_token: form.github_token.trim(),
    default_provider:
      form.default_provider === "" ? null : form.default_provider,
    agent_choices,
    thresholds: buildThresholds(form),
    request_timeout_seconds: parseTimeout(form.request_timeout_seconds),
    dry_run: form.dry_run,
    workflow: form.workflow.trim() === "" ? null : form.workflow,
    init_forks_profile: form.init_forks_profile,
  };
}

function modelsFor(form: FormState, provider: ProviderName): string[] {
  if (provider === "anthropic") return parseModels(form.anthropic.models_text);
  return parseModels(form.openai.models_text);
}

function validate(form: FormState, ctx: SetupContext): string | null {
  if (!form.target_branch.trim()) return "Target branch is required.";
  if (!form.fork_ref.trim()) return "Fork ref is required.";

  // Pristine slate — no global env, no project config, no project .env.
  const pristineDevice =
    !ctx.has_global_env && !ctx.has_existing_config && !ctx.has_project_env;

  // At least one provider must be enabled AND have a key (in the form
  // OR already on disk per ctx) AND list ≥1 model.
  const enabledList: ProviderName[] = [];
  const providerOk = (
    p: ProviderName,
    state: ProviderFormState,
    hintMasked: string,
  ): string | null => {
    if (!state.enabled) return null;
    if (!state.api_key.trim() && !hintMasked) {
      return `${p} is enabled but no API key was supplied and none is on disk.`;
    }
    if (parseModels(state.models_text).length === 0) {
      return `${p} is enabled but its models list is empty.`;
    }
    enabledList.push(p);
    return null;
  };
  const anthropicErr = providerOk(
    "anthropic",
    form.anthropic,
    pristineDevice ? "" : ctx.anthropic_key_hint.masked,
  );
  if (anthropicErr) return anthropicErr;
  const openaiErr = providerOk(
    "openai",
    form.openai,
    pristineDevice ? "" : ctx.openai_key_hint.masked,
  );
  if (openaiErr) return openaiErr;
  if (enabledList.length === 0) {
    return "At least one provider (Anthropic or OpenAI) must be enabled.";
  }
  if (enabledList.length > 1 && !form.default_provider) {
    return "Pick a default provider when both Anthropic and OpenAI are enabled.";
  }
  if (
    form.default_provider !== "" &&
    !enabledList.includes(form.default_provider)
  ) {
    return `Default provider "${form.default_provider}" is not enabled.`;
  }
  for (const [name, row] of Object.entries(form.agents)) {
    if (!enabledList.includes(row.provider)) {
      return `Agent "${name}" is assigned to ${row.provider}, which is not enabled.`;
    }
    const available = modelsFor(form, row.provider);
    if (!row.model || !available.includes(row.model)) {
      return `Agent "${name}" must pick a model from ${row.provider}.models.`;
    }
  }
  for (const [key, raw] of [
    ["threshold_auto", form.threshold_auto] as const,
    ["threshold_low", form.threshold_low] as const,
    ["threshold_high", form.threshold_high] as const,
  ]) {
    if (raw.trim() === "") continue;
    const n = Number(raw);
    if (!Number.isFinite(n) || n < 0 || n > 1) {
      return `${key} must be between 0.0 and 1.0`;
    }
  }
  if (form.request_timeout_seconds.trim() !== "") {
    const n = parseInt(form.request_timeout_seconds, 10);
    if (!Number.isFinite(n) || n < 5) {
      return "request_timeout_seconds must be an integer ≥ 5";
    }
  }
  return null;
}

interface ProviderSectionProps {
  provider: ProviderName;
  state: ProviderFormState;
  hint: { masked: string; source: string };
  recommendedModels: string[];
  onChange: (next: ProviderFormState) => void;
  disabled: boolean;
}

function ProviderSection({
  provider,
  state,
  hint,
  recommendedModels,
  onChange,
  disabled,
}: ProviderSectionProps): JSX.Element {
  const update = useCallback(
    <K extends keyof ProviderFormState>(
      key: K,
      value: ProviderFormState[K],
    ) => {
      onChange({ ...state, [key]: value });
    },
    [onChange, state],
  );

  const parsedCount = useMemo(
    () => parseModels(state.models_text).length,
    [state.models_text],
  );
  const restoreRecommended = useCallback(() => {
    update("models_text", recommendedModels.join("\n"));
  }, [recommendedModels, update]);

  return (
    <Card
      title={
        <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <input
            type="checkbox"
            checked={state.enabled}
            disabled={disabled}
            onChange={(e) => update("enabled", e.target.checked)}
          />
          {`› ${PROVIDER_LABEL[provider].toUpperCase()}`}
        </span>
      }
      hint={
        hint.masked
          ? `existing key: ${hint.masked} (from ${hint.source || "?"})`
          : "no key on disk"
      }
    >
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 12,
          opacity: state.enabled ? 1 : 0.5,
        }}
      >
        <div>
          <label style={labelStyle} htmlFor={`${provider}_api_key`}>
            api key
          </label>
          <input
            id={`${provider}_api_key`}
            style={inputStyle}
            type="password"
            autoComplete="off"
            value={state.api_key}
            disabled={!state.enabled}
            onChange={(e) => update("api_key", e.target.value)}
            placeholder={
              hint.masked
                ? `${hint.masked} (leave blank to keep)`
                : "paste API key"
            }
          />
        </div>
        <div>
          <label style={labelStyle} htmlFor={`${provider}_base_url`}>
            base url (optional)
          </label>
          <input
            id={`${provider}_base_url`}
            style={inputStyle}
            value={state.base_url}
            disabled={!state.enabled}
            onChange={(e) => update("base_url", e.target.value)}
            placeholder="https://api.anthropic.com or gateway URL"
          />
        </div>
        <div>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "baseline",
            }}
          >
            <label style={labelStyle} htmlFor={`${provider}_models`}>
              available models ({parsedCount} listed)
            </label>
            <button
              type="button"
              className="btn ghost"
              style={{ fontSize: 9, padding: "2px 6px" }}
              disabled={!state.enabled}
              onClick={restoreRecommended}
              title="replace the textarea with the built-in recommended list"
            >
              restore recommended
            </button>
          </div>
          <textarea
            id={`${provider}_models`}
            data-testid={`${provider}_models`}
            style={{
              ...inputStyle,
              minHeight: 84,
              resize: "vertical",
              fontFamily: "var(--mono)",
            }}
            value={state.models_text}
            disabled={!state.enabled}
            onChange={(e) => update("models_text", e.target.value)}
            placeholder={
              "one model per line, e.g.\nclaude-opus-4-7\nclaude-haiku-4-5-20251001"
            }
          />
          <div className="dim" style={{ fontSize: 10, marginTop: 4 }}>
            AGENT OVERRIDES picks from this list. First entry is the
            default for agents without an override.
          </div>
        </div>
      </div>
    </Card>
  );
}

export function Setup({ clientRef }: Props): JSX.Element {
  const { context, status, error, ready, submit, refresh } = useSetup(clientRef);
  const [form, setForm] = useState<FormState | null>(null);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [agentsOpen, setAgentsOpen] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  useEffect(() => {
    if (context && form === null) {
      setForm(deriveDefaults(context));
    }
  }, [context, form]);

  const updateField = useCallback(
    <K extends keyof FormState>(key: K, value: FormState[K]) => {
      setForm((prev) => (prev ? { ...prev, [key]: value } : prev));
    },
    [],
  );

  const updateProvider = useCallback(
    (which: "anthropic" | "openai", next: ProviderFormState) => {
      setForm((prev) => {
        if (!prev) return prev;
        const merged: FormState = { ...prev, [which]: next };
        // When the user disables a provider that was previously the
        // default, drop the default so validation forces a fresh
        // choice; auto-pick if the other one is the sole survivor.
        if (!next.enabled && merged.default_provider === which) {
          const other = which === "anthropic" ? "openai" : "anthropic";
          merged.default_provider = merged[other].enabled ? other : "";
        }
        if (next.enabled && merged.default_provider === "") {
          merged.default_provider = which;
        }
        // Repoint any agent rows assigned to the now-disabled provider
        // so the form doesn't fall into an unsubmittable state. We
        // only migrate when there's a single surviving enabled
        // provider; if both are still enabled the rows are fine.
        if (!next.enabled) {
          const other: ProviderName = which === "anthropic" ? "openai" : "anthropic";
          const otherEnabled = merged[other].enabled;
          if (otherEnabled) {
            const otherModels = parseModels(merged[other].models_text);
            if (otherModels.length > 0 && context) {
              const repointed: Record<string, AgentRowState> = {};
              for (const [name, row] of Object.entries(merged.agents)) {
                if (row.provider === which) {
                  repointed[name] = {
                    provider: other,
                    model: recommendedModelFor(
                      context,
                      other,
                      name,
                      otherModels,
                    ),
                  };
                } else {
                  repointed[name] = row;
                }
              }
              merged.agents = repointed;
            }
          }
        }
        // Re-point agent rows whose current model has been removed from
        // this provider's models_text. Without this the form silently
        // rots: textarea edit leaves agents[name].model pointing at a
        // value that no longer appears in the list, surfacing later as
        // "must pick a model from <provider>.models" at submit time.
        if (next.enabled && context) {
          const newModels = parseModels(next.models_text);
          const survivors = new Set(newModels);
          let touched = false;
          const fixed: Record<string, AgentRowState> = {};
          for (const [name, row] of Object.entries(merged.agents)) {
            if (row.provider === which && !survivors.has(row.model)) {
              touched = true;
              fixed[name] = {
                provider: which,
                model:
                  newModels.length > 0
                    ? recommendedModelFor(context, which, name, newModels)
                    : "",
              };
            } else {
              fixed[name] = row;
            }
          }
          if (touched) merged.agents = fixed;
        }
        return merged;
      });
    },
    [context],
  );

  // Re-derive every agent row from current providers / default — used
  // by the AGENT OVERRIDES "RESET TO DEFAULTS" button so the user can
  // undo manual edits in one click.
  const resetAgentsToDefaults = useCallback(() => {
    if (!context) return;
    setForm((prev) => {
      if (!prev) return prev;
      if (prev.default_provider === "") return prev;
      const provider = prev.default_provider;
      const available = parseModels(
        provider === "anthropic"
          ? prev.anthropic.models_text
          : prev.openai.models_text,
      );
      if (available.length === 0) return prev;
      const fresh: Record<string, AgentRowState> = {};
      for (const entry of context.agent_inventory) {
        fresh[entry.name] = {
          provider,
          model: recommendedModelFor(context, provider, entry.name, available),
        };
      }
      return { ...prev, agents: fresh };
    });
  }, [context]);

  const updateAgent = useCallback((name: string, next: AgentRowState) => {
    setForm((prev) =>
      prev ? { ...prev, agents: { ...prev.agents, [name]: next } } : prev,
    );
  }, []);

  const handleSubmit = useCallback(
    (e: FormEvent<HTMLFormElement>) => {
      e.preventDefault();
      if (!context || !form) return;
      const validationError = validate(form, context);
      if (validationError) {
        setLocalError(validationError);
        return;
      }
      setLocalError(null);
      submit(buildPayload(form));
    },
    [context, form, submit],
  );

  const showForksProfile = useMemo(
    () =>
      !!context &&
      context.fork_divergence_count >= context.forks_profile_threshold,
    [context],
  );

  if (!context || !form) {
    return (
      <div style={containerStyle}>
        <Card title="› SETUP">
          <div className="dim" style={{ fontSize: 12 }}>
            Waiting for server to publish setup context…
          </div>
        </Card>
      </div>
    );
  }

  if (status === "ready" && ready) {
    return (
      <div style={containerStyle}>
        <Card title="› CONFIG SAVED" hint={<Pill tone="green">READY</Pill>}>
          <div style={{ fontSize: 12, lineHeight: 1.6 }}>
            <div>
              Wrote{" "}
              <span style={{ color: "var(--accent)", fontFamily: "var(--mono)" }}>
                {ready.config_path}
              </span>
            </div>
            <div className="dim" style={{ marginTop: 8 }}>
              Starting orchestrator… the dashboard will open as soon as
              the first phase reports in.
            </div>
          </div>
        </Card>
      </div>
    );
  }

  const submitting = status === "submitting";
  const headerHint = context.has_existing_config
    ? "reconfigure existing .merge/config.yaml"
    : "first-run setup — no .merge/config.yaml yet";

  const enabledProviders: ProviderName[] = [];
  if (form.anthropic.enabled) enabledProviders.push("anthropic");
  if (form.openai.enabled) enabledProviders.push("openai");
  const needsDefaultPick = enabledProviders.length > 1;

  // Pristine slate — see deriveDefaults.
  const pristineDevice =
    !context.has_global_env &&
    !context.has_existing_config &&
    !context.has_project_env;
  const blankHint = { masked: "", source: "" };

  return (
    <form
      onSubmit={handleSubmit}
      style={{ ...containerStyle, display: "flex", flexDirection: "column", gap: 16 }}
    >
      <Card title="› MERGE TARGET" hint={headerHint}>
        <div style={rowStyle}>
          <div>
            <label style={labelStyle} htmlFor="target_branch">
              target branch (upstream)
            </label>
            <input
              id="target_branch"
              style={inputStyle}
              value={form.target_branch}
              onChange={(e: ChangeEvent<HTMLInputElement>) =>
                updateField("target_branch", e.target.value)
              }
              placeholder={context.suggested_target}
            />
            <div className="dim" style={{ fontSize: 10, marginTop: 4 }}>
              autodetected: {context.suggested_target}
            </div>
          </div>
          <div>
            <label style={labelStyle} htmlFor="fork_ref">
              fork ref (current branch)
            </label>
            <input
              id="fork_ref"
              style={inputStyle}
              value={form.fork_ref}
              onChange={(e: ChangeEvent<HTMLInputElement>) =>
                updateField("fork_ref", e.target.value)
              }
              placeholder={context.current_branch}
            />
            <div className="dim" style={{ fontSize: 10, marginTop: 4 }}>
              autodetected: {context.current_branch}
            </div>
          </div>
        </div>
        <div style={{ marginTop: 12 }}>
          <label style={labelStyle} htmlFor="project_context">
            project description (optional — helps the planner)
          </label>
          <textarea
            id="project_context"
            style={{ ...inputStyle, minHeight: 72, resize: "vertical" }}
            value={form.project_context}
            onChange={(e: ChangeEvent<HTMLTextAreaElement>) =>
              updateField("project_context", e.target.value)
            }
          />
        </div>
        <button
          type="button"
          className="btn ghost"
          style={{ marginTop: 12 }}
          onClick={refresh}
          disabled={submitting}
        >
          ⟳ re-detect git state
        </button>
      </Card>

      <ProviderSection
        provider="anthropic"
        state={form.anthropic}
        hint={
          pristineDevice
            ? blankHint
            : {
                masked: context.anthropic_key_hint.masked,
                source: context.anthropic_key_hint.source,
              }
        }
        recommendedModels={context.provider_recommended_models.anthropic ?? []}
        onChange={(next) => updateProvider("anthropic", next)}
        disabled={submitting}
      />

      <ProviderSection
        provider="openai"
        state={form.openai}
        hint={
          pristineDevice
            ? blankHint
            : {
                masked: context.openai_key_hint.masked,
                source: context.openai_key_hint.source,
              }
        }
        recommendedModels={context.provider_recommended_models.openai ?? []}
        onChange={(next) => updateProvider("openai", next)}
        disabled={submitting}
      />

      {needsDefaultPick && (
        <Card title="› DEFAULT PROVIDER" hint="agents inherit unless overridden below">
          <div style={{ display: "flex", gap: 16, fontSize: 12 }}>
            {enabledProviders.map((p) => (
              <label
                key={p}
                style={{ display: "flex", alignItems: "center", gap: 6 }}
              >
                <input
                  type="radio"
                  name="default_provider"
                  checked={form.default_provider === p}
                  onChange={() => updateField("default_provider", p)}
                />
                {PROVIDER_LABEL[p]}
              </label>
            ))}
          </div>
        </Card>
      )}

      <Card
        title={
          <span style={{ cursor: "pointer" }} onClick={() => setAgentsOpen((v) => !v)}>
            › AGENT OVERRIDES {agentsOpen ? "▾" : "▸"}
          </span>
        }
        hint={
          agentsOpen ? (
            <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span>per-agent provider + model</span>
              <button
                type="button"
                className="btn ghost"
                style={{ fontSize: 9, padding: "2px 6px" }}
                onClick={resetAgentsToDefaults}
                disabled={submitting}
                title="restore every row to the recommended (provider, model) defaults"
              >
                reset to defaults
              </button>
            </span>
          ) : (
            "click to expand"
          )
        }
      >
        {agentsOpen && (
          <div
            style={{ display: "flex", flexDirection: "column", gap: 10 }}
            data-testid="agent-overrides"
          >
            {context.agent_inventory.map((entry) => {
              const row = form.agents[entry.name];
              if (!row) return null;
              // Both dropdowns are always populated. Provider list =
              // enabled providers (no "(default)" placeholder). Model
              // list = whatever models the chosen provider currently
              // has configured.
              const availableModels = modelsFor(form, row.provider);
              return (
                <div
                  key={entry.name}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "150px 110px 1fr",
                    gap: 10,
                    alignItems: "center",
                  }}
                >
                  <div>
                    <div style={{ fontFamily: "var(--mono)", fontSize: 11 }}>
                      {entry.name}
                    </div>
                    <div className="dim" style={{ fontSize: 9 }}>
                      {entry.blurb}
                    </div>
                  </div>
                  <select
                    style={inputStyle}
                    value={row.provider}
                    onChange={(e) => {
                      const provider = e.target.value as ProviderName;
                      // When switching provider, snap the model to
                      // the (provider, agent) recommendation so the
                      // row stays consistent without an extra click.
                      const nextModels = modelsFor(form, provider);
                      const nextModel = recommendedModelFor(
                        context,
                        provider,
                        entry.name,
                        nextModels,
                      );
                      updateAgent(entry.name, { provider, model: nextModel });
                    }}
                  >
                    {enabledProviders.map((p) => (
                      <option key={p} value={p}>
                        {PROVIDER_LABEL[p]}
                      </option>
                    ))}
                  </select>
                  <select
                    style={inputStyle}
                    value={row.model}
                    onChange={(e) =>
                      updateAgent(entry.name, {
                        ...row,
                        model: e.target.value,
                      })
                    }
                  >
                    {availableModels.length === 0 && (
                      <option value="">(provider has no models)</option>
                    )}
                    {availableModels.map((m) => (
                      <option key={m} value={m}>
                        {m}
                      </option>
                    ))}
                  </select>
                </div>
              );
            })}
          </div>
        )}
      </Card>

      <Card
        title={
          <span style={{ cursor: "pointer" }} onClick={() => setAdvancedOpen((v) => !v)}>
            › ADVANCED {advancedOpen ? "▾" : "▸"}
          </span>
        }
        hint={
          advancedOpen
            ? "github / thresholds / timeout / dry-run / workflow"
            : "click to expand"
        }
      >
        {advancedOpen && (
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <div>
              <label style={labelStyle} htmlFor="github_token">
                github token (optional)
              </label>
              <input
                id="github_token"
                style={inputStyle}
                type="password"
                autoComplete="off"
                value={form.github_token}
                onChange={(e) => updateField("github_token", e.target.value)}
                placeholder={
                  context.github_token_hint.masked
                    ? `${context.github_token_hint.masked} (leave blank to keep)`
                    : "enables PR / issue lookups"
                }
              />
            </div>
            <div style={rowStyle}>
              <label
                style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12 }}
              >
                <input
                  type="checkbox"
                  checked={form.dry_run}
                  onChange={(e) => updateField("dry_run", e.target.checked)}
                />
                dry-run (analyse, do not merge)
              </label>
              <div>
                <label style={labelStyle} htmlFor="workflow">
                  workflow preset
                </label>
                <select
                  id="workflow"
                  style={inputStyle}
                  value={form.workflow}
                  onChange={(e) => updateField("workflow", e.target.value)}
                >
                  {WORKFLOW_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <div style={{ ...rowStyle, gridTemplateColumns: "1fr 1fr 1fr" }}>
              <div>
                <label style={labelStyle} htmlFor="t_auto">
                  auto_merge_confidence (0–1)
                </label>
                <input
                  id="t_auto"
                  style={inputStyle}
                  value={form.threshold_auto}
                  onChange={(e) => updateField("threshold_auto", e.target.value)}
                  placeholder="0.85"
                />
              </div>
              <div>
                <label style={labelStyle} htmlFor="t_low">
                  risk_score_low
                </label>
                <input
                  id="t_low"
                  style={inputStyle}
                  value={form.threshold_low}
                  onChange={(e) => updateField("threshold_low", e.target.value)}
                  placeholder="0.30"
                />
              </div>
              <div>
                <label style={labelStyle} htmlFor="t_high">
                  risk_score_high
                </label>
                <input
                  id="t_high"
                  style={inputStyle}
                  value={form.threshold_high}
                  onChange={(e) => updateField("threshold_high", e.target.value)}
                  placeholder="0.60"
                />
              </div>
            </div>
            <div>
              <label style={labelStyle} htmlFor="request_timeout_seconds">
                request_timeout_seconds (≥ 5, default 300)
              </label>
              <input
                id="request_timeout_seconds"
                style={{ ...inputStyle, width: 160 }}
                type="number"
                min={5}
                step={1}
                value={form.request_timeout_seconds}
                onChange={(e) =>
                  updateField("request_timeout_seconds", e.target.value)
                }
                placeholder="300"
              />
              <div className="dim" style={{ fontSize: 10, marginTop: 4 }}>
                per-request HTTP timeout in seconds passed to the LLM SDK
              </div>
            </div>
            {showForksProfile && (
              <label
                style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12 }}
              >
                <input
                  type="checkbox"
                  checked={form.init_forks_profile}
                  onChange={(e) =>
                    updateField("init_forks_profile", e.target.checked)
                  }
                />
                draft .merge/forks-profile.yaml (
                {context.fork_divergence_count} files deleted vs upstream)
              </label>
            )}
          </div>
        )}
      </Card>

      {(localError || error) && (
        <Card title="› ERROR" accent={false}>
          <div style={{ color: "var(--red)", fontSize: 12 }}>
            {localError ?? error?.reason}
            {error?.details && (
              <div className="dim" style={{ marginTop: 4 }}>
                {error.details}
              </div>
            )}
          </div>
        </Card>
      )}

      <div style={{ display: "flex", justifyContent: "flex-end", gap: 12 }}>
        <button
          type="submit"
          className="btn primary"
          disabled={submitting}
          aria-busy={submitting}
        >
          {submitting ? "SAVING…" : "▶ SAVE & START"}
        </button>
      </div>
    </form>
  );
}
