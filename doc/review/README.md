# Merge-Quality & Hallucination-Handling Audit (2026-05-29)

Triggered by: "合并结果总是不理想，每次测试都有不同的问题——大多是代码缺陷以及未能处理好 LLM 幻觉。"

This folder is the durable record of a deep audit of the merge-quality and
LLM-hallucination-handling paths, the optimization plan derived from it, and the
implementation that followed. It is written for **unattended maintenance** — read
it before touching the executor / conflict-analysis / judge / gating code.

## Documents

| File | What it is |
|------|------------|
| [`00-audit-findings.md`](00-audit-findings.md) | Root-cause themes, the confirmed defects, and reproducible hard evidence. The "why merges are bad" analysis. |
| [`01-optimization-plan.md`](01-optimization-plan.md) | The 12 ranked initiatives with expected benefit, files, effort, and risk. The "what to fix" plan. |
| [`02-implementation-log.md`](02-implementation-log.md) | What was actually changed, in order, with technical trade-offs, temporary assumptions, residual risks, and validation results. **The maintenance hand-off.** Includes the deferred/scoped-down carry-forward table and the Wave-3 E2E. |
| [`03-production-readiness.md`](03-production-readiness.md) | A strict, deliberately non-optimistic answer to "can this do complex production merges unattended?" — the hard limitations, the risk matrix, and the required operating posture (notably: a real `build_check` is mandatory). |

## One-paragraph conclusion

The system was **fail-OPEN**: `COMPLETED` is the default terminal sink past Judge
routing, and almost every gate meant to stop a bad merge was either vacuous
(non-Python syntax check returns `valid=True`), advisory-only (analyst grounding
warnings, diff-facts, deterministic verification report lines), wired on exactly
one of several merge paths (hallucinated-symbol guard only on the chunked path),
or fail-open on error (build-check/smoke launch crash → silent PASS; truncated
analyst/judge JSON → "no issues" / `confidence=0.5`). A TypeScript merge with a
hallucinated symbol, a silently dropped fork feature, or a brace-imbalanced
chunk splice could reach `COMPLETED` with exit 0 and a green "Merge completed
successfully!" — the exact failure class observed. The fix program converts these
logged-only / advisory / fail-open signals into real terminal gates that route to
`AWAITING_HUMAN`, and makes the always-on per-file syntax gate actually validate
the target languages. **Principle: escalate, never corrupt.**

## Implementation status (2026-05-29)

Shipped + unit-tested (3082 unit tests green, `ruff`/`mypy` clean) + live-validated
on `test/fork ← test/upstream`:

| Init | What | State |
|------|------|-------|
| #1 | real TS/JS/Go/Java/Rust syntax (balance) check | ✅ shipped (0 FP on 724 zod files) |
| #2 | single-shot executor fidelity guards (invented-symbol + dropped-export + dedup) | ✅ shipped |
| #3A | Judge batch review fail-closed on unparseable/unavailable | ✅ shipped |
| #4 | dep-bump C-class manifest exclusion | ✅ shipped (native-3way exclusion deliberately deferred — see log) |
| #5 | Judge deterministic invented-symbol veto + O-J1 real-syntax gate | ✅ shipped |
| #6 | hallucinated-symbol guard: chained-leaf + set-based | ✅ shipped |
| #7A | build/smoke launch-crash fail-closed | ✅ shipped |
| #7B | partial-failure visible on interactive/resume path | ✅ shipped |
| #8A | stop chunked-analyst base-resend storm | ✅ shipped (~63% token cut, measured) |
| #8C | pricing-independent token budget ceiling | ✅ shipped |
| #9A/#9B | elision/length-floor: untrimmed baseline + length-only branch | ✅ shipped |
| #9D | chunk_size ↔ max_tokens coupling (no self-truncating chunks) | ✅ shipped |
| #10 | chunked-merge structural alignment (forced-split / seam-balance / empty-target / func-dup escalations + symbol-guarded pairing + grounded prompt) | ✅ shipped (#1 scoped to symbol-guard, #5 detect-not-delete — see log) |
| #11 | preservation auditing (audit drained files, line-level partial-drop, security-zeroed floor, neutral prompt) | ✅ shipped |
| #12 | advisory→gate grounding (fabricated-symbol gate, raw-blob Judge grounding, broadened verbs) | ✅ shipped (part 4 alias-aware deferred) |
| #3B | stop_reason meta-gating for analyst/judge | ⏳ deferred (structured-path conflict) |

**Net effect:** Waves 1–3 are all shipped. The system is **fail-SAFE** — every
confirmed path by which an uncompilable / hallucinated / feature-dropping merge
reached `COMPLETED` now routes to `AWAITING_HUMAN` (or surfaces to conflict
analysis). Wave 3 closed the remaining structural-corruption (G), partial fork-
loss (F), and advisory-grounding (C/F) gaps. **Two changes were deliberately
scoped to avoid corruption** — #10's content-anchored pairing became a
conservative symbol-sequence guard backed by the seam-balance escalation, and
#10's function-dedup detects-and-escalates rather than auto-deleting (which would
risk dropping real TS overloads). See [`02-implementation-log.md`](02-implementation-log.md)
for every trade-off, the deferred carry-forward table, and the live E2E results.
For a strict, non-optimistic read of what is and is NOT production-ready, see
[`03-production-readiness.md`](03-production-readiness.md).

## How the audit was run

- 8 parallel subsystem readers mapped the merge-critical code (executor, conflict
  analysis, hallucination guards, gating/verification, deterministic merge, LLM
  I/O, orchestration/status, prompts).
- 10 multi-lens defect finders, each finding adversarially verified by an
  independent agent that traced the production path (53 candidates → 44 confirmed
  real + reachable).
- Cross-checked by hand against the real zod run log (`run 898b53b5`) and four
  reproducible Python probes (see `00-audit-findings.md` §Evidence).
