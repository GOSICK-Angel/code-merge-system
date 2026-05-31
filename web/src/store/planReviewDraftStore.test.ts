import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  commitApprove,
  commitModify,
  commitReject,
  usePlanReviewDraftStore,
} from "./planReviewDraftStore";
import type { OutboundMessage } from "../ws/messages";
import type { PendingUserDecision } from "../types/state";

function makeItem(
  id: string,
  options: Array<{ key: string; label: string; description?: string }> = [],
  user_choice: string | null = null,
): PendingUserDecision {
  return {
    item_id: id,
    file_path: `${id}.py`,
    description: `decide ${id}`,
    options: options.map((o) => ({
      key: o.key,
      label: o.label,
      description: o.description ?? "",
    })),
    user_choice,
    user_input: null,
  };
}

describe("planReviewDraftStore", () => {
  beforeEach(() => {
    usePlanReviewDraftStore.setState({ drafts: {}, notes: "" });
  });

  it("setDraft creates empty draft when missing", () => {
    usePlanReviewDraftStore.getState().setDraft("i1", "opt_a");
    expect(usePlanReviewDraftStore.getState().drafts["i1"]).toEqual({
      user_choice: "opt_a",
      user_input: "",
    });
  });

  it("setDraft preserves user_input when changing choice", () => {
    const store = usePlanReviewDraftStore.getState();
    store.setDraft("i1", "opt_a");
    store.setDraftInput("i1", "extra context");
    store.setDraft("i1", "opt_b");
    expect(usePlanReviewDraftStore.getState().drafts["i1"]).toEqual({
      user_choice: "opt_b",
      user_input: "extra context",
    });
  });

  it("setDraftInput auto-creates a notes-only draft (user_choice='') when no draft exists", () => {
    // Supports the no-options edge case where the reviewer's only
    // signal is the free-form input.
    usePlanReviewDraftStore.getState().setDraftInput("notes_only", "ack");
    expect(usePlanReviewDraftStore.getState().drafts["notes_only"]).toEqual({
      user_choice: "",
      user_input: "ack",
    });
  });

  it("clearDraft removes the entry", () => {
    const store = usePlanReviewDraftStore.getState();
    store.setDraft("i1", "a");
    store.setDraft("i2", "b");
    store.clearDraft("i1");
    expect(Object.keys(usePlanReviewDraftStore.getState().drafts)).toEqual([
      "i2",
    ]);
  });

  it("applyRecommendedToAll uses each item's first option as default", () => {
    const items = [
      makeItem("i1", [
        { key: "first", label: "First" },
        { key: "second", label: "Second" },
      ]),
      makeItem("i2", [{ key: "only", label: "Only" }]),
    ];
    const applied = usePlanReviewDraftStore
      .getState()
      .applyRecommendedToAll(items);
    expect(applied).toBe(2);
    expect(usePlanReviewDraftStore.getState().drafts["i1"].user_choice).toBe(
      "first",
    );
    expect(usePlanReviewDraftStore.getState().drafts["i2"].user_choice).toBe(
      "only",
    );
  });

  it("applyRecommendedToAll skips items with no options", () => {
    const items = [
      makeItem("i1", []),
      makeItem("i2", [{ key: "only", label: "Only" }]),
    ];
    const applied = usePlanReviewDraftStore
      .getState()
      .applyRecommendedToAll(items);
    expect(applied).toBe(1);
    expect(usePlanReviewDraftStore.getState().drafts["i1"]).toBeUndefined();
  });

  it("applyRecommendedToAll skips already-decided items", () => {
    const items = [
      makeItem("i1", [{ key: "a", label: "A" }], "previous_choice"),
      makeItem("i2", [{ key: "b", label: "B" }]),
    ];
    const applied = usePlanReviewDraftStore
      .getState()
      .applyRecommendedToAll(items);
    expect(applied).toBe(1);
    expect(usePlanReviewDraftStore.getState().drafts["i1"]).toBeUndefined();
    expect(usePlanReviewDraftStore.getState().drafts["i2"].user_choice).toBe(
      "b",
    );
  });

  it("reset clears drafts and notes", () => {
    const store = usePlanReviewDraftStore.getState();
    store.setDraft("i1", "a");
    store.setNotes("hello");
    store.reset();
    const state = usePlanReviewDraftStore.getState();
    expect(state.drafts).toEqual({});
    expect(state.notes).toBe("");
  });
});

describe("commitApprove / commitReject / commitModify (plan v1.1 §P1-3)", () => {
  it("commitApprove sends user_plan_decisions THEN plan_review:approve", () => {
    const send = vi.fn<(msg: OutboundMessage) => void>();
    commitApprove(
      send,
      [{ item_id: "i1" }, { item_id: "i2" }],
      {
        i1: { user_choice: "a", user_input: "ctx" },
        i2: { user_choice: "b", user_input: "" },
      },
      "Looks fine",
    );
    expect(send).toHaveBeenCalledTimes(2);
    const first = send.mock.calls[0][0];
    const second = send.mock.calls[1][0];
    expect(first.type).toBe("submit_user_plan_decisions");
    if (first.type !== "submit_user_plan_decisions") return;
    expect(first.payload.items).toEqual([
      { item_id: "i1", user_choice: "a", user_input: "ctx" },
      { item_id: "i2", user_choice: "b", user_input: undefined },
    ]);
    expect(second).toEqual({
      type: "submit_plan_review",
      payload: { decision: "approve", notes: "Looks fine" },
    });
  });

  it("commitApprove fills user_choice='' for items without a draft (no-options case)", () => {
    const send = vi.fn<(msg: OutboundMessage) => void>();
    commitApprove(
      send,
      [{ item_id: "i1" }, { item_id: "i6_no_options" }],
      { i1: { user_choice: "a", user_input: "" } },
      "",
    );
    const first = send.mock.calls[0][0];
    if (first.type !== "submit_user_plan_decisions") throw new Error("type");
    expect(first.payload.items).toEqual([
      { item_id: "i1", user_choice: "a", user_input: undefined },
      { item_id: "i6_no_options", user_choice: "", user_input: undefined },
    ]);
  });

  it("commitApprove omits notes when empty", () => {
    const send = vi.fn<(msg: OutboundMessage) => void>();
    commitApprove(
      send,
      [{ item_id: "i1" }],
      { i1: { user_choice: "a", user_input: "" } },
      "",
    );
    const second = send.mock.calls[1][0];
    if (second.type !== "submit_plan_review") throw new Error("unexpected");
    expect(second.payload.notes).toBeUndefined();
  });

  it("commitReject only sends plan_review:reject (no user_plan_decisions)", () => {
    const send = vi.fn<(msg: OutboundMessage) => void>();
    commitReject(send, "Plan unsafe, retry");
    expect(send).toHaveBeenCalledTimes(1);
    expect(send.mock.calls[0][0]).toEqual({
      type: "submit_plan_review",
      payload: { decision: "reject", notes: "Plan unsafe, retry" },
    });
  });

  it("commitReject omits notes when empty", () => {
    const send = vi.fn<(msg: OutboundMessage) => void>();
    commitReject(send, "");
    expect(send).toHaveBeenCalledTimes(1);
    const msg = send.mock.calls[0][0];
    if (msg.type !== "submit_plan_review") throw new Error("unexpected");
    expect(msg.payload.notes).toBeUndefined();
  });

  it("commitModify sends user_plan_decisions THEN plan_review:modify", () => {
    const send = vi.fn<(msg: OutboundMessage) => void>();
    commitModify(
      send,
      [{ item_id: "i1" }],
      { i1: { user_choice: "a", user_input: "" } },
      "Please rework layer 2",
    );
    expect(send).toHaveBeenCalledTimes(2);
    expect(send.mock.calls[0][0].type).toBe("submit_user_plan_decisions");
    const second = send.mock.calls[1][0];
    if (second.type !== "submit_plan_review") throw new Error("unexpected");
    expect(second.payload.decision).toBe("modify");
    expect(second.payload.notes).toBe("Please rework layer 2");
  });

  it("commitModify supports zero-draft 'just submit notes' path", () => {
    const send = vi.fn<(msg: OutboundMessage) => void>();
    commitModify(
      send,
      [{ item_id: "i1" }, { item_id: "i2" }],
      {},
      "Address layer 2 concerns",
    );
    expect(send).toHaveBeenCalledTimes(2);
    const first = send.mock.calls[0][0];
    if (first.type !== "submit_user_plan_decisions") throw new Error("type");
    expect(first.payload.items).toEqual([
      { item_id: "i1", user_choice: "", user_input: undefined },
      { item_id: "i2", user_choice: "", user_input: undefined },
    ]);
  });
});
