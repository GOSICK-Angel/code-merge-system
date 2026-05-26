import { describe, expect, it } from "vitest";
import type { ConfigFieldNode } from "../types/state";
import {
  buildConfigOverrides,
  cascadeModelPickerChange,
  curatedStripPaths,
  defaultFlatValue,
  defaultFlatValues,
  editableLeaves,
  filterTree,
  getByPath,
  initFlatValues,
  isLeafModified,
  modifiedPaths,
  nodeMatches,
  parseListText,
  parseYaml,
  setByPath,
  yamlErrors,
} from "./configForm";

function leaf(
  name: string,
  path: string,
  kind: ConfigFieldNode["kind"],
  def: unknown,
  curated = false,
): ConfigFieldNode {
  return {
    name,
    path,
    kind,
    default: def,
    description: null,
    required: false,
    curated,
    enum: kind === "enum" ? ["off", "auto", "always"] : null,
    minimum: null,
    maximum: null,
    children: [],
  };
}

function obj(
  name: string,
  path: string,
  children: ConfigFieldNode[],
  curated = false,
): ConfigFieldNode {
  return {
    name,
    path,
    kind: "object",
    default: null,
    description: null,
    required: false,
    curated,
    enum: null,
    minimum: null,
    maximum: null,
    children,
  };
}

// Root mirroring the real schema's relevant shape: a curated subtree
// (agents), a partially-curated container (thresholds), a non-curated
// container (dependency_graph), a top-level scalar, and a yaml node.
function sampleRoot(): ConfigFieldNode {
  return obj("", "", [
    leaf("max_files_per_run", "max_files_per_run", "int", 500),
    obj("agents", "agents", [leaf("model", "agents.planner.model", "str", "x", true)], true),
    obj("thresholds", "thresholds", [
      leaf("auto_merge_confidence", "thresholds.auto_merge_confidence", "float", 0.85, true),
      leaf("human_escalation", "thresholds.human_escalation", "float", 0.6),
    ]),
    obj("dependency_graph", "dependency_graph", [
      leaf("enabled", "dependency_graph.enabled", "bool", true),
      leaf("god_node_min_dependents", "dependency_graph.god_node_min_dependents", "int", 8),
      leaf("languages", "dependency_graph.languages", "list_str", ["python", "go"]),
    ]),
    leaf("customizations", "customizations", "yaml", []),
  ]);
}

describe("path helpers", () => {
  it("get/set nested paths", () => {
    const o: Record<string, unknown> = {};
    setByPath(o, "a.b.c", 1);
    expect(getByPath(o, "a.b.c")).toBe(1);
    expect(o).toEqual({ a: { b: { c: 1 } } });
  });
});

describe("parseListText", () => {
  it("splits on newlines and commas, dedups, preserves order", () => {
    expect(parseListText("a\nb, a\n\nc")).toEqual(["a", "b", "c"]);
  });
});

describe("editableLeaves / curatedStripPaths", () => {
  it("excludes curated leaves and the curated subtree", () => {
    const paths = editableLeaves(sampleRoot()).map((n) => n.path);
    expect(paths).toContain("max_files_per_run");
    expect(paths).toContain("thresholds.human_escalation");
    expect(paths).toContain("dependency_graph.languages");
    // curated leaf + whole curated subtree excluded
    expect(paths).not.toContain("thresholds.auto_merge_confidence");
    expect(paths).not.toContain("agents.planner.model");
    // yaml nodes ARE editable leaves now (Phase 2 inline editor)
    expect(paths).toContain("customizations");
  });

  it("returns topmost curated strip roots", () => {
    expect(curatedStripPaths(sampleRoot()).sort()).toEqual([
      "agents",
      "thresholds.auto_merge_confidence",
    ]);
  });
});

describe("initFlatValues", () => {
  it("prefers disk values, falls back to defaults, formats per kind", () => {
    const flat = initFlatValues(sampleRoot(), {
      max_files_per_run: 123,
      dependency_graph: { languages: ["ts"] },
    });
    expect(flat["max_files_per_run"]).toBe("123"); // disk, stringified
    expect(flat["dependency_graph.god_node_min_dependents"]).toBe("8"); // default
    expect(flat["dependency_graph.enabled"]).toBe(true); // bool stays boolean
    expect(flat["dependency_graph.languages"]).toBe("ts"); // disk list joined
    expect(flat["thresholds.human_escalation"]).toBe("0.6");
  });
});

describe("buildConfigOverrides", () => {
  it("emits only values differing from default, excludes curated", () => {
    const root = sampleRoot();
    const flat = initFlatValues(root, {});
    flat["max_files_per_run"] = "999";
    flat["dependency_graph.enabled"] = false;
    flat["thresholds.auto_merge_confidence"] = "0.1"; // curated — ignored
    const out = buildConfigOverrides(root, flat, {});
    expect(out).toEqual({
      max_files_per_run: 999,
      dependency_graph: { enabled: false },
    });
  });

  it("drops a key back to default when reset (removes stale disk override)", () => {
    const root = sampleRoot();
    const disk = { max_files_per_run: 999 };
    const flat = initFlatValues(root, disk);
    flat["max_files_per_run"] = "500"; // reset to default
    const out = buildConfigOverrides(root, flat, disk);
    expect(out.max_files_per_run).toBeUndefined();
  });

  it("preserves on-disk yaml/unknown keys and strips curated subtree", () => {
    const root = sampleRoot();
    const disk = {
      customizations: [{ name: "keepme" }],
      agents: { planner: { model: "should-be-stripped" } },
      unknown_future_key: 7,
    };
    const flat = initFlatValues(root, disk);
    const out = buildConfigOverrides(root, flat, disk);
    expect(out.customizations).toEqual([{ name: "keepme" }]);
    expect(out.unknown_future_key).toBe(7);
    expect(out.agents).toBeUndefined();
  });

  it("parses list_str edits into arrays", () => {
    const root = sampleRoot();
    const flat = initFlatValues(root, {});
    flat["dependency_graph.languages"] = "python\nrust";
    const out = buildConfigOverrides(root, flat, {});
    expect((out.dependency_graph as Record<string, unknown>).languages).toEqual([
      "python",
      "rust",
    ]);
  });
});

describe("yaml leaves", () => {
  it("initFlatValues dumps disk value to YAML text", () => {
    const flat = initFlatValues(sampleRoot(), {
      customizations: [{ name: "keepme" }],
    });
    expect(parseYaml(flat["customizations"] as string)).toEqual({
      ok: true,
      value: [{ name: "keepme" }],
    });
  });

  it("buildConfigOverrides parses edited YAML into structure", () => {
    const root = sampleRoot();
    const flat = initFlatValues(root, {});
    flat["customizations"] = "- name: added\n  files:\n    - a.py";
    const out = buildConfigOverrides(root, flat, {});
    expect(out.customizations).toEqual([{ name: "added", files: ["a.py"] }]);
  });

  it("buildConfigOverrides drops yaml back to default when unchanged", () => {
    const root = sampleRoot();
    const flat = initFlatValues(root, {}); // customizations dumps to "[]"
    const out = buildConfigOverrides(root, flat, {});
    expect(out.customizations).toBeUndefined();
  });

  it("invalid YAML is skipped (keeps disk value) and reported", () => {
    const root = sampleRoot();
    const disk = { customizations: [{ name: "keepme" }] };
    const flat = initFlatValues(root, disk);
    flat["customizations"] = "key: [unclosed";
    expect(Object.keys(yamlErrors(root, flat))).toContain("customizations");
    // build defensively preserves the on-disk value rather than dropping it
    const out = buildConfigOverrides(root, flat, disk);
    expect(out.customizations).toEqual([{ name: "keepme" }]);
  });

  it("yamlErrors is empty when all yaml leaves parse", () => {
    const root = sampleRoot();
    const flat = initFlatValues(root, { customizations: [{ name: "x" }] });
    expect(yamlErrors(root, flat)).toEqual({});
  });
});

describe("modified detection + reset", () => {
  it("isLeafModified flags only values differing from default", () => {
    const root = sampleRoot();
    const flat = initFlatValues(root, {});
    const intLeaf = editableLeaves(root).find((n) => n.path === "max_files_per_run")!;
    expect(isLeafModified(intLeaf, flat["max_files_per_run"])).toBe(false);
    expect(isLeafModified(intLeaf, "999")).toBe(true);
    expect(isLeafModified(intLeaf, "")).toBe(false); // empty → use default
    const yamlLeaf = editableLeaves(root).find((n) => n.path === "customizations")!;
    expect(isLeafModified(yamlLeaf, "[]")).toBe(false);
    expect(isLeafModified(yamlLeaf, "key: [unclosed")).toBe(true); // pending/invalid
  });

  it("modifiedPaths lists every changed leaf", () => {
    const root = sampleRoot();
    const flat = initFlatValues(root, {});
    flat["max_files_per_run"] = "1";
    flat["dependency_graph.enabled"] = false;
    expect(modifiedPaths(root, flat).sort()).toEqual([
      "dependency_graph.enabled",
      "max_files_per_run",
    ]);
  });

  it("defaultFlatValue / defaultFlatValues restore schema defaults", () => {
    const root = sampleRoot();
    const intLeaf = editableLeaves(root).find((n) => n.path === "max_files_per_run")!;
    expect(defaultFlatValue(intLeaf)).toBe("500");
    const defaults = defaultFlatValues(root);
    expect(defaults["dependency_graph.enabled"]).toBe(true);
    expect(modifiedPaths(root, defaults)).toEqual([]);
  });
});

describe("cascadeModelPickerChange", () => {
  const providerModels = {
    anthropic: ["claude-opus-4-7", "claude-haiku-4-5-20251001"],
    openai: ["gpt-5.4", "gpt-5.4-mini"],
  };

  it("re-snaps the primary model on provider switch but leaves the cross-provider fallback alone", () => {
    const flat = {
      "llm.provider": "openai",
      "llm.model": "claude-opus-4-7", // stale anthropic model
      "llm.fallback_model": "claude-haiku-4-5-20251001",
    };
    // Only the primary model is re-snapped; fallback_model is cross-provider
    // so it is independent of llm.provider and must not be touched.
    expect(cascadeModelPickerChange("llm.provider", flat, providerModels)).toEqual({
      "llm.model": "gpt-5.4", // first openai model
    });
  });

  it("leaves a still-valid primary model untouched", () => {
    const flat = {
      "llm.provider": "openai",
      "llm.model": "gpt-5.4-mini", // already an openai model
      "llm.fallback_model": "gpt-5.4",
    };
    expect(cascadeModelPickerChange("llm.provider", flat, providerModels)).toEqual(
      {},
    );
  });

  it("falls back to empty primary model when the new provider has no configured models", () => {
    const flat = { "llm.provider": "openai", "llm.model": "claude-opus-4-7" };
    expect(
      cascadeModelPickerChange("llm.provider", flat, {
        anthropic: ["claude-opus-4-7"],
        openai: [],
      }),
    ).toEqual({ "llm.model": "" });
  });

  it("returns nothing when the changed path drives no model picker", () => {
    const flat = { "llm.model": "gpt-5.4" };
    expect(
      cascadeModelPickerChange("max_files_per_run", flat, providerModels),
    ).toEqual({});
  });
});

describe("search filter", () => {
  it("nodeMatches checks name, path and description", () => {
    const root = sampleRoot();
    const leaf = editableLeaves(root).find((n) => n.path === "max_files_per_run")!;
    expect(nodeMatches(leaf, "max_files")).toBe(true);
    expect(nodeMatches(leaf, "DEPENDENCY")).toBe(false);
  });

  it("filterTree prunes to matching leaves and keeps ancestor groups", () => {
    const out = filterTree(sampleRoot(), "languages");
    const top = out.children.map((c) => c.name);
    expect(top).toEqual(["dependency_graph"]);
    const depKids = out.children[0].children.map((c) => c.name);
    expect(depKids).toEqual(["languages"]);
  });

  it("filterTree keeps the whole subtree when a group name matches", () => {
    const out = filterTree(sampleRoot(), "dependency_graph");
    expect(out.children).toHaveLength(1);
    expect(out.children[0].children.map((c) => c.name)).toEqual([
      "enabled",
      "god_node_min_dependents",
      "languages",
    ]);
  });

  it("empty query returns the tree unchanged", () => {
    const root = sampleRoot();
    expect(filterTree(root, "  ")).toBe(root);
  });
});
