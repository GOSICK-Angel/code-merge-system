import { beforeEach, describe, expect, it } from "vitest";
import {
  useConflictDraftStore,
  validateDraft,
} from "./conflictDraftStore";

describe("conflictDraftStore", () => {
  beforeEach(() => {
    useConflictDraftStore.setState({ drafts: {}, selectedFile: null });
  });

  it("setDraftDecision creates a fresh draft with notes/content defaulting to empty", () => {
    useConflictDraftStore.getState().setDraftDecision("a.py", "take_current");
    const { drafts } = useConflictDraftStore.getState();
    expect(drafts["a.py"]).toEqual({
      decision: "take_current",
      reviewer_notes: "",
      custom_content: "",
    });
  });

  it("setDraftDecision preserves notes/content when changing decision", () => {
    const { setDraftDecision, setDraftNotes, setDraftCustomContent } =
      useConflictDraftStore.getState();
    setDraftDecision("a.py", "manual_patch");
    setDraftNotes("a.py", "verified locally");
    setDraftCustomContent("a.py", "--- patch ---");
    setDraftDecision("a.py", "take_current"); // change mind
    const { drafts } = useConflictDraftStore.getState();
    expect(drafts["a.py"]).toEqual({
      decision: "take_current",
      reviewer_notes: "verified locally",
      custom_content: "--- patch ---",
    });
  });

  it("setDraftNotes is a no-op when no draft exists", () => {
    useConflictDraftStore.getState().setDraftNotes("ghost.py", "notes");
    expect(useConflictDraftStore.getState().drafts).toEqual({});
  });

  it("clearDraft removes the entry", () => {
    const { setDraftDecision, clearDraft } = useConflictDraftStore.getState();
    setDraftDecision("a.py", "skip");
    setDraftDecision("b.py", "take_target");
    clearDraft("a.py");
    expect(Object.keys(useConflictDraftStore.getState().drafts)).toEqual([
      "b.py",
    ]);
  });

  it("applyRecommendedToAll skips null and escalate_human recommendations", () => {
    const { applyRecommendedToAll } = useConflictDraftStore.getState();
    const applied = applyRecommendedToAll([
      { file_path: "a.py", recommendation: "take_target" },
      { file_path: "b.py", recommendation: null }, // skip
      { file_path: "c.py", recommendation: "semantic_merge" },
    ]);
    expect(applied).toBe(2);
    const { drafts } = useConflictDraftStore.getState();
    expect(drafts["a.py"].decision).toBe("take_target");
    expect(drafts["c.py"].decision).toBe("semantic_merge");
    expect(drafts["b.py"]).toBeUndefined();
  });

  it("applyRecommendedToAll preserves existing notes/content when overwriting decision", () => {
    const store = useConflictDraftStore.getState();
    store.setDraftDecision("a.py", "manual_patch");
    store.setDraftCustomContent("a.py", "custom patch text");
    store.applyRecommendedToAll([
      { file_path: "a.py", recommendation: "take_current" },
    ]);
    expect(useConflictDraftStore.getState().drafts["a.py"]).toEqual({
      decision: "take_current",
      reviewer_notes: "",
      custom_content: "custom patch text",
    });
  });
});

describe("validateDraft", () => {
  it("rejects manual_patch with empty custom_content", () => {
    const err = validateDraft({
      decision: "manual_patch",
      reviewer_notes: "",
      custom_content: "",
    });
    expect(err).toMatch(/MANUAL_PATCH requires/);
  });

  it("rejects manual_patch with whitespace-only custom_content", () => {
    const err = validateDraft({
      decision: "manual_patch",
      reviewer_notes: "",
      custom_content: "   \n\t  ",
    });
    expect(err).not.toBeNull();
  });

  it("accepts manual_patch with content", () => {
    expect(
      validateDraft({
        decision: "manual_patch",
        reviewer_notes: "",
        custom_content: "--- patch ---",
      }),
    ).toBeNull();
  });

  it("accepts other decisions regardless of custom_content", () => {
    expect(
      validateDraft({
        decision: "take_current",
        reviewer_notes: "",
        custom_content: "",
      }),
    ).toBeNull();
    expect(
      validateDraft({
        decision: "skip",
        reviewer_notes: "",
        custom_content: "",
      }),
    ).toBeNull();
  });
});
