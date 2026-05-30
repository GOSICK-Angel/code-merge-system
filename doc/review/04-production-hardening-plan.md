# Production-Hardening Plan — closing the Wave-3 residual gaps (Wave 4)

> **STATUS (2026-05-29): P1–P5 all shipped.** This document is the original
> forward plan; for what actually shipped (trade-offs, scope decisions,
> carry-forwards) see [`05-wave4-implementation-log.md`](05-wave4-implementation-log.md),
> and for the strict post-Wave-4 re-assessment see
> [`06-production-readiness-post-wave4.md`](06-production-readiness-post-wave4.md).

Date: 2026-05-29. This is the **closure plan** for the residual gaps named in
[`03-production-readiness.md`](03-production-readiness.md). 03 argued the
skeptical case ("can this merge unattended and be trusted? — not without a build
gate + a human"); this document turns each named limitation into a concrete,
file-anchored initiative, ranked by leverage/risk, with insertion points,
trade-offs, and a validation plan — the same discipline as
[`01-optimization-plan.md`](01-optimization-plan.md) /
[`02-implementation-log.md`](02-implementation-log.md).

Every claim below was re-grounded against the current `feat/web` working tree
(Waves 1–3 + the 批次D structured-output work are committed). Where the code
contradicts 03, that correction is stated explicitly.

## Principles (extending "escalate, never corrupt")

Wave 4 adds two rules to the Wave 1–3 principle:

1. **No silent skip.** A deterministic gate that *cannot run* (git read failed,
   blob unreadable, no compile toolchain configured) must record that it was
   skipped and flip the run to `partial_failure` — never let "the gate didn't
   fire" read as "the gate passed."
2. **Make the safety net's dependencies visible.** The system's correctness on
   auto-merged files rests on two operator-supplied inputs — a real `build_check`
   and an adequate `max_tokens` — and today both fail *silently* when absent or
   undersized. Surface them at the point of use.

No Wave-4 change auto-resolves more aggressively, and none deletes content.

---

## Verified corrections to 03 (what the code actually shows)

The grounding surfaced material corrections that reprioritize the work:

- **§5 is partly wrong — and hides a live regression.** 03 says "integration
  tests need real keys and are excluded." In fact `tests/integration/` is
  **fully mocked** (it patches `_call_llm_with_retry` + `GitTool`) and a
  hermetic full-orchestrator E2E **already exists** (`tests/integration/
  test_happy_path.py:22-43`, canned payloads in `conftest.py:11-155`,
  `FakeGitTool:161-198`). It is excluded from CI *by omission*, not by key-gating
  — and it is **currently broken**: `enable_working_branch` now defaults `True`
  (U7) so `orchestrator.py:263` calls `git_tool.create_working_branch(...)`, a
  method `FakeGitTool` never gained → the whole directory fails to import-run.
  This rot went undetected *because CI never runs the directory* — the cleanest
  possible evidence for the gap. The hermetic E2E is ~90 % built; this is
  repair + wire, not greenfield.
- **§1 build_check — the hole is narrower than "always silent."** Setup
  auto-detects and enables `build_check` for TS/Go/Rust repos
  (`setup.py:357-367` via `_detect_build_check_command:287-319`). The live hole
  is **undetectable / hand-edited / gate-less** configs, plus the fact that an
  operator can satisfy the compile gate via the `gate` subsystem (a
  `tsc --noEmit` command + `tsc_errors` baseline parser) instead of `build_check`
  — so the absence test must check **both**.
- **#3B truncation — structured outputs do NOT cover it, and the live fail-open
  is the per-file Judge path.** `use_structured_outputs` defaults **OFF**
  (`config.py:67`) and is never flipped on in production, so the default path is
  still the legacy parser. Worse, the structured path is *itself* truncation-blind
  (`client.py:481-483/710/720` discard `stop_reason`/`finish_reason`;
  `ModelOutputError` only fires from `complete_structured`, which no agent uses).
  #3A hardened **only** the Judge *batch* path (AUTO_MERGING). The default
  `JUDGE_REVIEW` *per-file* path (`judge_agent.py:352`→`review_file:161-206`),
  the commit-round analysis (`parse_commit_round_analyses` → empty dict on
  `ParseError`), and the conflict analyst (confidence silently 0.5/0.3) remain
  truncation-blind. The "schema=None forced by meta" blocker in 02 is real but
  **irrelevant** — the fix belongs at the parser layer (like #3A), not the meta
  layer.
- **§4 chunk coupling is executor-only.** The #9D coupling
  (`executor_agent.py:586-605 _effective_chunk_size`, automatic, reads
  `config.agents.executor.max_tokens`) does **not** apply to the conflict
  analyst's chunked path (`conflict_analyst_agent.py:122→261`), whose default
  `max_tokens=4096` is far below the executor's 32768 — so analyst chunked
  analysis can self-truncate unprotected.
- **§4 surfacing is mostly already wired.** `state.errors` already flips
  `partial_failure` (exit 30) on `--ci` (`run.py:50-58` → `ci_reporter.py:56,67`)
  and on the production-reachable resume/browser terminal (`resume.py:236-253`,
  the #7B fix). Only `run.py:196-197`'s non-CI interactive tail still prints
  "successfully!" unconditionally — a consistency gap, not the linchpin.

---

## Gap → initiative map

| 03 gap | Wave-4 initiative | Kind |
|---|---|---|
| §4 "no 'gates were silently skipped' alarm" | **P1** silent gate-skip alarm | code (gating) |
| risk-matrix "Truncated verdict → analyst/commit-round (#3B deferred)" | **P2** truncation fail-closed on the still-blind consumers | code (parsers/agents) |
| §1/§2 "open without build_check" | **P3** build_check dependency made visible + opt-in enforced | code (gating/config) |
| §4 "config sensitivity high, under-documented at point of use" | **P4** config preflight on every run | code (cli) |
| §5 "no integration/E2E in CI" | **P5** repair + wire hermetic E2E, add gate assertions, secret-gated nightly | tests/CI |
| §2 non-member hallucination · §3 escalation rate · §6 heuristic mis-fire | **monitor & document** (not closable by code) | docs/telemetry |

---

## Ranked initiatives

### P1 — "Silent gate-skip" alarm  ·  closes §4 (third bullet)  ·  effort M · risk LOW

**Problem (grounded).** Every deterministic gate that reads git content
degrades-to-skip *silently* on a read failure. Shared primitive:
`git_tool.get_file_content / get_file_hash / get_worktree_blob_sha` swallow
`GitCommandError → None` (`git_tool.py:41-67,320-336`); `three_way_diff.
_safe_read_text` swallows `(UnicodeDecodeError, OSError) → None`
(`three_way_diff.py:9-21`). Gates then treat `None` as "no content" and `continue`.
A systematically misconfigured `git_tool` thus **disables a whole class of gates
with zero signal**. Worse, `auto_merge.py:1642` logs `len(drift)/checked` but
increments `checked` *before* the `sha is None: continue`, so a total git failure
prints `0/N drift` — indistinguishable from "clean." There is **no**
`gates_skipped` counter anywhere (verified by grep).

Degrade-to-skip sites (representative, with file:line):

| Layer | Gate | Skip site |
|---|---|---|
| Executor (writes state) | additive fork-export preservation | `executor_agent.py:655-662` (`except → base_content=None`) |
| Judge (**read-only**) | deterministic-pipeline master guard (covers B/C/D/TODO checks at once) | `judge_agent.py:526-527` (`if not merge_base or not upstream_ref: return []`) |
| Judge (**read-only**) | invented-symbol veto / duplicate-symbol veto (merged blob unreadable) | `judge_agent.py:1130-1131` / `1078-1080` |
| auto-merge phase | preservation wholesale-drop / line-level partial-drop | `preservation_auditor.py:190-192` / `216-228` (+`96-110`) |
| auto-merge phase | O-B5 B-class drift sanity | `auto_merge.py:1632-1637` (+ misleading log `:1642`) |
| report verification | post-merge dup-symbol / dropped-export | `merge_verification.py:119-120` |

**Design — append to `state.errors`, reuse the partial_failure channel.** No new
state field, no new `SystemStatus`. Use a stable phase tag and message prefix:
`{"phase": "gate_skip", "message": "GATE_SKIPPED [<gate>] <path>: git read of <ref> failed"}`.
This is exactly the shape `report_generation._run_deterministic_verification`
already writes (`report_generation.py:54-60`).

- **Non-reviewer sites write directly.** Executor (A), auto-merge phase + the
  preservation auditor's caller (C), and report verification (E) all hold
  `state`/`ctx` and already append to `state.errors` elsewhere — add the
  `gate_skip` append at each `except`/`continue`.
- **Judge is read-only** (`ReadOnlyStateView.__setattr__ → PermissionError`,
  `read_only_state_view.py:66-78`) — it must **not** touch `state.errors`.
  Accumulate `self._skipped_gates: list[str]` during the deterministic checks and
  ship them in the `PHASE_COMPLETED` payload (`judge_agent.py:234-241`,
  alongside `verdict`); the **phase** (`judge_review.py:59-64`, which already
  writes `state.judge_verdict` from the payload) appends them to `state.errors`.
  The single highest-value insertion is the master guard `judge_agent.py:526-527`
  — one record there covers a total-pipeline blackout.
- **Surfacing.** Already wired on `--ci` and resume/browser. For consistency,
  mirror `resume.py:236-253` into `run.py:196-197` (check `final_state.errors`
  before printing "successfully!"; exit `EXIT_PARTIAL_FAILURE`).

**Trade-off / risk.** A flaky-but-recoverable git read now produces a
`partial_failure` exit even if the merge itself is fine — acceptable (the
operator *wants* to know a gate was blind), and far better than silent
disablement. Skip in `state.dry_run` to match the sibling verification helpers.

**Validation.** Unit test: patch `git_tool.get_file_content` to raise on a
specific path, run the auto-merge/judge gating, assert a `gate_skip` entry lands
in `state.errors` and the run exits 30 (CI) / prints "WITH WARNINGS" (resume).
Also fix the `auto_merge.py:1642` log to count only genuinely-checked files.

---

### P2 — Truncation fail-closed on the still-blind consumers  ·  closes the #3B residual  ·  effort M · risk LOW–MEDIUM

**Problem (grounded).** Three production consumers parse a truncated LLM response
as a clean result:

| Consumer | Call site | Current truncation behavior |
|---|---|---|
| Judge **per-file** review (default `JUDGE_REVIEW`) | `judge_agent.py:352`→`review_file:161-206` | `parse_file_review_issues` `ParseError` → just **logs** (`:364-365`) → "no issues" → incomplete `all_issues` before the verdict is even computed |
| Commit-round analysis | `conflict_analyst_agent.py:602` | `parse_commit_round_analyses` `ParseError` → **empty dict** (`response_parser.py:581-582`); partial JSON → dropped files |
| Conflict analyst (single + chunked) | `:347` / `:446` | `parse_conflict_analysis` → `confidence` silently defaults **0.5**; unparseable → 0.3 record — indistinguishable from a genuine low-confidence answer |

#3A's fail-closed covers **only** the Judge *batch* path
(`parse_batch_file_review_issues(strict_json=True)`, `response_parser.py:655,
683-684`; synth veto `judge_agent.py:1785`). Structured outputs do not help (see
the §correction above).

**Design — parser-level `strict_json`, mirroring #3A (NOT the meta path).** The
02 blocker ("`schema=None` forced by the meta call") only blocks the
*meta-call* approach; the gate belongs where #3A already put it — at the parser.

- **Change A (the minimal, recommended close):**
  - Add `strict_json: bool = False` to `parse_commit_round_analyses`
    (`response_parser.py:573`) and re-raise on the `ParseError` (`:581-582`) when
    set — exact `parse_batch_file_review_issues` shape.
  - Add the same flag to `parse_file_review_issues`; have `review_file` pass it
    (`judge_agent.py:357`) and convert the log-and-continue except
    (`:364-365`) into a synthesized **CRITICAL `review_unavailable` veto**,
    mirroring `_review_files_batch_llm:1798-1812`. This closes the live per-file
    fail-open.
  - Analyst: in `_analyze_file`/`_chunked_analyze_file`, treat a truncation-class
    `ParseError` as a **forced `ESCALATE_HUMAN`** (it already escalates at 0.3 —
    make truncation *unambiguous* rather than mimicking a real low-confidence
    answer). Insertion: `conflict_analyst_agent.py:362-373` and `:483-494`.
- **Change B (optional, hardens the analyst like the executor):** when
  `use_structured_outputs` is off (`_structured_kwargs → {}`), route analyst
  single-shot/commit-round/judge-per-file through `_call_llm_with_retry_meta`
  (`base_agent.py:540-570`) and pass the `LLMResponse` (not `str(raw)`) into a
  `stop_reason`-aware parser — exactly the executor's gate-1
  (`executor_agent.py:510-513`; the reusable duck-typed gate is
  `response_parser.py:375-384`).

**Known sub-residual to state honestly.** `_extract_json` salvages via
`find("{")`/`rfind("}")` (`response_parser.py:35-40`), so a truncation that still
leaves an *earlier* balanced object yields partial-but-valid JSON that
`strict_json` will not catch. The dominant case (truncation breaks JSON) is
covered by Change A; the partial-but-valid case needs Change B's `stop_reason`
gate. Document this rather than pretend Change A is total.

**Trade-off / risk.** Undersized `max_tokens` now escalates more — pair with **P4**
(the preflight warns before it bites). LOW–MEDIUM.

**Validation.** Per-consumer unit tests feeding (a) JSON-breaking truncation and
(b) a `stop_reason='max_tokens'` `LLMResponse`, asserting a veto / forced
escalation rather than a clean parse.

---

### P3 — Make the build_check dependency visible and optionally enforce it  ·  closes §1/§2  ·  effort M · risk LOW (a/c) / MEDIUM (b)

**Problem (grounded).** When `build_check` is unset, `judge_review.py:352-354`
returns silently; a brace-balanced merge that does not typecheck flows
PASS → `GENERATING_REPORT` → **COMPLETED exit 0**
(`report_generation.py:230-237`). The always-on per-file gate is balance-only by
design (`syntax_checker.py`, real parsers only for `.py/.json/.yaml`;
`.ts/.go/.rs/...` get bracket/string balance via `_BALANCE_SPECS:160-169`). There
is **no** type-aware net in-repo beyond `build_check` and the operator-configured
`gate` subsystem (the `baseline_parsers` — `tsc_errors/mypy_json/...` — *parse*
an operator-supplied command's output; they run nothing themselves). The E2E in
03 (run `bf72bb3e`) is the live proof: only the configured `tsc` caught
`TS2339 payload.fallback`.

**Design (three layers — ship a + c now; b is opt-in).**

- **(a) Loud advisory → `state.errors`.** When the auto-merged set
  (`file_decision_records` with a `TAKE_*`/`SEMANTIC_MERGE`/`MANUAL_PATCH`
  decision and `decision_source != HUMAN`) contains a compiled-language file
  (`Path(fp).suffix in set(_BALANCE_SPECS)`) **and neither** `build_check.enabled`
  **nor** a compile-capable `gate.commands` entry is configured, append a
  `no_compile_gate` advisory to `state.errors`. Insertion:
  `report_generation.py`, a new helper next to `_run_deterministic_verification`
  (which already appends `state.errors`) → `partial_failure` / exit 30. Skip in
  `state.dry_run`.
- **(b) Opt-in soft gate (default OFF).** New `BuildCheckConfig` field
  `require_for_compiled_langs: bool = False`. When set and (a)'s condition holds,
  reroute `JUDGE_REVIEWING → AWAITING_HUMAN` in `judge_review.py` (the PASS branch,
  after `_run_build_check`/`_run_smoke_tests`, before the `GENERATING_REPORT`
  transition). This is a *legal* edge; `GENERATING_REPORT → AWAITING_HUMAN` is
  **not**, so the gate must live in `judge_review`, never `report_generation`.
  The transition is phase-level (Orchestrator layer), so the read-only-reviewer
  rule is untouched; it matches the documented "files need human sign-off →
  AWAITING_HUMAN" routing.
- **(c) Preflight nudge.** Fold into **P4**: when no compile gate is configured,
  warn at run start and suggest `_detect_build_check_command(repo_path)`'s result
  as the command to add.

**Why check both `build_check` and `gate`.** An operator running `tsc --noEmit`
as a `gate.commands` entry with the `tsc_errors` baseline parser already has a
compile gate even with `build_check` unset — (a)/(b) must not nag them.

**Trade-off / risk.** (a)/(c) are advisory → LOW (turns a previously-green
gate-less compiled merge into exit 30 — intended; note in release notes). (b)
behind a default-off flag preserves current behavior → MEDIUM only for operators
who opt in.

**Validation.** Unit tests: compiled-lang auto-merge + no gate → `no_compile_gate`
in `state.errors` + exit 30; same with `require_for_compiled_langs=True` →
`AWAITING_HUMAN`; with a `tsc_errors` gate command configured → no advisory.

---

### P4 — Config preflight on every run  ·  closes §4 (first/second bullets) + posture #4  ·  effort S–M · risk LOW

**Problem (grounded).** `validate_config_warnings` (`main.py:413-432`) checks
**only** tree-sitter, and runs **only** on `merge validate` — a normal `merge`
run calls just `_preflight_check_api_keys` (`run.py:157`). There is **no**
`max_tokens`-vs-`chunk_size` self-truncation warning anywhere, so chunks that
will self-truncate are discovered only when the truncation gate fires at runtime.
The preservation thresholds (`preservation_min_fork_lines=50`,
`preservation_fork_survival_floor=0.7`, `config.py:222-248`, read at
`preservation_auditor.py:147-156`) are config-driven but undocumented at the
point of use.

**Design.**

- **Relocate + always-run.** Move `validate_config_warnings` into a new
  `src/cli/preflight.py` (breaks the `main ↔ run` import cycle); call it from
  run start (after `_preflight_check_api_keys`, `run.py:157`) **and** keep it in
  `merge validate`.
- **Self-truncation warning (highest value), for BOTH agents.** For
  `agent ∈ {executor, conflict_analyst}`, warn when
  `chunk_size_chars > agent.max_tokens * 1.4` (the #9D heuristic from
  `executor_agent.py:604`), with the analyst caveat: *the executor auto-clamps
  via #9D; the analyst does not — its chunked analysis truncates unprotected.*
  Suggest the fix (raise `max_tokens` to `≥ chunk/1.4` or lower
  `chunk_size_chars`).
- **Reasoning-model floor warning.** Surface the reactive client auto-bump
  (`client.py:899-912`, `_OPENAI_REASONING_MIN_MAX_TOKENS=32768`) as a preflight:
  warn when a `gpt-5*/o1/o3/o4` agent has `max_tokens < 32768`.
- **build_check-absence advisory** — the P3(c) hook.
- **Document the preservation thresholds** in the preflight/validate output and
  in CLAUDE.md's Configuration section.
- **Optional follow-up — make the analyst clamp automatic.** Factor
  `_effective_chunk_size` into a shared helper (e.g. in `chunk_processor.py`)
  consumed by both agents, OR clamp at `conflict_analyst_agent.py:122`. Gate this
  behind a cost check first (finer analyst chunks → more LLM calls); ship the
  warn-only preflight now.

**Trade-off / risk.** Advisory only → LOW. The one structural change is the
`preflight.py` extraction to avoid the import cycle.

**Validation.** Unit test feeding an undersized-`max_tokens` config and asserting
the self-truncation + reasoning-floor warnings are emitted; assert the executor
path is exempted when its own coupling already covers it.

---

### P5 — CI integration smoke: repair, wire, assert, nightly  ·  closes §5  ·  effort M · risk LOW

**Problem (grounded).** `.github/workflows/ci.yml` runs `web-build` + `test`
(`pytest tests/unit/ ... --cov-fail-under=80`, `:78`) + a `workflow_dispatch`-only
`eval-tier1` (`continue-on-error`). `tests/integration/` is referenced by **no**
job, the nightly `cron` is a commented placeholder (`:9-12`), and the existing
eval E2E routes through a `fake_merge.sh` stub — so **no CI job exercises the
real `src/` gates end-to-end**. And the mocked integration harness is currently
**broken** (`FakeGitTool.create_working_branch` missing → `orchestrator.py:263`).

**Design — two tiers.**

- **Tier A — hermetic mocked-LLM E2E (zero keys, blocks PRs).** ~90 % built.
  1. **Repair `FakeGitTool`**: add `create_working_branch(branch, base_ref) → str`
     and the remaining git surface the orchestrator/phases call, **or** (higher
     fidelity) back the run with a real repo materialized by
     `scripts/eval/git_bootstrap.py:121 bootstrap_synthetic_repo(t1-0031,
     tmp_path)` and mock **only** the LLM. Both are key-free.
  2. **Encode the 5 highest-value gate assertions** (the exact risk-matrix rows
     03 validates only by manual runs), driving `Orchestrator(config).run(state)`
     (`orchestrator.py:218`) with canned per-phase LLM responses:
     - brace-imbalanced canned merge → escalates, status ≠ COMPLETED (#1/#3/#10);
     - hallucinated-symbol canned merge → `judge_verdict.veto_triggered`, ≠ COMPLETED (#5/#12);
     - C-class low-confidence → `AWAITING_HUMAN` with `human_decision_requests`
       populated and **no auto-filled decision** (the never-auto-fill contract);
     - fork-feature wholesale drop → preservation flag + escalation (#11);
     - truncated Judge verdict → fail-closed, ≠ COMPLETED (#3A, **and the new P2**).
  3. **Wire `pytest tests/integration/`** into the `test` job (no keys, gate on
     PRs like the unit step).
- **Tier B — real-key nightly (non-blocking, secret-gated).** Uncomment the cron
  (`ci.yml:11-12`); new job `if: github.event_name=='schedule' && secrets.* != ''`
  + `continue-on-error: true`; `bootstrap_synthetic_repo` the **t1-0031/0032/0033**
  C-class triple (forgejo `auth_token.go`) and run real `merge --ci`, asserting
  `AWAITING_HUMAN` (`meta.yaml expected_human: true`).

**Also:** fix the stale "needs real keys" claim in 03 §5 and in the
`run-integration` skill — it is incorrect for the current mocked suite.

**Trade-off / risk.** Tests/CI only → LOW. Tier A becomes the **regression net
for all of Wave 4** (encode P1–P3's new behaviors as its assertions).

**Validation.** The suite is the validation; CI green with integration included,
and the previously-broken directory runs.

---

## Inherent limitations — monitor & document, do NOT pretend to close

These three 03 limitations are not closable by adding a gate; pretending
otherwise would add fragile heuristics that *mask* real defects.

- **§2 non-member-shape / real-symbol-wrong-type hallucination.** No lexical
  guard catches `payload.fallback` used against a type that lacks the field; the
  only nets are `build_check` and the (fallible) Judge LLM. **Action:** P3 makes
  the `build_check` dependency loud; document that *hands-off correctness
  REQUIRES a compile gate*. Do **not** add a pseudo-semantic guard.
- **§3 high escalation rate on complex C-class.** Intentional and desirable.
  **Action:** add escalation-rate telemetry (auto vs escalate counts per change
  category) so operators can see the queue shape — but never relax a gate to
  lower the number.
- **§6 heuristic mis-fire (all fail-safe, noisy).** Keep the `*`-suppression /
  set-based discipline; every Wave-4 gate adds only advisories/escalations, never
  deletions.

---

## Sequencing (Wave 4)

1. **P1 + P4 first** — pure visibility, lowest risk; P4's preflight pre-empts the
   extra escalation noise P2 introduces (it warns before `max_tokens` bites).
   Do the `run.py:196` ↔ `resume.py` alignment once, shared by P1 and P3.
2. **P2** — truncation fail-closed; independent, pairs with P4.
3. **P3** — build_check visibility (a/c) + opt-in enforce (b); shares the
   `state.errors`/`partial_failure` plumbing with P1.
4. **P5** — CI smoke; encode P1–P3's new behaviors as the Tier-A assertions so
   the wave ships with its own regression net.

---

## Updated risk matrix (post-Wave-4 target)

| Failure mode | 03 (now) | Wave-4 target | Residual after Wave 4 |
|---|---|---|---|
| Type-error merge → COMPLETED | open without build_check | **visible** (P3a `partial_failure`) / **gated** (P3b opt-in) | correctness still needs a configured compile gate (inherent §2) |
| Silently-skipped gate (git misconfig) → green | open (no alarm) | **partial_failure** (P1) | flaky single-file read → one noisy escalation (acceptable) |
| Truncated Judge per-file / commit-round / analyst → silent pass | open (#3B deferred) | **fail-closed** (P2 Change A) | partial-but-valid JSON needs P2 Change B (`stop_reason`) |
| Analyst chunk self-truncation (undersized max_tokens) | open (executor-only coupling) | **warned** (P4) + optional auto-clamp | warn-only until the auto-clamp follow-up lands |
| Behavior on a new fork → extrapolated | open (no CI E2E) | **measured** (P5 Tier-A on every PR + nightly real-key) | corpus still small (t1-0031..33 + zod); widen over time |

---

## Acceptance criteria

- A run with a systematically broken `git_tool` ends `partial_failure` (exit 30),
  not green, and lists each skipped gate. **[P1]**
- A truncated Judge per-file verdict / commit-round analysis escalates (veto /
  `ESCALATE_HUMAN`), never silently passes. **[P2]**
- A compiled-language auto-merge with **no** compile gate ends `partial_failure`
  (P3a) or `AWAITING_HUMAN` (P3b, opt-in); never silent green. **[P3]**
- `merge` (not just `merge validate`) prints a self-truncation warning when
  `chunk_size_chars > max_tokens*1.4` for the executor **or** analyst. **[P4]**
- CI runs the hermetic integration E2E on every PR with the 5 gate assertions
  green; a secret-gated nightly runs the real-key t1-0031..33 triple. **[P5]**

## Bottom line

Wave 1–3 made the system **fail-SAFE**; Wave 4 makes the safety net **honest** —
it can no longer pass silently when it didn't actually run (P1), when a verdict
was truncated (P2), or when the compile gate it depends on is absent (P3) — and
it tells the operator before a known-undersized config bites (P4) and proves all
of it in CI (P5). None of this makes the system an autonomous complex-merge bot;
03's verdict stands. What changes is that the system's *dependence* on a build
gate and a human reviewer becomes **loud and measured** instead of silent and
extrapolated.
