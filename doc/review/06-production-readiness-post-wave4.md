# Production-Readiness Re-assessment — post Wave 4 (strict)

Date: 2026-05-29, after Waves 1–4. This re-runs the deliberately-skeptical
assessment of [`03-production-readiness.md`](03-production-readiness.md) given
the Wave-4 hardening ([`04`](04-production-hardening-plan.md) plan,
[`05`](05-wave4-implementation-log.md) what-shipped). 03 remains the historical
pre-Wave-4 baseline; this is the current verdict. The bar is unchanged: **can
this perform complex merges on a production-grade repo, unattended, and be
trusted?**

## One-line verdict (updated)

Still a **safe human-in-the-loop merge assistant** — Wave 4 did not make it an
autonomous complex-merge bot, and the architecture still should not pretend
otherwise. What changed is that the assistant is now **honest about its own
blind spots**: it can no longer report a clean green `COMPLETED` when a safety
gate silently failed to run (P1), when a Judge verdict was truncated (P2), or
when the compile gate it depends on is absent (P3) — and it warns about a
known-undersized config *before* the run instead of failing mid-merge (P4), with
the whole flow now exercised in CI (P5). Net: the same safety posture, with the
**silent-failure surface closed and the dependencies made visible**.

## What Wave 4 changed, against 03's named limitations

| 03 limitation | Wave-4 outcome |
|---|---|
| **§1 always-on gate is balance-only; uncompilable merge → COMPLETED without `build_check`** | **Narrowed, not eliminated.** Still true that real compile-correctness needs a configured gate. But a gate-less compiled-language merge now reports `partial_failure` (P3a advisory) and can be hard-routed to `AWAITING_HUMAN` (P3b opt-in), and the absence is warned at run start (P4). The dependency is loud, not silent. |
| **§2 hallucination guards are lexical, not semantic** | **Unchanged (inherent).** No new semantic check; deliberately so (a fragile pseudo-semantic guard would mask real defects). The only semantic nets remain `build_check` + the fallible Judge LLM — and P3 makes that dependency explicit. |
| **§3 "safe" largely means "escalates a lot"** | **Unchanged (intentional).** P1–P3 add *more* escalation/advisory paths, never fewer. The hands-off auto-merge rate on hard files stays intentionally low. |
| **§4 config sensitivity high, under-documented at point of use** | **Closed for the high-value knobs.** P4 surfaces the `max_tokens`↔`chunk_size_chars` self-truncation risk and the reasoning-model floor on every run (validated live on the zod config). Preservation thresholds documented in 05. |
| **§4 (last bullet) gates degrade-to-skip on git failure with NO alarm** | **Closed.** P1 records every unambiguous gate-skip into `state.errors` → `partial_failure`. A systemically broken `git_tool` can no longer yield a silent clean `COMPLETED`. |
| **§5 validation is unit-heavy, integration-light; no E2E in CI** | **Substantially closed.** The hermetic mocked-LLM orchestrator E2E is repaired and wired into CI on every PR (P5); a nightly cron is enabled. *Correction:* 03's claim that the integration tests "need real keys" was factually wrong — they are fully mocked (and had been silently broken). |
| **§6 heuristics can mis-fire (fail-safe, noisy)** | **Unchanged (acceptable).** Every Wave-4 gate adds only advisories/escalations, never deletions; the live zod E2E showed 0 false escalations from the new gates. |

## Updated risk matrix (post-Wave-4)

| Failure mode | 03 (pre-W4) | Now (post-W4) | Residual |
|---|---|---|---|
| Type-error merge → COMPLETED | open without `build_check` | **visible** (P3a partial_failure) / **gated** (P3b opt-in) / warned (P4) | correctness still needs a configured compile gate (inherent §2) |
| Silently-skipped gate (git misconfig) → green | open (no alarm) | **partial_failure** (P1) | only *unambiguous* skips alarmed — an `absent-at-ref` vs `git-broken` `None` is still conflated by `git_tool` (see carry-forward) |
| Truncated per-file Judge verdict → silent PASS | open (#3A covered only batch) | **fail-closed veto** (P2) | partial-but-valid JSON (earlier balanced object) needs the `stop_reason` gate (not done) |
| Truncated commit-round analysis | open (silent empty) | **logged unambiguously**, files escalate/DROPPED | routing unchanged; explicit signal only |
| Behavior on a new fork → extrapolated | open (no CI E2E) | **measured in CI** (hermetic E2E every PR) | corpus small; 12 drifted tests xfailed; no real-key nightly merge yet |
| Runaway cost on unpriced model | token ceiling (#8C) | unchanged | default 8M generous; tune per target |

## The honest residuals after Wave 4 (carry-forward)

1. **Semantic correctness still rests on `build_check` + the Judge.** This is
   architectural, not a bug. P3 makes the dependency loud; it does not remove it.
   *Operating rule stands: never run a production compiled-language merge without
   a real `build_check`.*
2. **P1 alarms only unambiguous skips.** `get_file_hash → None` conflates
   "git broken" with "file absent at ref", so P1 deliberately does not alarm every
   `None` (it would over-escalate on legitimately-absent files). A complete fix
   needs `git_tool` to distinguish the two (sentinel vs `None`) — tracked.
3. **P2 closes truncation-breaks-JSON, not partial-but-valid JSON.** The
   `stop_reason`-level gate (plan §P2 Change B) is not done; the executor's gate-1
   proves the pattern if needed. In practice the analyst's retry layer absorbs
   most provider truncations before the parser (observed live).
4. **12 integration tests are `xfail`** (documented in 05) for pre-existing
   routing drift, not a Wave-4 regression. Restoring them (re-script mocks or move
   to a real `git_bootstrap` fixture) is a tracked maintenance task; the real-repo
   E2E is the higher-fidelity check meanwhile.
5. **Compile-gate predicate is conservative** — any configured gate suppresses the
   P3 advisory, even a linter-only gate that does not typecheck. Per-language
   precision is a tracked refinement.

## Recommended operating posture (unchanged, now enforced/visible)

1. **Always configure a real `build_check`** — now warned at run start (P4) and
   flagged at report time (P3a) if absent; set `require_for_compiled_langs: true`
   (P3b) for a hard "never green without a compile gate" posture.
2. **Read `state.errors` / the exit code** — `partial_failure` (exit 30) now fires
   on silently-skipped gates and missing compile gates, on `--ci`, resume, AND the
   interactive tail (P1 closed the `run.py` parity gap).
3. **Tune `max_tokens` / `chunk_size_chars`** before a large run — P4 warns when
   `chunk_size_chars >= executor.max_tokens * 1.4` (the self-truncation boundary).
4. **Treat `AWAITING_HUMAN` as the expected outcome on complex forks.**

## E2E confirmation (live zod run, 2026-05-29, post-Wave-4)

Run config: `test/fork ← test/upstream`, deepseek-v4-pro,
`chunk_size_chars=12000`, executor `max_tokens=8192`, `build_check = pnpm run
build`. Run `57d3f131`, ~19 min wall-clock (slow deepseek proxy), 14 LLM calls /
174,520 input + 6,584 output tokens. **Terminal CI summary:**

```json
{"status": "needs_human", "total_files": 23, "auto_merged": 23,
 "human_required": 4, "human_decided": 0, "failed_count": 0,
 "judge_verdict": "none", "errors": []}
```

Observations confirming Wave-4 behavior in production:

- **P4 preflight fired on the real config, in `--ci`** — the run's stdout opened
  with `WARNING: chunk_size_chars=12000 meets/exceeds the executor's output
  budget (max_tokens=8192 → ~11468 chars)…` (not only `merge validate`). The
  compile-gate advisory is correctly **silent** because `build_check` is set.
- **Zero false signals from the new gates** — `errors: []` and `failed_count:
  0` on a healthy run that escalated 4 C-class files to human. P1/P2/P3 added no
  spurious partial-failure / gate-skip / veto noise — the single most important
  check (a buggy Wave-4 gate would have left a spurious `state.errors` entry here).
- **Correct safe escalation** — the system staged the auto-mergeable files and
  routed the 4 conflict files to `AWAITING_HUMAN` (`needs_human`) rather than
  silently auto-merging them. "Escalate, never corrupt" held.
- **#8A token win holds** — conflict-analysis prompts were ~25–69 KB (the run
  log), not the pre-#8A ~219 KB; total analyst input ~157 K tokens across 9
  calls, far under the #8C 8 M ceiling. Cost `$0` (deepseek untracked) — the
  token ceiling, not the dollar cap, is the real guard, exactly as documented.
- **Truncation handled gracefully** — one analyst call returned
  `finish_reason='length'`; the retry layer absorbed it (attempt 2/3 succeeded),
  so P2's parser-level fail-closed was the backstop, not needed here.

> This run escalated at the conflict/human-review stage (its scenario has C-class
> files that need human decisions), so it did not reach `build_check`. The prior
> Wave-3 E2E (`bf72bb3e`) covers the other terminal — an all-auto-merge run that
> ends verdict=FAIL on a `build_check_failed` CRITICAL when the merged tree does
> not typecheck. Together they show both halves of the §1/§2 dependence on a
> configured compile gate, now made loud by Wave 4.

## Bottom line

Waves 1–3 moved the system from **fail-OPEN** to **fail-SAFE**. Wave 4 makes
fail-safe **honest**: the safety net can no longer pass silently when it didn't
run, when a verdict was truncated, or when its compile-gate dependency is absent,
and it says so before a known-bad config bites. Production use: **yes, as a
safety-gated assistant with a human on the escalation queue and a real
`build_check` wired in** — now with the silent-failure paths closed and the
dependencies surfaced. Autonomous hands-off merging of a complex divergent fork:
**still not, by design.**
