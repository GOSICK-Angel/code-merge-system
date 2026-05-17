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
  ApiKeyHint,
  SetupContext,
  SetupPayload,
  ThresholdsPayload,
} from "../types/state";

interface Props {
  clientRef: MutableRefObject<WsClient | null>;
}

interface FormState {
  target_branch: string;
  fork_ref: string;
  project_context: string;
  api_keys: Record<string, string>;
  threshold_auto: string;
  threshold_low: string;
  threshold_high: string;
  dry_run: boolean;
  workflow: string;
  init_forks_profile: boolean;
}

const API_KEY_FIELDS: Array<{
  name: string;
  label: string;
  required: boolean;
  help: string;
}> = [
  {
    name: "ANTHROPIC_API_KEY",
    label: "ANTHROPIC_API_KEY",
    required: true,
    help: "planner / conflict_analyst / judge / human_interface",
  },
  {
    name: "OPENAI_API_KEY",
    label: "OPENAI_API_KEY",
    required: true,
    help: "planner_judge / executor",
  },
  {
    name: "GITHUB_TOKEN",
    label: "GITHUB_TOKEN",
    required: false,
    help: "optional — enables PR / issue lookups",
  },
];

const WORKFLOW_OPTIONS = [
  { value: "", label: "(default)" },
  { value: "standard", label: "standard" },
  { value: "careful", label: "careful" },
  { value: "fast", label: "fast" },
  { value: "analysis-only", label: "analysis-only" },
];

const FORM_DEFAULTS: FormState = {
  target_branch: "",
  fork_ref: "",
  project_context: "",
  api_keys: { ANTHROPIC_API_KEY: "", OPENAI_API_KEY: "", GITHUB_TOKEN: "" },
  threshold_auto: "",
  threshold_low: "",
  threshold_high: "",
  dry_run: false,
  workflow: "",
  init_forks_profile: false,
};

const inputStyle: CSSProperties = {
  width: "100%",
  background: "var(--bg-2)",
  border: "1px solid var(--line)",
  color: "var(--fg-0)",
  padding: "6px 10px",
  fontFamily: "var(--mono)",
  fontSize: 12,
  outline: "none",
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
    typeof summary.project_context === "string"
      ? summary.project_context
      : "";
  const thresholds =
    summary.thresholds && typeof summary.thresholds === "object"
      ? (summary.thresholds as Record<string, number | undefined>)
      : {};
  return {
    ...FORM_DEFAULTS,
    target_branch: target,
    fork_ref: fork,
    project_context: project,
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
  };
}

function findHint(
  hints: ApiKeyHint[] | undefined,
  name: string,
): ApiKeyHint | null {
  return hints?.find((h) => h.name === name) ?? null;
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

function buildPayload(form: FormState): SetupPayload {
  const apiKeys: Record<string, string> = {};
  for (const [k, v] of Object.entries(form.api_keys)) {
    if (v.trim()) apiKeys[k] = v.trim();
  }
  return {
    target_branch: form.target_branch.trim(),
    fork_ref: form.fork_ref.trim(),
    project_context: form.project_context,
    api_keys: apiKeys,
    thresholds: buildThresholds(form),
    dry_run: form.dry_run,
    workflow: form.workflow.trim() === "" ? null : form.workflow,
    init_forks_profile: form.init_forks_profile,
  };
}

function validate(form: FormState, ctx: SetupContext): string | null {
  if (!form.target_branch.trim()) return "Target branch is required.";
  if (!form.fork_ref.trim()) return "Fork ref is required.";
  for (const field of API_KEY_FIELDS) {
    if (!field.required) continue;
    const supplied = form.api_keys[field.name]?.trim();
    const onDisk = findHint(ctx.api_key_hints, field.name)?.masked ?? "";
    if (!supplied && !onDisk) {
      return `${field.name} is required (no existing value on disk).`;
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
  return null;
}

export function Setup({ clientRef }: Props): JSX.Element {
  const { context, status, error, ready, submit, refresh } = useSetup(clientRef);
  const [form, setForm] = useState<FormState>(FORM_DEFAULTS);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const [initialised, setInitialised] = useState(false);

  // Seed the form the first time a context arrives. After that, treat
  // ``context`` as a static snapshot — re-seeding on every refresh would
  // discard user edits each time the divergence count refreshes.
  useEffect(() => {
    if (context && !initialised) {
      setForm(deriveDefaults(context));
      setInitialised(true);
    }
  }, [context, initialised]);

  const updateField = useCallback(
    <K extends keyof FormState>(key: K, value: FormState[K]) => {
      setForm((prev) => ({ ...prev, [key]: value }));
    },
    [],
  );

  const updateApiKey = useCallback((name: string, value: string) => {
    setForm((prev) => ({
      ...prev,
      api_keys: { ...prev.api_keys, [name]: value },
    }));
  }, []);

  const handleSubmit = useCallback(
    (e: FormEvent<HTMLFormElement>) => {
      e.preventDefault();
      if (!context) return;
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

  if (!context) {
    return (
      <div style={{ padding: 32 }}>
        <Card title="› SETUP">
          <div className="dim" style={{ fontSize: 12 }}>
            Waiting for server to publish setup context…
          </div>
        </Card>
      </div>
    );
  }

  if (status === "ready" && ready) {
    // After a successful submit we sit here until the server pushes the
    // first state_snapshot, at which point runStore.applySnapshot flips
    // mode → "run" and routing leaves this view.
    return (
      <div style={{ padding: 32 }}>
        <Card
          title="› CONFIG SAVED"
          hint={<Pill tone="green">READY</Pill>}
        >
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

  return (
    <form
      onSubmit={handleSubmit}
      style={{ padding: 24, display: "flex", flexDirection: "column", gap: 16 }}
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

      <Card title="› API KEYS">
        <div
          style={{ display: "flex", flexDirection: "column", gap: 12 }}
          data-testid="api-key-fields"
        >
          {API_KEY_FIELDS.map((field) => {
            const hint = findHint(context.api_key_hints, field.name);
            const placeholder = hint?.masked
              ? `${hint.masked} (from ${hint.source || "?"} — leave blank to keep)`
              : field.required
                ? "required"
                : "optional";
            return (
              <div key={field.name}>
                <label style={labelStyle} htmlFor={`key_${field.name}`}>
                  {field.label}
                  {field.required && (
                    <span style={{ color: "var(--red)", marginLeft: 4 }}>
                      *
                    </span>
                  )}
                </label>
                <input
                  id={`key_${field.name}`}
                  style={inputStyle}
                  type="password"
                  autoComplete="off"
                  value={form.api_keys[field.name] ?? ""}
                  onChange={(e) => updateApiKey(field.name, e.target.value)}
                  placeholder={placeholder}
                />
                <div className="dim" style={{ fontSize: 10, marginTop: 4 }}>
                  {field.help}
                </div>
              </div>
            );
          })}
        </div>
      </Card>

      <Card
        title={
          <span
            style={{ cursor: "pointer" }}
            onClick={() => setAdvancedOpen((v) => !v)}
          >
            › ADVANCED {advancedOpen ? "▾" : "▸"}
          </span>
        }
        hint={
          advancedOpen
            ? "thresholds / dry-run / workflow"
            : "click to expand"
        }
      >
        {advancedOpen && (
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <div style={rowStyle}>
              <label
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  fontSize: 12,
                }}
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
                  onChange={(e) =>
                    updateField("threshold_auto", e.target.value)
                  }
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
                  onChange={(e) =>
                    updateField("threshold_low", e.target.value)
                  }
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
                  onChange={(e) =>
                    updateField("threshold_high", e.target.value)
                  }
                  placeholder="0.60"
                />
              </div>
            </div>

            {showForksProfile && (
              <label
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  fontSize: 12,
                }}
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
