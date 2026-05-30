# Optimization Plan — 12 Ranked Initiatives

Ranked by (merge-quality impact / effort). Effort: S ≤ ~1 file, M = a few files, L =
invasive. The guiding principle is **fail-SAFE**: convert logged-only / advisory /
fail-open signals into terminal gates that route to `AWAITING_HUMAN`. See
[`02-implementation-log.md`](02-implementation-log.md) for what actually shipped.

| # | Eff | Initiative | Closes theme |
|---|-----|-----------|--------------|
| 1 | S | `check_syntax` non-vacuous for TS/JS/Go/Java/Rust (brace/paren/bracket + string balance) | B |
| 2 | S | Mirror chunked-path guards (invented-symbol + dropped-export + dedup) onto single-shot executor | C, F, H |
| 3 | M | Truncation fail-CLOSED across analyst/judge/commit-round parsers (+ `stop_reason` gating) | D |
| 4 | S | Exclude C-class from native 3-way auto-merge and dep-bump auto-`TAKE_TARGET` | E |
| 5 | M | Judge-side deterministic invented-symbol veto + gate O-J1 skip on a real syntax check | B, C |
| 6 | S | Harden hallucinated-symbol guard: chained-leaf scan + set-based recombination | C |
| 7 | M | Fail-closed on build/smoke launch crash + report-write failure; surface `state.errors` on reachable interactive path | A |
| 8 | M | Stop chunked-analyst base-resend storm + token-based budget ceiling | D (cost) |
| 9 | M | Fix elision/length-floor: untrimmed gate-4 baseline + length-only truncation branch + don't elide single-shot inputs | H |
| 10 | L | Chunked-merge structural alignment: content-anchored pairing, forced-split escalation, seam brace-balance, empty-target pass-through, JS/TS func dedup | G |
| 11 | L | Strengthen fork-preservation auditing: audit drained files, line-level survival check, configurable security floor | F |
| 12 | M | Tighten advisory grounding: gate analyst fabrication channel, broaden diff-facts, alias-aware guard | C, F |

---

## Details

### #1 — `check_syntax` non-vacuous (S) · `src/tools/syntax_checker.py`
Add per-extension lightweight, comment/string-aware balance checkers for
`.ts/.tsx/.js/.jsx/.go/.java/.rs`: count only `{}()[]` (never `<>` — TS generics) and
detect unterminated quotes; empty/whitespace content is valid (matches the JSON/YAML
convention, avoids the placeholder-`HumanDecisionRequest` leak). Return `valid=False` with
the offending line on a clear imbalance. A real parser is delegated to a configurable
`tsc/eslint/gofmt` gate — do **not** embed subprocess I/O in this pure module. All three
Judge consumers and the O-J1 pre-filter inherit the gate automatically; Judge syntax
issues are CRITICAL → deterministic FAIL → `AWAITING_HUMAN`.
**Benefit:** converts a guaranteed false-negative into a real gate across every path for
the entire non-Python surface. Highest leverage per line.
**Risk:** false positives on generics/JSX/regex/template literals → mitigate by counting
only `{}()[]` + quotes and being comment/string-aware.

### #2 — Mirror guards onto single-shot executor (S) · `executor_agent.py`, `feature_preservation.py`
In `execute_semantic_merge`, after the `_foreign_chars` block and before
`apply_with_snapshot`, run the same guards the chunked path uses, against the **UNTRIMMED**
`orig_current_content`/`orig_target_content`: `remove_duplicate_top_level_symbols`,
`find_invented_member_accesses` → escalate on hit, and a `feature_preservation` missing
added-export check (needs a merge-base fetch the executor doesn't currently do).
**Benefit:** closes the dominant unhandled-hallucination + silent-fork-loss surface — the
single-shot ≤20KB path is the most-traveled and currently runs only the non-ASCII check.
**Risk:** low; must use untrimmed sources and add the merge-base fetch or symbols get
mislabeled.

### #3 — Truncation fail-CLOSED (M) · `response_parser.py`, `judge_agent.py`, `conflict_analyst_agent.py`, `base_agent.py`
(A) `parse_batch_file_review_issues` raises `ParseError` on `_extract_json` failure (the
caller synthesizes a per-file CRITICAL `batch_review_unparseable` issue instead of
approving); `parse_conflict_analysis` / `parse_commit_round_analyses` treat **absent**
`confidence` (key presence, not truthiness — `0.0` is valid) as fail → existing
`missing_files → ESCALATE_HUMAN@0.3` path. (B) Route judge/analyst/commit-round through
`_call_llm_with_retry_meta` and reject `stop_reason ∈ {max_tokens, length}` before parsing
(mirrors the executor Gate-1). Meta forces `schema=None` (incompatible with structured
json_schema) → apply meta-gating only on the legacy path; structured path raises
`ModelOutputError` on truncation. Judge fails closed; analyst may retry-with-larger-budget
once before escalating.
**Benefit:** eliminates the worst fail-open class (truncated Judge batch → silent PASS) and
the `confidence=0.5` directional auto-merge that drops fork content.
**Risk:** medium — too-low `max_tokens` could push fine layers to `AWAITING_HUMAN`; pair
with a single retry-with-larger-budget. Respect the structured/meta incompatibility.

### #4 — Exclude C-class from deterministic blends (S) · `auto_merge.py`
Native 3-way loop (570-626): `if change_category == C: continue` so the file flows to the
deferred conflict-analysis / executor SEMANTIC_MERGE path (enforces the documented invariant
at 1123-1126). Dep-bump loop (1094): keep the lock-file branch, but require `cat != C` before
`TAKE_TARGET` on hand-edited manifests.
**Benefit:** removes the WRONG_MERGE / silent-fork-loss class on both-sides-modified files.
**Risk:** low; routes C-class to its intended home. Slightly more LLM analysis (correct).

### #5 — Judge invented-symbol veto + gate O-J1 skip (M) · `judge_agent.py`
(A) Only allow the high-confidence skip when the file's extension is genuinely checkable
(registered-checker set, post-#1). (B) Wire `find_invented_member_accesses` into
`_run_deterministic_pipeline` over `file_decision_records` (skip SKIP/missing/empty), reading
merged worktree via `_safe_read_text` and fork+upstream via `get_file_content`; emit CRITICAL
with `veto_condition` → deterministic FAIL, path-independent.
**Benefit:** makes hallucinated-member detection a Judge-side blocking veto regardless of merge
path; defense-in-depth behind #2.
**Risk:** low-medium; `.ts/.js` semantic merges now always pay LLM-review cost.

### #6 — Harden hallucinated-symbol guard (S) · `hallucinated_symbol_guard.py`
(A) Match full dotted chains, split on `.`, test every adjacent `(parent, child)` pair (so
`core.schemas._isoWeek` forms `core.schemas` AND `schemas._isoWeek`). (B) Replace the `ref in
src` substring test with membership in a precomputed set of qualified-ref tokens per source
(kills the `core._isoWeekFoo` longer-identifier leak). Optional: comment/string stripping.
**Benefit:** closes the exact zod chained-leaf shape and the substring-whitelist bypass; pure
function, both call sites unchanged.
**Risk:** low; set membership strictly improves over substring.

### #7 — Fail-closed gates + visible partial-failure (M) · `judge_review.py`, `resume.py`, `report_generation.py`, `judge_agent.py`
(A) build_check/smoke launch-exception → don't bare-return; synthesize CRITICAL
`build_check_failed` + verdict FAIL+veto (→ `AWAITING_HUMAN`). (B) On the reachable interactive
terminal (`resume.py`), after the success print, `if final_state.errors:` print a warning
summary and `sys.exit(EXIT_PARTIAL_FAILURE)`. (C) Route `missing_additive_export` CRITICAL
deterministic findings through the Judge (FAIL → `AWAITING_HUMAN` + repair) instead of a
post-commit report line.
**Benefit:** closes "gate that cannot run fails open"; makes the dropped-export detector a real
gate; makes partial_failure visible without `--ci`.
**Risk:** low-medium — report-write failure must become "completed with warnings" +
EXIT_PARTIAL_FAILURE, **not** FAILED (the merge may have landed).

### #8 — Stop the base-resend storm + token ceiling (M) · `conflict_analyst_agent.py`, `analyst_prompts.py`, `base_agent.py`, `cost_tracker.py`, `config.py`
(A) In `_chunked_analyze_file`, stop passing raw `base_content` into every chunk prompt — pass
`None` (the prompt degrades to "Not available" and still shows fork+upstream per chunk) or stage
base ONCE before fan-out. (B) Hoist worked examples + enriched context into the cacheable system
block. (C) Add `max_total_tokens` config checked in `_check_budget` so a `$0`-priced/proxy model
still trips a ceiling; emit the unpriced-model condition as a budget warning.
**Benefit:** ~6× input-token reduction on large C-class files (~370k → ~60k for one file);
removes the 16+ min wall-clock; restores a budget guardrail for unpriced models.
**Risk:** low — do NOT positionally zip a separately-split base (boundaries won't align). A
concurrency bump must land AFTER the base-drop.

### #9 — Elision/length-floor fixes (M) · `executor_agent.py`, `elision_detector.py`, `client.py`, `executor_prompts.py`, `prompt_builders.py`
(A) Pass `current_size=len(orig_current_content)` / `target_size=len(orig_target_content)` to
`parse_merge_result` on the single-shot path (matches `repair()`). (B) `looks_truncated`: add a
length-only branch — flag when `len(content) < floor` even if the tail ends healthy; guard with a
min absolute size. (C) `complete_meta`: when `stop_reason is None` AND
`tokens >= ~0.95*max_tokens`, normalize to `max_tokens` (defeats finish_reason-omitting
gateways — the deepseek-v4-pro case). (D) Re-route to chunked merge when staging would elide a
file the router deemed single-shot; add an anti-stub-body fidelity clause to the prompt.
**Benefit:** restores the only length-based code-loss defense on the single-shot path.
**Risk:** medium — more false-positive escalations for legitimately-shorter merges; behavior-change
tests/docs required. (D) is the most invasive — gate behind a validation.

### #10 — Chunked-merge structural alignment (L) · `chunk_processor.py`, `executor_agent.py`, `duplicate_symbol_check.py`, prompts
Content-anchored chunk pairing (drop the equal-count index-zip; use boundary-symbol overlap,
preserving the b-covering "never dropped" invariant); escalate on forced mid-body split; lexer-aware
`{}()[]` seam balance check post-`merge_chunks`; verbatim pass-through when the upstream chunk is
empty; JS/TS `function` redeclaration dedup; register + ground the chunk-merge prompt ("you are
merging ONE slice; do not reference a symbol not in these two sections").
**Risk:** medium-high — most invasive; symbol-overlap alignment must keep the b-covering invariant.

### #11 — Fork-preservation auditing (L) · `preservation_auditor.py`, `auto_merge.py`, `judge_agent.py`, `config.py`, `analyst_prompts.py`, `feature_preservation.py`
Audit `original_file_paths` (native-3way-drained files); add a line-level fork-survival check for
C-class (read-only — surface HIGH/route, never a reviewer state write); configurable `min_fork_lines`
(zeroed for security-sensitive); flag C-class `TAKE_TARGET` byte-equality-with-upstream as fork loss;
demote the analyst's imperative "Strongly prefer take_target" size-signal to neutral.
**Risk:** medium — line-level check must tolerate legitimate fork-line replacement (surface, don't
hard-veto). Largely subsumed by #4 but remains the safety net.

### #12 — Tighten advisory grounding (M) · `conflict_analysis.py`, `response_parser.py`, `diff_facts_grounding.py`, `hallucinated_symbol_guard.py`
Gate the analyst fabricated-symbol channel (`if grounding_warnings: ESCALATE_HUMAN`, scoped to the
fabricated subset if noisy); compare Judge evidence against the RAW on-disk merged content (not the
staged view) before downgrading; broaden diff-facts verb synonyms (advisory only); alias-aware symbol
guard.
**Risk:** low-medium — hard-escalate on any warning may over-escalate; scope to the fabricated subset.

---

## Estimated benefit (aggregate)

- **Correctness:** #1 + #2 + #4 + #3/#5 together close every confirmed path by which an
  uncompilable or feature-dropping merge currently reaches `COMPLETED`. The fail-OPEN terminal
  state becomes fail-SAFE — defects route to `AWAITING_HUMAN` rather than green completion.
- **Hallucination containment:** from ~one guarded path (chunked) to all paths (single-shot
  executor, chunked, native-3way via category exclusion, Judge deterministic veto), with the guard
  itself hardened against the chained-leaf shape and the substring bypass.
- **Truncation fail-open:** #3 converts the majority of currently-invisible truncated verdicts into
  escalations.
- **Cost/latency:** #8 is a ~6× reduction in conflict-analysis input tokens on large C-class files
  (~370k → ~60k for one file), plus a real token ceiling for unpriced/proxy models.
- **Residual risk:** added human-review volume from fail-safe escalations (acceptable — escalate,
  never corrupt), tunable via the thresholds in #9/#11.
