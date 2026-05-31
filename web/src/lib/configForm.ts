// Pure helpers backing the schema-driven config editor (Web config UI
// Phase 1). Kept free of React so the value <-> config_overrides round-trip
// can be unit-tested in isolation.
//
// Model: the form holds a *flat* map keyed by each editable leaf's dotted
// path. `object` nodes are pure containers; `yaml` nodes (complex
// list/dict structures) are not editable yet (Phase 2) but their on-disk
// values are preserved untouched. `config_overrides` is rebuilt from the
// current on-disk config minus the curated keys, with the form's editable
// leaves overlaid — so editing is non-destructive to fields the editor
// does not yet own.

import { dump as yamlDump, load as yamlLoad } from "js-yaml";
import type { ConfigFieldNode } from "../types/state";

// bool leaves carry a boolean; every other editable kind carries the raw
// input string (parsed to its real type only when building overrides).
// `yaml` leaves carry the YAML text of a complex list/dict structure.
export type FormLeafValue = boolean | string;
export type FlatValues = Record<string, FormLeafValue>;

// Provider name → list of models that provider exposes (parsed from the
// first-screen provider textareas). Drives the model-picker dropdowns in
// the comprehensive config editor.
export type ProviderModels = Record<string, string[]>;

export interface ModelPickerSpec {
  // Sibling leaf whose value selects which provider's models to offer.
  providerPath: string;
  // ``fallback_model`` is Optional, so the dropdown offers an empty
  // ("none") choice; ``model`` is required and must resolve to a model.
  allowEmpty: boolean;
  // When true the dropdown offers models from *every* configured provider
  // (cross-provider) rather than just the one at ``providerPath`` — a
  // fallback can target a different provider than the primary so a single
  // provider outage isn't fatal. Cross-provider pickers don't depend on
  // ``providerPath``, so switching the primary provider never re-snaps them.
  crossProvider?: boolean;
}

// FULL CONFIGURATION leaves whose value must come from the providers
// configured in the first-screen form rather than free text. Keyed by
// dotted schema path. Mirrors ``LLMConfig`` (``src/models/config.py``):
// the legacy global ``llm`` block, the only non-curated model fields in
// the schema (per-agent models live under the curated ``agents`` subtree).
export const MODEL_PICKER_FIELDS: Record<string, ModelPickerSpec> = {
  "llm.model": { providerPath: "llm.provider", allowEmpty: false },
  "llm.fallback_model": {
    providerPath: "llm.provider",
    allowEmpty: true,
    crossProvider: true,
  },
};

// FULL CONFIGURATION leaves that pick a provider; their options are the
// providers enabled in the first-screen form (not the raw schema enum).
export const PROVIDER_PICKER_FIELDS: ReadonlySet<string> = new Set([
  "llm.provider",
]);

/** When a provider leaf changes, re-snap every model-picker that reads it
 * so the model no longer points at the previous provider's catalogue.
 * Returns only the leaves that need updating (empty when nothing reads the
 * changed path or every dependent model is still valid). Pure — the caller
 * overlays the result onto its flat map. */
export function cascadeModelPickerChange(
  changedPath: string,
  flat: FlatValues,
  providerModels: ProviderModels,
): FlatValues {
  const updates: FlatValues = {};
  for (const [modelPath, spec] of Object.entries(MODEL_PICKER_FIELDS)) {
    if (spec.providerPath !== changedPath) continue;
    // Cross-provider pickers (e.g. the fallback) are independent of the
    // primary provider, so a provider switch must not disturb them.
    if (spec.crossProvider) continue;
    const provider = String(flat[spec.providerPath] ?? "");
    const models = providerModels[provider] ?? [];
    const current = String(flat[modelPath] ?? "");
    if (current !== "" && models.includes(current)) continue;
    updates[modelPath] = spec.allowEmpty ? "" : models[0] ?? "";
  }
  return updates;
}

const EDITABLE_KINDS: ReadonlySet<string> = new Set([
  "bool",
  "int",
  "float",
  "str",
  "enum",
  "list_str",
  "yaml",
]);

export type YamlParse =
  | { ok: true; value: unknown }
  | { ok: false; error: string };

export function parseYaml(text: string): YamlParse {
  try {
    return { ok: true, value: yamlLoad(text) };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}

function dumpYaml(value: unknown): string {
  if (value === null || value === undefined) return "";
  return yamlDump(value).trimEnd();
}

export function isEditableLeaf(node: ConfigFieldNode): boolean {
  return EDITABLE_KINDS.has(node.kind);
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return (
    typeof value === "object" && value !== null && !Array.isArray(value)
  );
}

export function getByPath(
  obj: Record<string, unknown>,
  path: string,
): unknown {
  let cur: unknown = obj;
  for (const part of path.split(".")) {
    if (!isPlainObject(cur)) return undefined;
    cur = cur[part];
  }
  return cur;
}

export function setByPath(
  obj: Record<string, unknown>,
  path: string,
  value: unknown,
): void {
  const parts = path.split(".");
  let cur: Record<string, unknown> = obj;
  for (let i = 0; i < parts.length - 1; i++) {
    const next = cur[parts[i]];
    if (!isPlainObject(next)) {
      cur[parts[i]] = {};
    }
    cur = cur[parts[i]] as Record<string, unknown>;
  }
  cur[parts[parts.length - 1]] = value;
}

export function deleteByPath(
  obj: Record<string, unknown>,
  path: string,
): void {
  const parts = path.split(".");
  let cur: Record<string, unknown> = obj;
  for (let i = 0; i < parts.length - 1; i++) {
    const next = cur[parts[i]];
    if (!isPlainObject(next)) return;
    cur = next;
  }
  delete cur[parts[parts.length - 1]];
}

function deepClone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function deepEqual(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

export function parseListText(text: string): string[] {
  // Newline-primary, comma-secondary, dedup preserved-order — matches the
  // provider models textarea so users can paste either form.
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of text.split(/[\n,]/)) {
    const trimmed = raw.trim();
    if (!trimmed || seen.has(trimmed)) continue;
    seen.add(trimmed);
    out.push(trimmed);
  }
  return out;
}

/** Format a raw value into the leaf's flat (form) representation: boolean
 * for `bool`, newline-joined for `list_str`, YAML text for `yaml`, plain
 * string otherwise. */
function formatLeaf(node: ConfigFieldNode, value: unknown): FormLeafValue {
  if (node.kind === "bool") return typeof value === "boolean" ? value : false;
  if (node.kind === "list_str")
    return (Array.isArray(value) ? value.map((v) => String(v)) : []).join("\n");
  if (node.kind === "yaml") return dumpYaml(value);
  return value === null || value === undefined ? "" : String(value);
}

/** The flat representation of a leaf's schema default (used to pre-fill and
 * to reset a field). */
export function defaultFlatValue(node: ConfigFieldNode): FormLeafValue {
  return formatLeaf(node, node.default);
}

/** Editable, non-curated leaves in document order. Curated leaves are owned
 * by the first-screen form, so the comprehensive editor skips them. */
export function editableLeaves(root: ConfigFieldNode): ConfigFieldNode[] {
  const out: ConfigFieldNode[] = [];
  const walk = (node: ConfigFieldNode): void => {
    if (node.kind === "object") {
      node.children.forEach(walk);
      return;
    }
    if (isEditableLeaf(node) && !node.curated) out.push(node);
  };
  root.children.forEach(walk);
  return out;
}

/** Topmost curated paths — deleting these from the disk seed lets the
 * curated first-screen form (provider/agents/core thresholds) stay the sole
 * writer for those keys. A node is a strip-root when it is curated and its
 * parent is not (so `agents` strips the whole subtree, while
 * `thresholds.auto_merge_confidence` strips just that leaf). */
export function curatedStripPaths(root: ConfigFieldNode): string[] {
  const out: string[] = [];
  const walk = (node: ConfigFieldNode, parentCurated: boolean): void => {
    if (node.curated && !parentCurated) {
      out.push(node.path);
      return;
    }
    node.children.forEach((c) => walk(c, node.curated));
  };
  root.children.forEach((c) => walk(c, false));
  return out;
}

/** Initial flat values for every editable non-curated leaf: the on-disk
 * value when present, else the schema default — formatted into the raw form
 * representation (boolean for `bool`, string otherwise). */
export function initFlatValues(
  root: ConfigFieldNode,
  diskValues: Record<string, unknown>,
): FlatValues {
  const flat: FlatValues = {};
  for (const leaf of editableLeaves(root)) {
    const disk = getByPath(diskValues, leaf.path);
    flat[leaf.path] =
      disk !== undefined ? formatLeaf(leaf, disk) : defaultFlatValue(leaf);
  }
  return flat;
}

/** All editable leaves reset to their schema defaults — backs "reset all". */
export function defaultFlatValues(root: ConfigFieldNode): FlatValues {
  const flat: FlatValues = {};
  for (const leaf of editableLeaves(root)) flat[leaf.path] = defaultFlatValue(leaf);
  return flat;
}

const UNSET = Symbol("unset");

function parseLeaf(node: ConfigFieldNode, raw: FormLeafValue): unknown | typeof UNSET {
  if (node.kind === "bool") return raw === true;
  if (node.kind === "list_str") return parseListText(String(raw));
  const text = String(raw).trim();
  if (node.kind === "int" || node.kind === "float") {
    if (text === "") return UNSET;
    const n = node.kind === "int" ? parseInt(text, 10) : Number(text);
    return Number.isFinite(n) ? n : UNSET;
  }
  // str / enum
  return String(raw);
}

function pruneEmptyObjects(obj: Record<string, unknown>): void {
  for (const key of Object.keys(obj)) {
    const val = obj[key];
    if (isPlainObject(val)) {
      pruneEmptyObjects(val);
      if (Object.keys(val).length === 0) delete obj[key];
    }
  }
}

/** Build the `config_overrides` payload: the on-disk config minus curated
 * keys, with each editable leaf overlaid when it differs from its default
 * (and removed when it equals the default, so a reset drops a stale disk
 * override). yaml/object on-disk values are preserved untouched. */
export function buildConfigOverrides(
  root: ConfigFieldNode,
  flat: FlatValues,
  diskValues: Record<string, unknown>,
): Record<string, unknown> {
  const base: Record<string, unknown> = deepClone(diskValues ?? {});
  for (const path of curatedStripPaths(root)) deleteByPath(base, path);

  for (const leaf of editableLeaves(root)) {
    if (leaf.kind === "yaml") {
      const text = String(flat[leaf.path] ?? "").trim();
      if (text === "") {
        deleteByPath(base, leaf.path);
        continue;
      }
      const parsedYaml = parseYaml(text);
      if (!parsedYaml.ok) continue; // submit is blocked on parse errors
      if (deepEqual(parsedYaml.value, leaf.default)) deleteByPath(base, leaf.path);
      else setByPath(base, leaf.path, parsedYaml.value);
      continue;
    }
    const parsed = parseLeaf(leaf, flat[leaf.path]);
    if (parsed === UNSET || deepEqual(parsed, leaf.default)) {
      deleteByPath(base, leaf.path);
    } else {
      setByPath(base, leaf.path, parsed);
    }
  }
  pruneEmptyObjects(base);
  return base;
}

/** Per-path YAML parse errors for the `yaml` leaves — surfaced inline and
 * used to block submit before the structure reaches the backend. */
export function yamlErrors(
  root: ConfigFieldNode,
  flat: FlatValues,
): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const leaf of editableLeaves(root)) {
    if (leaf.kind !== "yaml") continue;
    const text = String(flat[leaf.path] ?? "").trim();
    if (text === "") continue;
    const parsed = parseYaml(text);
    if (!parsed.ok) errors[leaf.path] = parsed.error;
  }
  return errors;
}

/** True when a leaf's current form value differs from its schema default
 * (an empty numeric / blank yaml counts as "use default" → not modified;
 * unparseable yaml counts as a pending change → modified). */
export function isLeafModified(node: ConfigFieldNode, raw: FormLeafValue): boolean {
  if (node.kind === "yaml") {
    const text = String(raw ?? "").trim();
    if (text === "") return false;
    const parsed = parseYaml(text);
    if (!parsed.ok) return true;
    return !deepEqual(parsed.value, node.default);
  }
  const parsed = parseLeaf(node, raw);
  if (parsed === UNSET) return false;
  return !deepEqual(parsed, node.default);
}

export function modifiedPaths(
  root: ConfigFieldNode,
  flat: FlatValues,
): string[] {
  return editableLeaves(root)
    .filter((n) => isLeafModified(n, flat[n.path]))
    .map((n) => n.path);
}

/** Case-insensitive match of a node against a search query (name / path /
 * description). */
export function nodeMatches(node: ConfigFieldNode, query: string): boolean {
  const q = query.toLowerCase();
  return (
    node.name.toLowerCase().includes(q) ||
    node.path.toLowerCase().includes(q) ||
    (node.description ?? "").toLowerCase().includes(q)
  );
}

/** Prune the tree to nodes matching `query`. A matching object node keeps
 * its whole subtree; otherwise an object survives only if a descendant
 * matches. Empty query returns the tree unchanged. */
export function filterTree(
  root: ConfigFieldNode,
  query: string,
): ConfigFieldNode {
  const q = query.trim();
  if (q === "") return root;
  const prune = (node: ConfigFieldNode): ConfigFieldNode | null => {
    if (node.kind !== "object") return nodeMatches(node, q) ? node : null;
    if (nodeMatches(node, q)) return node;
    const kids = node.children
      .map(prune)
      .filter((n): n is ConfigFieldNode => n !== null);
    return kids.length ? { ...node, children: kids } : null;
  };
  const kids = root.children
    .map(prune)
    .filter((n): n is ConfigFieldNode => n !== null);
  return { ...root, children: kids };
}
