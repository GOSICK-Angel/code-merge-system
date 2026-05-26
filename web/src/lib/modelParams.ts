// Per-model LLM tuning for the Setup wizard. Pure helpers (no React) so the
// recommended-default / reconcile logic can be unit-tested in isolation.
//
// The form keeps params as strings (raw input values) keyed by model name;
// they are parsed to numbers only when building the payload.

export interface ModelParamsForm {
  max_tokens: string;
  temperature: string;
  max_retries: string;
}

/** Recommended per-model defaults, by model-family prefix. Mirrors
 * ``recommended_model_params`` in ``src/cli/commands/setup.py`` so a model
 * the user never edits resolves to the same values on both ends. */
export function recommendedModelParams(model: string): ModelParamsForm {
  const m = model.toLowerCase();
  // OpenAI reasoning-class models need a large completion budget.
  if (
    m.startsWith("gpt-5") ||
    m.startsWith("o1") ||
    m.startsWith("o3") ||
    m.startsWith("o4")
  ) {
    return { max_tokens: "32768", temperature: "0.2", max_retries: "3" };
  }
  if (m.includes("haiku")) {
    return { max_tokens: "4096", temperature: "0.2", max_retries: "3" };
  }
  // Claude opus/sonnet, gpt-4o, and everything else.
  return { max_tokens: "8192", temperature: "0.2", max_retries: "3" };
}

/** Seed per-model params from an existing config's ``agents`` block (the
 * reconfigure round-trip): the first agent using each model wins, and any
 * field the old block omitted is back-filled with the recommended default. */
export function modelParamsFromAgents(
  agents: Record<string, unknown>,
): Record<string, ModelParamsForm> {
  const out: Record<string, ModelParamsForm> = {};
  for (const spec of Object.values(agents)) {
    if (typeof spec !== "object" || spec === null) continue;
    const s = spec as Record<string, unknown>;
    const model = typeof s.model === "string" ? s.model : "";
    if (!model || out[model]) continue;
    const rec = recommendedModelParams(model);
    out[model] = {
      max_tokens: s.max_tokens != null ? String(s.max_tokens) : rec.max_tokens,
      temperature:
        s.temperature != null ? String(s.temperature) : rec.temperature,
      max_retries:
        s.max_retries != null ? String(s.max_retries) : rec.max_retries,
    };
  }
  return out;
}

/** Keep the per-model param map in lockstep with the configured models:
 * preserve existing edits, add a recommended default for any new model, and
 * drop models that are no longer configured. Order follows
 * ``configuredModels`` (deduped). */
export function reconcileModelParams(
  configuredModels: string[],
  existing: Record<string, ModelParamsForm>,
): Record<string, ModelParamsForm> {
  const out: Record<string, ModelParamsForm> = {};
  for (const model of configuredModels) {
    if (out[model]) continue;
    out[model] = existing[model] ?? recommendedModelParams(model);
  }
  return out;
}
