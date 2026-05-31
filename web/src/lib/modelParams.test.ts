import { describe, expect, it } from "vitest";
import {
  modelParamsFromAgents,
  recommendedModelParams,
  reconcileModelParams,
} from "./modelParams";

describe("recommendedModelParams", () => {
  it("gives reasoning-class OpenAI models a large completion budget", () => {
    expect(recommendedModelParams("gpt-5.4").max_tokens).toBe("32768");
    expect(recommendedModelParams("o3-mini").max_tokens).toBe("32768");
  });

  it("gives haiku a small budget and others the standard 8192", () => {
    expect(recommendedModelParams("claude-haiku-4-5-20251001").max_tokens).toBe(
      "4096",
    );
    expect(recommendedModelParams("claude-opus-4-7").max_tokens).toBe("8192");
    expect(recommendedModelParams("gpt-4o").max_tokens).toBe("8192");
  });

  it("defaults temperature 0.2 and retries 3", () => {
    const p = recommendedModelParams("claude-opus-4-7");
    expect(p.temperature).toBe("0.2");
    expect(p.max_retries).toBe("3");
  });
});

describe("reconcileModelParams", () => {
  it("adds recommended defaults for new models, drops removed ones, keeps edits", () => {
    const existing = {
      "claude-opus-4-7": { max_tokens: "9000", temperature: "0.1", max_retries: "5" },
      "gone-model": { max_tokens: "1", temperature: "0", max_retries: "1" },
    };
    const out = reconcileModelParams(
      ["claude-opus-4-7", "gpt-5.4"],
      existing,
    );
    // edited entry preserved verbatim
    expect(out["claude-opus-4-7"]).toEqual({
      max_tokens: "9000",
      temperature: "0.1",
      max_retries: "5",
    });
    // new model gets recommended default
    expect(out["gpt-5.4"].max_tokens).toBe("32768");
    // removed model dropped
    expect(out["gone-model"]).toBeUndefined();
  });

  it("dedupes models appearing in both providers", () => {
    const out = reconcileModelParams(["m", "m"], {});
    expect(Object.keys(out)).toEqual(["m"]);
  });
});

describe("modelParamsFromAgents", () => {
  it("seeds one entry per model (first agent wins), back-filling missing fields", () => {
    const agents = {
      planner: {
        provider: "anthropic",
        model: "claude-opus-4-7",
        max_tokens: 8192,
        temperature: 0.2,
        max_retries: 3,
      },
      // second agent on the same model is ignored (first wins)
      judge: {
        provider: "anthropic",
        model: "claude-opus-4-7",
        max_tokens: 2048,
      },
      // model with no params at all → all recommended
      executor: { provider: "openai", model: "gpt-5.4" },
    };
    const out = modelParamsFromAgents(agents);
    expect(out["claude-opus-4-7"]).toEqual({
      max_tokens: "8192",
      temperature: "0.2",
      max_retries: "3",
    });
    expect(out["gpt-5.4"]).toEqual({
      max_tokens: "32768", // recommended back-fill
      temperature: "0.2",
      max_retries: "3",
    });
  });

  it("ignores malformed entries", () => {
    expect(modelParamsFromAgents({ a: null, b: 7, c: {} })).toEqual({});
  });
});
