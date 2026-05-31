import { useState, type CSSProperties } from "react";
import type { ConfigFieldNode } from "../types/state";
import {
  defaultFlatValue,
  isEditableLeaf,
  isLeafModified,
  MODEL_PICKER_FIELDS,
  PROVIDER_PICKER_FIELDS,
  type FlatValues,
  type FormLeafValue,
  type ProviderModels,
} from "../lib/configForm";

interface Props {
  nodes: ConfigFieldNode[];
  flat: FlatValues;
  onChange: (path: string, value: FormLeafValue) => void;
  // Per-path YAML parse errors for `yaml` leaves (keyed by node.path).
  errors?: Record<string, string>;
  // When true (search active), object groups render expanded regardless of
  // their local collapse state.
  forceOpen?: boolean;
  depth?: number;
  // Provider → models from the first-screen form. When present, the
  // model-picker leaves (``llm.model`` / ``llm.fallback_model``) render as
  // dropdowns sourced from the selected provider's configured models
  // instead of free-text inputs.
  providerModels?: ProviderModels;
  // Providers enabled in the first-screen form — restricts the provider
  // picker leaves (``llm.provider``) to what the user actually configured.
  enabledProviders?: string[];
}

function ResetButton({ onReset }: { onReset: () => void }): JSX.Element {
  return (
    <button
      type="button"
      className="btn ghost"
      style={{ fontSize: 9, padding: "1px 6px" }}
      onClick={onReset}
      title="reset this field to its default"
    >
      ↺ reset
    </button>
  );
}

const inputStyle: CSSProperties = {
  width: "100%",
  background: "var(--bg-2)",
  border: "1px solid var(--line)",
  color: "var(--fg-0)",
  padding: "5px 8px",
  fontFamily: "var(--mono)",
  fontSize: 12,
  outline: "none",
  boxSizing: "border-box",
};

const labelStyle: CSSProperties = {
  display: "block",
  fontSize: 10,
  letterSpacing: "0.06em",
  color: "var(--fg-2)",
  marginBottom: 3,
  fontFamily: "var(--mono)",
};

const descStyle: CSSProperties = {
  fontSize: 9,
  color: "var(--fg-3, var(--fg-2))",
  marginTop: 3,
  lineHeight: 1.4,
};

/** True when a node has at least one non-curated, renderable descendant —
 * used to hide fully-curated subtrees and empty groups. */
function hasRenderable(node: ConfigFieldNode): boolean {
  if (node.curated) return false;
  if (node.kind === "object") return node.children.some(hasRenderable);
  return true; // editable leaf or yaml placeholder
}

/** Dropdown sourced from the first-screen provider config: model options
 * follow ``flat[providerPath]``; provider options follow the enabled
 * providers. Keeps the FULL CONFIGURATION ``llm`` block in lockstep with
 * the providers/models configured above instead of free text. */
function PickerControl({
  node,
  flat,
  onChange,
  providerModels,
  enabledProviders,
}: {
  node: ConfigFieldNode;
  flat: FlatValues;
  onChange: (path: string, value: FormLeafValue) => void;
  providerModels: ProviderModels;
  enabledProviders: string[];
}): JSX.Element {
  const value = typeof flat[node.path] === "string" ? (flat[node.path] as string) : "";
  const modelSpec = MODEL_PICKER_FIELDS[node.path];

  if (modelSpec?.crossProvider) {
    // Offer every enabled provider's models, grouped by provider, so a
    // fallback can target a provider other than the primary.
    const groups = enabledProviders
      .map((p) => [p, providerModels[p] ?? []] as const)
      .filter(([, ms]) => ms.length > 0);
    const known = groups.flatMap(([, ms]) => ms);
    const stale = value !== "" && !known.includes(value);
    return (
      <select
        id={node.path}
        data-testid={`cfg-${node.path}`}
        style={inputStyle}
        value={value}
        onChange={(e) => onChange(node.path, e.target.value)}
      >
        {modelSpec.allowEmpty && <option value="">(none)</option>}
        {groups.length === 0 && !modelSpec.allowEmpty && (
          <option value="">(configure provider models above)</option>
        )}
        {groups.map(([prov, ms]) => (
          <optgroup key={prov} label={prov}>
            {ms.map((m) => (
              <option key={`${prov}:${m}`} value={m}>
                {m}
              </option>
            ))}
          </optgroup>
        ))}
        {stale && (
          <option value={value}>{value} (not in configured models)</option>
        )}
      </select>
    );
  }

  if (modelSpec) {
    const provider = String(flat[modelSpec.providerPath] ?? "");
    const models = providerModels[provider] ?? [];
    // Surface a value that isn't in the catalogue (stale config / unknown
    // provider) rather than silently dropping it on first render.
    const stale = value !== "" && !models.includes(value);
    return (
      <select
        id={node.path}
        data-testid={`cfg-${node.path}`}
        style={inputStyle}
        value={value}
        onChange={(e) => onChange(node.path, e.target.value)}
      >
        {modelSpec.allowEmpty && <option value="">(none)</option>}
        {!modelSpec.allowEmpty && models.length === 0 && (
          <option value="">(configure {provider || "provider"} models above)</option>
        )}
        {models.map((m) => (
          <option key={m} value={m}>
            {m}
          </option>
        ))}
        {stale && (
          <option value={value}>{value} (not in configured models)</option>
        )}
      </select>
    );
  }

  // Provider picker — options follow the enabled providers above.
  const options = [...enabledProviders];
  if (value !== "" && !options.includes(value)) options.push(value);
  return (
    <select
      id={node.path}
      data-testid={`cfg-${node.path}`}
      style={inputStyle}
      value={value}
      onChange={(e) => onChange(node.path, e.target.value)}
    >
      {options.length === 0 && <option value="">(enable a provider above)</option>}
      {options.map((p) => (
        <option key={p} value={p}>
          {p}
        </option>
      ))}
    </select>
  );
}

function LeafControl({
  node,
  flat,
  onChange,
  providerModels,
  enabledProviders,
}: {
  node: ConfigFieldNode;
  flat: FlatValues;
  onChange: (path: string, value: FormLeafValue) => void;
  providerModels?: ProviderModels;
  enabledProviders?: string[];
}): JSX.Element {
  // Provider-coupled leaves (the legacy ``llm`` block) render as dropdowns
  // tied to the first-screen config, but only when that context is wired
  // in — fall through to the generic controls otherwise.
  if (
    providerModels &&
    (MODEL_PICKER_FIELDS[node.path] || PROVIDER_PICKER_FIELDS.has(node.path))
  ) {
    return (
      <PickerControl
        node={node}
        flat={flat}
        onChange={onChange}
        providerModels={providerModels}
        enabledProviders={enabledProviders ?? []}
      />
    );
  }

  if (node.kind === "bool") {
    const checked = flat[node.path] === true;
    return (
      <label
        style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12 }}
      >
        <input
          type="checkbox"
          data-testid={`cfg-${node.path}`}
          checked={checked}
          onChange={(e) => onChange(node.path, e.target.checked)}
        />
        <span style={{ fontFamily: "var(--mono)" }}>{node.name}</span>
      </label>
    );
  }

  const value = typeof flat[node.path] === "string" ? (flat[node.path] as string) : "";

  if (node.kind === "enum") {
    return (
      <select
        id={node.path}
        data-testid={`cfg-${node.path}`}
        style={inputStyle}
        value={value}
        onChange={(e) => onChange(node.path, e.target.value)}
      >
        {(node.enum ?? []).map((opt) => (
          <option key={opt} value={opt}>
            {opt}
          </option>
        ))}
      </select>
    );
  }

  if (node.kind === "list_str") {
    return (
      <textarea
        id={node.path}
        data-testid={`cfg-${node.path}`}
        style={{ ...inputStyle, minHeight: 56, resize: "vertical" }}
        value={value}
        onChange={(e) => onChange(node.path, e.target.value)}
        placeholder="one entry per line"
      />
    );
  }

  if (node.kind === "int" || node.kind === "float") {
    return (
      <input
        id={node.path}
        data-testid={`cfg-${node.path}`}
        style={inputStyle}
        type="number"
        step={node.kind === "int" ? 1 : "any"}
        min={node.minimum ?? undefined}
        max={node.maximum ?? undefined}
        value={value}
        onChange={(e) => onChange(node.path, e.target.value)}
        placeholder={node.default === null ? "" : String(node.default)}
      />
    );
  }

  // str
  return (
    <input
      id={node.path}
      data-testid={`cfg-${node.path}`}
      style={inputStyle}
      value={value}
      onChange={(e) => onChange(node.path, e.target.value)}
      placeholder={node.default === null ? "" : String(node.default)}
    />
  );
}

function modifiedWrapStyle(modified: boolean): CSSProperties {
  return {
    borderLeft: `2px solid ${modified ? "var(--accent)" : "transparent"}`,
    paddingLeft: 6,
  };
}

function FieldHeader({
  text,
  htmlFor,
  modified,
  onReset,
}: {
  text: string;
  htmlFor: string;
  modified: boolean;
  onReset: () => void;
}): JSX.Element {
  return (
    <div
      style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}
    >
      <label style={labelStyle} htmlFor={htmlFor}>
        {text}
      </label>
      {modified && <ResetButton onReset={onReset} />}
    </div>
  );
}

function LeafField({
  node,
  flat,
  onChange,
  modified,
  providerModels,
  enabledProviders,
}: {
  node: ConfigFieldNode;
  flat: FlatValues;
  onChange: (path: string, value: FormLeafValue) => void;
  modified: boolean;
  providerModels?: ProviderModels;
  enabledProviders?: string[];
}): JSX.Element {
  const onReset = (): void => onChange(node.path, defaultFlatValue(node));
  const bounds =
    node.minimum != null || node.maximum != null
      ? ` (${node.minimum ?? "−∞"}…${node.maximum ?? "∞"})`
      : "";
  return (
    <div style={modifiedWrapStyle(modified)}>
      {node.kind === "bool" ? (
        <div
          style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}
        >
          <LeafControl node={node} flat={flat} onChange={onChange} />
          {modified && <ResetButton onReset={onReset} />}
        </div>
      ) : (
        <>
          <FieldHeader
            text={`${node.name}${bounds}`}
            htmlFor={node.path}
            modified={modified}
            onReset={onReset}
          />
          <LeafControl
            node={node}
            flat={flat}
            onChange={onChange}
            providerModels={providerModels}
            enabledProviders={enabledProviders}
          />
        </>
      )}
      {node.description && <div style={descStyle}>{node.description}</div>}
    </div>
  );
}

function YamlField({
  node,
  flat,
  onChange,
  error,
  modified,
}: {
  node: ConfigFieldNode;
  flat: FlatValues;
  onChange: (path: string, value: FormLeafValue) => void;
  error?: string;
  modified: boolean;
}): JSX.Element {
  // Complex list/dict structures edited as a YAML blob (Phase 2). Inline
  // parse errors block submit; deep schema errors echo from the backend.
  const value = typeof flat[node.path] === "string" ? (flat[node.path] as string) : "";
  const onReset = (): void => onChange(node.path, defaultFlatValue(node));
  return (
    <div style={modifiedWrapStyle(modified)}>
      <div
        style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}
      >
        <label style={labelStyle}>
          {node.name}{" "}
          <span style={{ color: "var(--fg-2)" }}>· structured (YAML)</span>
        </label>
        {modified && <ResetButton onReset={onReset} />}
      </div>
      <textarea
        data-testid={`yaml-${node.path}`}
        style={{
          ...inputStyle,
          minHeight: 72,
          resize: "vertical",
          borderColor: error ? "var(--red)" : "var(--line)",
        }}
        value={value}
        onChange={(e) => onChange(node.path, e.target.value)}
        placeholder="YAML — e.g. a list of items or key: value map"
        spellCheck={false}
      />
      {error && (
        <div style={{ ...descStyle, color: "var(--red)" }}>YAML error: {error}</div>
      )}
      {node.description && <div style={descStyle}>{node.description}</div>}
    </div>
  );
}

function ObjectGroup({
  node,
  flat,
  onChange,
  errors,
  forceOpen,
  depth,
  providerModels,
  enabledProviders,
}: {
  node: ConfigFieldNode;
  flat: FlatValues;
  onChange: (path: string, value: FormLeafValue) => void;
  errors?: Record<string, string>;
  forceOpen?: boolean;
  depth: number;
  providerModels?: ProviderModels;
  enabledProviders?: string[];
}): JSX.Element {
  const [open, setOpen] = useState(false);
  const effectiveOpen = forceOpen || open;
  return (
    <div
      style={{
        borderLeft: depth > 0 ? "1px solid var(--line)" : "none",
        paddingLeft: depth > 0 ? 10 : 0,
      }}
    >
      <div
        style={{
          cursor: "pointer",
          fontFamily: "var(--mono)",
          fontSize: 11,
          color: "var(--fg-1, var(--fg-0))",
          padding: "4px 0",
        }}
        onClick={() => setOpen((v) => !v)}
      >
        {effectiveOpen ? "▾" : "▸"} {node.name}
      </div>
      {effectiveOpen && (
        <div style={{ paddingTop: 4 }}>
          <SchemaForm
            nodes={node.children}
            flat={flat}
            onChange={onChange}
            errors={errors}
            forceOpen={forceOpen}
            depth={depth + 1}
            providerModels={providerModels}
            enabledProviders={enabledProviders}
          />
        </div>
      )}
    </div>
  );
}

export function SchemaForm({
  nodes,
  flat,
  onChange,
  errors,
  forceOpen,
  depth = 0,
  providerModels,
  enabledProviders,
}: Props): JSX.Element {
  const visible = nodes.filter(hasRenderable);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {visible.map((node) => {
        if (node.kind === "object") {
          return (
            <ObjectGroup
              key={node.path}
              node={node}
              flat={flat}
              onChange={onChange}
              errors={errors}
              forceOpen={forceOpen}
              depth={depth}
              providerModels={providerModels}
              enabledProviders={enabledProviders}
            />
          );
        }
        if (node.kind === "yaml") {
          return (
            <YamlField
              key={node.path}
              node={node}
              flat={flat}
              onChange={onChange}
              error={errors?.[node.path]}
              modified={isLeafModified(node, flat[node.path])}
            />
          );
        }
        if (isEditableLeaf(node)) {
          return (
            <LeafField
              key={node.path}
              node={node}
              flat={flat}
              onChange={onChange}
              modified={isLeafModified(node, flat[node.path])}
              providerModels={providerModels}
              enabledProviders={enabledProviders}
            />
          );
        }
        return null;
      })}
    </div>
  );
}
