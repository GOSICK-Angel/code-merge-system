# Production-Readiness Assessment — strict, non-optimistic

Date: 2026-05-29, after Waves 1–3 shipped. This document deliberately argues the
*skeptical* case. The question is not "did the fixes land" (they did, 3105 tests
green) but **"can this system perform complex code merges on a production-grade
repository, unattended, and be trusted?"** The honest answer is nuanced.

## One-line verdict

The system is now a **safe human-in-the-loop merge assistant**: after Waves 1–3
the confirmed paths by which a corrupt / hallucinated / feature-dropping merge
reached a green `COMPLETED` are closed — they escalate instead. It is **NOT** a
trustworthy *fully-autonomous* complex-merge bot. Its safety comes substantially
from **escalating aggressively**, not from merging complex conflicts correctly on
its own. Treat a high auto-merge rate on hard files as a red flag, not a success.

## What genuinely improved (Waves 1–3)

- **No more silent corruption on the known paths.** TS/JS/Go/Java/Rust now get a
  real brace/string-balance gate (#1); the single-shot path runs the same
  fidelity guards as the chunked path (#2); truncated Judge verdicts fail closed
  (#3A); native-3way C-class files are covered by the Judge invented-symbol veto
  (#5) and the preservation audit (#11); build/smoke launch crashes fail closed
  (#7A); chunked merges escalate on forced mid-body splits, seam imbalance, and
  function redeclaration (#10); a rationale that invents a symbol can no longer
  auto-merge (#12).
- **Escalate-never-corrupt is now structural**, not aspirational. Every Wave-3
  gate routes to `AWAITING_HUMAN` / conflict analysis; none deletes content.

## The hard limitations a production operator MUST understand

### 1. The always-on syntax gate is balance-only, not a type/semantic check
`check_syntax` counts brackets and detects unterminated strings. It does **not**
catch a brace-balanced file that is semantically broken: a type error, an
undefined-but-plausible identifier, a wrong import path, a broken call signature.
**Real compile-level correctness depends entirely on the operator configuring the
`build_check` toolchain gate (`tsc --noEmit`, `go build`, …).** If `build_check`
is unset, a brace-balanced but uncompilable merge can still reach `COMPLETED`.
This is the single biggest "looks safe but isn't" gap. *Action: never run a
production merge without a real `build_check` configured.*

### 2. The hallucination guards are lexical, not semantic
`find_invented_member_accesses` catches one specific shape — a fabricated member
on a real, referenced base (`core._isoWeek`). It does **not** catch: a real
symbol used wrongly, a plausible-but-incorrect API call, off-by-one logic, a
dropped guard clause, or semantic drift that uses only real symbols. The only
semantic reviewer is the **Judge LLM**, which is fallible and itself subject to
truncation / hallucination (now partly mitigated by #3A and #12-part-2, not
eliminated). Defense-in-depth is real but bounded.

### 3. "Safe" largely means "escalates a lot" on complex files
The chunked path now escalates on forced-split, seam imbalance, truncation, dup
function, and fabricated symbols. On genuinely large/complex C-class files the
system will frequently escalate rather than auto-merge. That is *correct* and
*desirable*, but it means the **hands-off auto-merge rate on hard conflicts is
intentionally low**. Anyone expecting autonomous resolution of a large divergent
fork will instead get a large human-review queue.

### 4. Config sensitivity is high and under-documented at the point of use
- `max_tokens` default (8192 for deepseek) is **too small** to emit a large
  file's chunk → big files hit the truncation gate and escalate. The operator
  must raise `max_tokens` (model permitting) or lower `chunk_size_chars` (the
  #9D coupling helps but is not automatic across all configs).
- `preservation_fork_survival_floor` (0.7) and `preservation_min_fork_lines`
  (50) are calibration defaults; wrong values either over-escalate or miss
  partial loss.
- The line-level preservation check and several deterministic gates read git
  content best-effort and **degrade to "skip" on any git failure** — a
  systematically misconfigured `git_tool` would silently disable gates without
  failing the run. There is no "gates were silently skipped" alarm.

### 5. Validation coverage is unit-heavy, integration-light
3105 unit tests + `mypy --strict` + `ruff` is strong for *logic*, but there is
**no integration/E2E in CI** (integration tests need real keys and are excluded).
All end-to-end evidence is manual runs against two repos (zod, forgejo). The
gates are validated on those corpora; behavior on an unrelated production fork is
*extrapolated*, not measured. The "0 false positives on 724 zod files" result for
#1 is encouraging but corpus-specific.

### 6. Heuristics that can mis-fire (all fail-safe, but noisy)
- The #10 symbol-sequence alignment guard falls back to positional pairing when
  symbols are sparse; a pathological equal-count boundary shift the guard can't
  see still relies on the #3 seam-balance gate to catch corruption.
- The #11 line-level check is set-based and whitespace-insensitive; a fork that
  legitimately rewrites its own lines could be flagged (→ extra re-analysis).
- The #12 fabricated-symbol gate trusts the lexical guard; a rare FP escalates a
  clean file.
None corrupt output; all add human-review volume.

## Risk matrix (post-Wave-3)

| Failure mode | Before | Now | Residual |
|---|---|---|---|
| Brace-imbalanced merge → COMPLETED | open | gated (#1/#3/#10) | exotic regex/lifetime edge cases |
| Type-error / undefined-ref merge → COMPLETED | open | only if `build_check` configured | **open without build_check** |
| Hallucinated member → COMPLETED | one path | all paths (#2/#5/#6/#12) | non-member-shape hallucinations |
| Truncated Judge verdict → silent PASS | open | fail-closed (#3A) | analyst/commit-round meta-gating (#3B deferred) |
| Silent fork-feature loss | partial | wholesale + partial + drained files (#11) | sub-threshold loss on non-security files |
| Chunk mispairing/corruption | open | escalate (#10) | full content-anchored pairing not done (guard + seam gate instead) |
| Runaway cost on unpriced model | open | token ceiling (#8C) | default 8M is generous; tune per target |

## Recommended operating posture

1. **Always configure a real `build_check`** (compile/typecheck) — the
   always-on gate alone does not guarantee compilability.
2. **Run with `--ci` output / read `state.errors`** — partial-failure is now
   visible (#7B), but only if you look.
3. **Treat `AWAITING_HUMAN` as the expected outcome on complex forks**, not a
   failure. The system's value is a *triaged, safe* queue with rationale +
   preservation/grounding flags, not autonomous resolution.
4. **Tune `max_tokens` / `chunk_size_chars` to the model** before a large run,
   or large files will escalate on truncation.
5. **Add an integration smoke run to CI** against a fixed fork pair before
   trusting behavior on a new target.

## Bottom line

Waves 1–3 moved the system from **fail-OPEN (silently shipped bad merges)** to
**fail-SAFE (escalates them)**. That is the correct and necessary direction, and
the confirmed corruption paths are closed. But "fail-safe" is not "capable of
autonomous complex merges": correctness on the files it *does* auto-merge still
rests on a configured build gate plus a fallible Judge LLM, and its safety on
hard files rests on escalating them to humans. **Production use: yes, as a
safety-gated assistant with a human reviewing the escalation queue and a real
build gate wired in. Autonomous hands-off merging of a complex divergent fork:
not yet, and the architecture honestly should not pretend otherwise.**

## E2E confirmation of this assessment (run `bf72bb3e`, 2026-05-29)

A full post-Wave-3 run on `test/fork ← test/upstream` (zod, deepseek-v4-pro,
`build_check = pnpm run build`) ended `needs_human` / judge_verdict=**FAIL** /
24 auto-merged / **0 failed** — and the *reason* is the strongest possible
evidence for the cautions above:

- The FAIL was a single CRITICAL **`build_check_failed`**: `tsc` caught
  `TS2339: Property 'fallback' does not exist on type 'ParsePayload<any>'`
  (10 errors) in `classic/schemas.ts`. This file is **brace-balanced** (the
  always-on #1 gate passed it) and uses only **real** symbols (the lexical
  hallucination guards correctly did not flag `payload.fallback`). **Only the
  configured `build_check` caught it.** Had `build_check` been unset, this
  uncompilable merge would have reached `COMPLETED`. → confirms §1 and §2
  empirically, not just in theory.
- The system did the right thing: it escalated, did not corrupt. No new Wave-3
  gate produced a false escalation (all structural/grounding escalations fired
  0 times); the large `core/schemas.ts` escalated on the truncation gate (the
  §4 max_tokens caveat), and all fork features survived (§ none lost).
- **Read this as proof the safety net works AND as proof of its dependence on a
  configured build gate** — exactly the operating posture above. The auto-merge
  "success" number (24) is meaningless on its own; the merge as a whole was
  correctly rejected because one file didn't typecheck.
