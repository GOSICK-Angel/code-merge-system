# Wave-5 Residual-Closure Plan — closing 06's carry-forward residuals

> **STATUS (2026-05-29): forward plan, not yet shipped.** This is the closure
> plan for the honest residuals that
> [`06-production-readiness-post-wave4.md`](06-production-readiness-post-wave4.md)
> carried forward. It continues the discipline of
> [`04-production-hardening-plan.md`](04-production-hardening-plan.md): each
> *closable* residual becomes a file-anchored initiative (W1–W5) ranked by
> leverage/risk, with insertion points, trade-offs, and a validation plan. The
> one *inherent* residual (semantic correctness rests on `build_check` + the
> Judge) is kept in "monitor & document, do not pretend to close."

Date: 2026-05-29. 06 re-ran 03's skeptical assessment after Wave 4 and ended with
five "honest residuals" plus one inherent limit. This document turns the five
closable ones into concrete work. Every claim below was **re-grounded against the
current `feat/web` working tree** by tracing the production call paths (not the
tests, and not the prose of the earlier docs — several of which have since gone
stale). Where the code contradicts an earlier doc, the correction is stated
explicitly in the next section.

## Principles (Wave 1–4, extended)

Wave 5 inherits "escalate, never corrupt" (Waves 1–3), "no silent skip" and "make
the safety net's dependencies visible" (Wave 4), and adds three:

1. **Distinguish the ambiguous.** A `None` that means *"git is broken"* must not
   read as *"the file is legitimately absent here."* Wave 4 deliberately under-
   alarmed because the two were conflated at the `git_tool` layer; Wave 5 splits
   them at the source so the alarm can be precise instead of either over- or
   under-firing.
2. **Measure the queue you create.** The system's safety comes from escalating;
   an operator running it unattended needs to *see the shape of that queue*
   (which change-categories auto-merge vs escalate) — locally, from state, with
   **no** new tracking or network.
3. **Prove it on real keys, not just mocks.** Tier-A (hermetic, mocked) proves
   the gates' *logic* on every PR; it does not prove the real `merge --ci`
   binary still escalates a real C-class conflict. Close that with a secret-gated
   nightly over the committed corpus.

No Wave-5 change auto-resolves more aggressively, and none deletes content.

---

## Current-state confirmations & corrections (grounded against `feat/web`)

The grounding surfaced material drift between the docs and the code. These
reprioritize the work and, in two cases, make it *cheaper* than 04 implied:

- **`client.py` already populates `stop_reason` on the non-structured path.**
  04 §correction claimed the client "discards `stop_reason`/`finish_reason`
  (`client.py:481-483/710/720`)." That is **stale.** Every provider's
  `complete_meta` now sets it: Anthropic `client.py:323-361` (`response.stop_reason`,
  `end_turn→stop` normalized), OpenAI chat `client.py:533-578`
  (`stop_reason = "max_tokens" if finish_reason=="length" else finish_reason`),
  OpenAI Responses `client.py:580-651` (`status=="incomplete"` → `"max_tokens"`).
  `LLMResponse` (`client.py:19-42`) carries `stop_reason`. **Consequence: W2 is a
  plumbing change (route 5 call sites through the already-existing
  `_call_llm_with_retry_meta`), not a client rewrite.** Caveat below.
- **Git *can* distinguish absent-at-ref from broken-ref** — exit code is 128 for
  both, but `GitCommandError.stderr` differs: absent → contains `does not exist
  in`, broken-ref → contains `invalid object name`. **Consequence: W1's
  `git_tool` split is feasible with a stderr predicate, not guesswork.**
- **The integration suite is live at 34 passed / 12 xfailed**, not the 35/12 that
  05 §P5 records. Minor count drift (one test moved); the 12 xfail node-ids and
  the `strict=False` reason at `tests/integration/conftest.py:32-42` are intact.
- **Tier-A is wired; the `schedule: cron` is already active.** `ci.yml:82-87`
  runs `pytest tests/integration/` (hermetic, mocked, key-free) in the `test`
  job, and `ci.yml:13-14` has an *active* `cron: "0 18 * * *"`. **But that cron
  today only drives `eval-tier1` (`ci.yml:106-139`), which runs the
  `fake_merge.sh` stub (`tests/eval/fixtures/fake_merge_bin/fake_merge.sh`), not
  the real binary.** There is **no `secrets.*` reference anywhere in `ci.yml`**
  (grep: zero hits). The real-key nightly (06 risk-matrix "no real-key nightly
  merge yet") is the genuine gap — W5 Part B.
- **No escalation-rate telemetry exists at all** — confirmed by grep (no
  `escalation_rate` / `by_category` / `outcome_matrix` symbol in `src/`). The CI
  summary `build_ci_summary` (`ci_reporter.py:8-79`) is a flat run-level tally
  with no category breakdown. But both axes already live on `state` at report
  time (W5 Part A) — this is a *pure join*, no new tracking.
- **No gate→language map exists** — `has_compile_gate` (`compile_gate.py:46-56`)
  is a pure boolean "is anything configured." W4 is contained entirely to
  `compile_gate.py`.

---

## Residual → initiative map

| 06 residual / source | Wave-5 initiative | Kind |
|---|---|---|
| #2 "P1 alarms only unambiguous skips — `None` conflates git-broken vs absent-at-ref" | **W1** finer gate-skip via a `git_tool` read-status split | code (tools/gating) |
| #3 "P2 closes truncation-breaks-JSON, not partial-but-valid JSON — `stop_reason` gate not done" | **W2** truncation `stop_reason` fail-closed on the 5 still-blind sites | code (parsers/agents) |
| #4 "12 integration tests `xfail` for routing drift" | **W3** restore the 12 + encode W1/W2/W4 as Tier-A assertions | tests |
| #5 "compile-gate predicate is conservative — any gate suppresses, even linter-only" | **W4** per-language compile-gate precision | code (tools) |
| 04 monitor "§3 add escalation-rate telemetry" + 06 risk-matrix "no real-key nightly" | **W5** local escalation telemetry + secret-gated Tier-B nightly | code (report) + CI |
| #1 "semantic correctness rests on `build_check` + the Judge" | **monitor & document** (inherent — not closable by a gate) | docs |

---

## Ranked initiatives

### W1 — Finer gate-skip: distinguish git-broken from absent-at-ref · closes 06 residual #2 · effort M · risk LOW

> **SHIPPED 2026-05-29.** `git_tool.GitReadStatus` enum + additive
> `get_file_hash_checked` / `get_worktree_blob_sha_checked` /
> `get_file_content_checked` (stderr predicate, existing readers byte-for-byte
> unchanged); `GIT_ERROR`-only alarm wired at the B-class drift sanity
> (`auto_merge.py`), executor fork-export (`executor_agent.py`), and read-only
> judge take-verification (`judge_agent.py` → `_gate_skips`). Tests:
> `tests/unit/test_w1_git_read_status.py` (real-git classification + per-consumer
> alarm/silence); existing `test_p1_gate_skip_alarm.py` / `test_b_class_sanity.py`
> / `test_agents_extended.py` test doubles migrated to the `_checked` contract.
> Full suite green (3164), `mypy --strict` + `ruff` clean.

**Problem (grounded).** Wave 4's P1 alarms only *unambiguous* skips because the
shared readers conflate two very different `None`s. `get_file_bytes`
(`git_tool.py:47-67`, behind `get_file_content:41-45`) and `get_file_hash`
(`git_tool.py:320-324`) both `except git.GitCommandError: return None` — and that
`None` means *either* "git is broken" *or* "this path is legitimately absent at
this ref." `get_worktree_blob_sha` (`git_tool.py:329-336`) already separates the
on-disk-absent case (`if not abs_path.exists(): return None`, `:331-332`) from the
git-error case (`:335-336`), so only the two **ref-based** readers conflate.

Two consequences fall out of this conflation:

1. **A real, currently-silent gap at the executor.** `_single_shot_fidelity_issue`
   alarms via `gate_skip_entry` only inside its `except Exception`
   (`executor_agent.py:660-674`). But `get_file_content` **swallows
   `GitCommandError` internally** (`git_tool.py:61-62`), so a genuinely broken-ref
   read never raises — it returns `None`, falls into the silent `base_content=None`
   branch, and the `except` never fires. A systemically broken `git_tool` at this
   site is invisible *today*, exactly the failure class P1 set out to close.
2. **The B-class drift sanity is deliberately un-alarmed** (`auto_merge.py:1631-1640`,
   the documented headline carry-forward) precisely because its `get_file_hash →
   None` could be a legitimately-absent file (the D-missing/D-extra case). It must
   stay quiet on absent, but a *git error* there is a true blind gate.

Git itself can tell these apart (exit 128 for both; the signal is in
`GitCommandError.stderr` — `does not exist in` ⇒ absent, `invalid object name` ⇒
broken). The codebase uses the `<ref>:<path>` colon form exclusively, so these two
strings are the only variants.

**Design — additive `_checked` readers returning `(value, status)`. Do NOT change
the existing readers' return types.** The decisive constraint: `get_file_hash` /
`get_file_content`'s `None` is **load-bearing as a semantic value** at ≥6 sites —
`file_classifier.py:458-474` (derives `D_MISSING`/`D_EXTRA`/`A`),
`pollution_auditor.py:141-160`, `conflict_analysis.py:188-197` (`None`→exists-bool),
`git_tool.py:586-587` (`file_exists_at_ref`), and the `or ""` graceful-degrade
sites `three_way_diff.py:178-179` / `judge_agent.py:1324-1325`. Returning a bare
**sentinel object** from the existing methods would corrupt classification
(`head_hash == up_hash` with a sentinel mis-derives the category at
`file_classifier.py:468`), force a narrow-before-`==` edit at ~50 call sites, and
keep three reader `Protocol` copies in lockstep. A **typed exception** would break
the best-effort-degrade contract every caller relies on. The additive option
breaks **zero** existing signatures and keeps mypy `--strict` clean.

- **Add** to `git_tool.py`: an `enum GitReadStatus { OK, ABSENT, GIT_ERROR }` and
  sibling methods `get_file_hash_checked(ref, path) -> tuple[str | None,
  GitReadStatus]`, `get_worktree_blob_sha_checked(path) -> tuple[...]`, and
  `get_file_content_checked(ref, path) -> tuple[...]`. Each classifies via
  `"does not exist in" in (e.stderr or "")` ⇒ `ABSENT`, else `GIT_ERROR`. The
  existing methods stay byte-for-byte identical (optionally reimplemented to call
  `_checked` and drop the status — a 1-line refactor).
- **Consume the status → alarm only `GIT_ERROR`** at the sites where `None` is a
  *skip*, never where it is a *value*:
  | Site | Change | Newly alarmable? |
  |---|---|---|
  | `auto_merge.py:1631-1639` (B-class sanity) | `_checked`; `state.errors.append(gate_skip_entry(...))` on `GIT_ERROR`, keep silent `continue` on `ABSENT` | **YES — the headline target** |
  | `executor_agent.py:657-674` (fork-export) | `get_file_content_checked`; `GIT_ERROR` now surfaces the in-method `GitCommandError` the current `except` misses | **YES — closes the real gap** |
  | `judge_agent.py:499-502` (interface-drift veto, read-only) | `_checked`; on `GIT_ERROR` push `gate_skip_entry` onto `self._gate_skips` (shipped via `PHASE_COMPLETED`, persisted by `judge_review._persist_gate_skips`), keep `continue` on `ABSENT` | **YES** |
  | `auto_merge.py:1682-1684` (repair confirm) | optional — alarm `GIT_ERROR` (a post-repair read failure is anomalous) | YES (optional) |
- **Narrow an existing over-alarm.** `preservation_auditor.py:191-205` currently
  alarms on **any** `None`; with `_checked` it can alarm only `GIT_ERROR` and stay
  quiet on `ABSENT`, reducing false partial-failures on legitimately-absent C-class
  blobs. Net: W1 makes the alarm *more precise in both directions*.
- **Do NOT migrate** the 6 semantic-`None` sites listed above — they legitimately
  treat broken and absent identically; the additive design leaves them untouched
  (the `enum` never reaches a `==` on hashes, a blob map, or a checkpoint).

**Trade-off / risk.** Additive, zero signature churn (LOW). The one hazard —
a `GitReadStatus` leaking into a hash comparison or `state.errors` (which is
`list[dict[str, Any]]`, `state.py:268`) — is structurally avoided: only the
existing `gate_skip_entry` *string dict* is ever appended; the enum stays inside
the `if status == GIT_ERROR` branch and is never persisted. A flaky single-file
git read now produces one `partial_failure` exit even on an otherwise-fine merge —
*intended* (the operator wants to know a gate was blind). Skip in `state.dry_run`
to match the sibling helpers.

**Validation.** Unit test: patch `get_file_hash_checked` to return `(None,
GIT_ERROR)` on a specific path during the B-class sanity / executor fork-export /
judge interface-drift gates; assert a `gate_skip` entry lands in `state.errors`
(or `self._gate_skips` for the read-only Judge) and the run exits 30. Negative
test: `(None, ABSENT)` ⇒ **no** alarm (the D-missing case). Mypy `--strict` must
stay clean (the enum is the only new type).

---

### W2 — Truncation `stop_reason` fail-closed on the 5 still-blind sites · closes 06 residual #3 (P2 Change B) · effort M · risk LOW–MEDIUM

> **SHIPPED 2026-05-29.** New `response_parser._stop_reason_gate(raw, strict_json)`
> (duck-typed LLMResponse → raise `ParseError` on `stop_reason in {max_tokens,
> length}`, mirroring `parse_merge_result` gate #1) threaded into all four
> parsers (`parse_file_review_issues`, `parse_batch_file_review_issues`,
> `parse_commit_round_analyses`, and `parse_conflict_analysis` — the last gains a
> `strict_json` param). The 5 call sites (judge `review_file` / `_review_files_batch_llm`,
> analyst `analyze_file` / `_analyze_chunk` / `analyze_commit_round`) now call
> `_call_llm_with_retry(_return_meta=True, **_structured_kwargs(...))` and pass the
> `LLMResponse` (not `str(raw)`) to the parser. **Structured-output opt-in
> preserved**: `json_schema` (when `use_structured_outputs=True`) wins the branch
> precedence in `_call_llm_with_retry`, returning a `str` (stop_reason absent →
> gate inert, best-effort, documented); the default path returns `complete_meta`'s
> `LLMResponse`. Veto/escalation routing unchanged — the gate's `ParseError`
> reaches the existing `review_unavailable` veto (judge) / `0.3` escalation
> (analyst) / `ParallelFileRunner` chunk-fail escalation. Tests:
> `tests/unit/test_w2_truncation_stop_reason.py` (parser gate matrix + agent
> wiring). Full suite green (3179), `mypy --strict` + `ruff` clean.

**Problem (grounded).** P2 (Wave 4) re-raises `ParseError` under `strict_json` when
the response is *unparseable* — but `_extract_json` (`response_parser.py:19-42`)
salvages JSON via `find("{")`/`rfind("}")` (`:35-39`), so a truncation that lands
**after an earlier balanced object** yields a *partial-but-valid* dict that
`strict_json` never catches. The only signal that the text was cut is
`LLMResponse.stop_reason == "max_tokens"`, which today is read **only** by the
executor's `parse_merge_result` Gate-1 (`response_parser.py:364-384`) — never by
the analyst or the per-file Judge. Five production consumers parse on the
`str`-returning retry and so cannot see it:

| Consumer | Call site (str-retry) | Parser |
|---|---|---|
| Judge per-file review | `judge_agent.py:361-372` | `parse_file_review_issues` (`:530-580`) |
| Judge batch review | `judge_agent.py:1827-1839` | `parse_batch_file_review_issues` (`:672-742`) |
| Analyst commit-round | `conflict_analyst_agent.py:603-625` | `parse_commit_round_analyses` (`:583-669`) |
| Analyst single-file | `conflict_analyst_agent.py:348-355` | `parse_conflict_analysis` (`:200-266`, **no `strict_json` param**) |
| Analyst chunk | `conflict_analyst_agent.py:447-452` | `parse_conflict_analysis` |

**The retry layer does not absorb this.** `_call_llm_with_retry`'s loop
(`base_agent.py:698-960`) only re-enters on a raised `except Exception`; a
truncated-but-nonempty completion **returns successfully** — `stop_reason ==
"max_tokens"` is never raised, never retried. `error_classifier.py` has no
truncation category (`PROVIDER_EMPTY` fires only on *empty* content). 05's "the
retry layer absorbs `finish_reason='length'`" describes retries on *raised*
errors, not on partial-but-valid truncation. So this is a genuine, retry-immune
residual.

**Design — route the 5 sites through the existing `_call_llm_with_retry_meta`,
duck-type `stop_reason` in the 4 parsers (the `parse_merge_result` template).**
The plumbing already exists (`_call_llm_with_retry_meta`, `base_agent.py:540-570`,
returns `LLMResponse`); the executor proves the pattern
(`executor_agent.py:509-522` passes the whole `LLMResponse` into a parser whose
Gate-1 raises on `stop_reason in {"max_tokens","length"}`).

- **Switch 5 call sites** from `_call_llm_with_retry(...)` (then `str(raw)`) to
  `_call_llm_with_retry_meta(...)` and pass the `LLMResponse` (not `str(raw)`) to
  the parser: `judge_agent.py:361`, `:1827`; `conflict_analyst_agent.py:603`,
  `:348`, `:447`.
- **Parsers** gain a duck-typed Gate-1, copied from `parse_merge_result:375-384`:
  before `_extract_json`, if `strict_json and getattr(raw, "stop_reason", None) in
  {"max_tokens", "length"}` → `raise ParseError(...)`. Apply to
  `parse_file_review_issues`, `parse_batch_file_review_issues`,
  `parse_commit_round_analyses` (all three already take `strict_json`), and **add
  `strict_json: bool = False` to `parse_conflict_analysis`** (`:200`) which has
  none today. Reuse the `LLMResponseLike = Any` alias (`response_parser.py:454-456`)
  to dodge the import cycle. A legacy `str`-only caller is unaffected:
  `getattr(str, "stop_reason", None)` is `None`.
- **The veto/escalation paths already exist** — no new routing. Judge per-file and
  batch already convert a `ParseError` under `strict_json` into a synthesized
  CRITICAL `review_unavailable` veto (`judge_agent.py:374-401`, `:1842-1869`). The
  analyst's broad `except` already routes a `ParseError` to `ESCALATE_HUMAN`
  (`:363`, `:447`) / `_consecutive_failures` bookkeeping (`:626-637`). W2 only
  makes the *truncation* case reach those existing paths instead of being salvaged
  into a falsely-complete result.

**Known caveat to state honestly (the one residual W2 itself leaves).** When
`use_structured_outputs=True`, the `structured_json` path returns a bare `str`
with no `stop_reason` (`client.py:400-407/690/156-163`), so the gate is inert
there. But `use_structured_outputs` defaults **False** (`config.py:67-75`) and is
never flipped on in production, so the **default path is fully covered.** Document
the gate as best-effort-on-the-default-path; extending `structured_json` to return
an `LLMResponse`-like is a larger 3-provider change, deliberately out of scope
(noted as a sub-carry-forward, mirroring how 05 scoped P2's salvage gap).

**Trade-off / risk.** Undersized `max_tokens` now escalates *more* — paired with
the **already-shipped P4 preflight** (`preflight.py`), which warns when
`chunk_size_chars >= max_tokens*1.4` before it bites. LOW–MEDIUM. Touches exactly
5 LLM call sites; leave the executor (already meta), the rationale-only str sites
(`executor_agent.py:1164/1302`), and the verdict aggregators
(`parse_plan_judge_verdict`/`parse_judge_verdict`) alone.

**Validation.** Per-consumer unit test feeding a synthetic `LLMResponse(text=<an
earlier-balanced-then-truncated blob>, stop_reason="max_tokens")`: assert a veto
(Judge) / forced `ESCALATE_HUMAN` (analyst), **not** a clean partial parse. A
companion test with `stop_reason="stop"` on the same salvageable blob must still
pass cleanly (no over-fire).

---

### W3 — Restore the 12 xfailed integration tests + encode W1/W2/W4 as Tier-A assertions · closes 06 residual #4 · effort S–M · risk LOW

> **SHIPPED 2026-05-29.** All 12 restored; the `xfail` mechanism
> (`_DRIFTED_NODEIDS` + `pytest_collection_modifyitems`) is removed —
> `pytest tests/integration/` is now **49 passed, 0 xfailed**. Fixes: (1)
> `FakeGitTool._blob` "C" branch makes fork/upstream *conflict on the same line*
> so the rule resolver declines and the scripted analyst runs (un-xfails the 7
> escalation/semantic tests); (2) `FakeGitTool` hash readers return **real git
> blob shas** (`_git_blob_sha`) so the patch applier's post-write self-check
> passes once the semantic-merge write path is actually reached; (3) the revision
> issue uses a non-`SHORTCIRCUIT_SAFE_ISSUE_TYPES` type so the R1 plan review runs
> each round; (4) call-count assertions aligned to current routing (planner built
> deterministically for all-auto-safe = 0 LLM calls; planner-judge reviews twice
> then hits the round limit). Plus a new `test_wave5_gates.py` Tier-A net: W1
> git-error→`gate_skip`, W4 `.go`+ruff-gate→`no_compile_gate` advisory (both full
> orchestrator runs), and W2 truncated review→`review_unavailable` veto (against
> the orchestrator's real Judge — the synthetic scenarios never drive per-file
> LLM review, documented inline). Also required adding the W1 `_checked` twins to
> the integration `FakeGitTool`. `ruff` clean.

**Problem (grounded).** Live today: **34 passed, 12 xfailed**
(`tests/integration/conftest.py:32-42`, `strict=False`). The 12 fail for genuine
pipeline evolution, not a Wave-4 regression, and trace to exactly three causes:

- **Rule-based resolver short-circuit (7 tests).** `conflict_analysis.py:485-520`
  runs `RuleBasedResolver.try_resolve` before the scripted analyst; the C-class
  fixture blobs (`conftest.py:236-240`: base, base+`"# fork change"`,
  base+`"# upstream change"`) are a pure **line-addition union**, which
  `rule_resolver.py:254-310` resolves to `TAKE_TARGET @ confidence 0.88`. So
  `llm_files` excludes it (`:604-610`), the scripted analyst is never called, and
  the run proceeds to COMPLETED instead of escalating. This is the root of
  `test_escalation.py:45/69/120/146` and `test_semantic_merge.py:84/116/211`
  (the asserted `0.3`/`0.92` confidences and `semantic_merge` decision all lose to
  the rule's `0.88`/`TAKE_TARGET`).
- **Plan-review R1 short-circuit (4 tests).** `plan_review.py:275-300` (log at
  `:291`) auto-accepts a single `risk_underestimated` revision issue when it is in
  `SHORTCIRCUIT_SAFE_ISSUE_TYPES` and the precheck is clean, so the 2nd/3rd
  planner-judge LLM call never happens — breaking the call-count asserts in
  `test_plan_revision.py:93/115/158` and the terminal-status asserts at `:45/:115`.
  (`test_plan_revision.py:45` additionally hits the judge-stall fail-closed:
  `coordinator.py:34-45` + `config.py:1038-1042`, threshold 2, → AWAITING_HUMAN.)
- **Call-count drift (1 test).** `test_happy_path.py:65` asserts the plan-judge LLM
  was called once; an all-`auto_safe` round-0 plan is now approved without that
  call (`plan_review.py` auto-proceeds), so the mock is called 0 times.

**Design — re-script against current routing (NOT git_bootstrap).** The grounding
is unambiguous: none of the 12 fail because `FakeGitTool` mis-models git; they fail
because the *scripted inputs* are now auto-resolved or auto-approved. The cheapest
levers:

- **One fixture edit un-xfails all 7 resolver-short-circuit tests.** Change the
  `"C"` branch of `FakeGitTool._blob` (`conftest.py:236-240`) from each side
  *appending a distinct line* to each side *modifying the same base line* (an
  overlapping edit, e.g. base `def run(self): return 0`, fork `return 1`, upstream
  `return 2`). Then `_try_line_addition_union` / `_try_adjacent_edit` /
  `_try_whitespace_only` / `_try_import_union` all decline, `try_resolve` returns
  unresolved, and the scripted analyst runs — `CONFLICT_LOW/HIGH_CONFIDENCE` is
  consumed again and the 7 assertions hold.
- **The 4 plan-revision tests:** make the revision `issue_type` one **not** in
  `SHORTCIRCUIT_SAFE_ISSUE_TYPES` (or supply a non-pure-ACCEPT 2nd planner
  response) so the R1 LLM review fires every round and consumes the scripted
  planner-judge verdicts; for `test_plan_revision.py:45` additionally script a 2nd
  `JUDGE_VERDICT_PASS` so the judge converges to COMPLETED.
- **`test_happy_path.py:65`:** relax `judge_mock.assert_called_once()` →
  `call_count in (0, 1)` (keep the planner assert); the sibling
  `test_all_auto_safe_reaches_completed` already proves the flow is correct.
- **Keep `xfail(strict=False)` discipline:** as each is restored it flips to
  **xpass** visibly; remove its node-id from `_DRIFTED_NODEIDS` only once green.

**Then make W3 the regression net for this wave** (mirroring how 04 made P5 encode
P1–P3's behaviors): add Tier-A assertions driving `Orchestrator(config).run(state)`
over `FakeGitTool` for the new Wave-5 behaviors —
1. broken-`git_tool` (patch `get_file_hash_checked → (None, GIT_ERROR)`) ⇒
   `gate_skip` in `state.errors`, status ≠ COMPLETED (**W1**);
2. a `stop_reason="max_tokens"` canned per-file Judge response ⇒ `review_unavailable`
   veto, ≠ COMPLETED (**W2**);
3. a `.go` auto-merge with a ruff-only gate ⇒ `no_compile_gate` advisory present
   (**W4**).

**Trade-off / risk.** Tests only (LOW). The fixture edit is shared by 7 tests, so
verify it does not perturb the other passing C-class tests (the grounding confirms
the resolver-decline is the *intended* path the analyst-scripted tests assume).

**Validation.** `pytest tests/integration/` returns **0 xfail** (all 12 xpass and
are de-listed); the 3 new Wave-5 Tier-A assertions are green. (The separate
*real-merge-vs-golden* E2E that `git_bootstrap` would enable is **not** part of
W3 — it is folded into W5 Part B as a new layer, not a restoration of these
unit-style orchestrator assertions.)

---

### W4 — Per-language compile-gate precision · closes 06 residual #5 · effort S · risk LOW

> **SHIPPED 2026-05-29.** New `compile_gate.gate_covered_suffixes(config)`
> (parser-id map + command-token map + recognised-lint set + conservative
> unknown→cover-all) threaded into `auto_merged_compiled_paths_without_gate`;
> both behavioral consumers (`report_generation._check_compile_gate_advisory`,
> `judge_review` opt-in soft gate) become per-language-precise with zero
> call-site changes; `has_compile_gate` kept coarse for the preflight nag. Tests:
> `tests/unit/test_p3_compile_gate.py` (+11 cases). `mypy --strict` + `ruff`
> clean. *Refinement vs first draft: ESLint is lint-only — see the parser-id
> note below.*

**Problem (grounded).** `has_compile_gate(config)` (`compile_gate.py:46-56`) is a
pure boolean: `build_check.enabled` with a non-blank command, **or** `gate.enabled`
with **any** non-blank `gate.commands[i].command`. It never inspects *what* the
command compiles. So a Python-only `ruff` / `pytest` / `mypy` gate suppresses the
`no_compile_gate` advisory even when the merge auto-merged only `.ts` / `.go`
files — the exact false-suppression 06 names. The compiled-language set is the
single-source-of-truth `_BALANCE_SPECS` (`syntax_checker.py:160-171` →
`balance_only_language_suffixes()`, `:449-456`): `.ts/.tsx/.js/.jsx/.mjs/.cjs`
(TS/JS), `.go`, `.rs`, `.java/.kt`.

**Design — a `gate_covered_suffixes(config)` helper, contained entirely to
`compile_gate.py`.** Build the covered-suffix union from two structured sources,
then subtract it from `compiled_language_paths(merged)`:

- **Parser-id → suffixes** (high confidence; the registered ids at
  `baseline_parsers/__init__.py:99-108`): `tsc_errors` ⇒ TS/JS set;
  `go_test_json` ⇒ `{.go}`; `cargo_test_json` ⇒ `{.rs}`; `junit_xml` ⇒
  `{.java,.kt}`; `eslint_json`/`mypy_json`/`basedpyright_json`/`ruff_json`/
  `pytest_summary` ⇒ `{}` (lint/format/test or Python — not a *typecheck* of the
  balance-only set, so a lint-only or Python-only gate correctly contributes
  nothing — this is the headline fix). **Implementation refinement vs the first
  draft:** `eslint`/`eslint_json` is classed lint-only (covers nothing), not
  TS/JS — ESLint lints, it does not typecheck, so an eslint-only gate must keep
  flagging the TS files it cannot compile-check.
- **Command-token → suffixes** (lower confidence; substring-match the lowercased
  `build_check.command` and any parser-less `gate.commands[i].command`):
  *compile-capable* — `tsc` / `vue-tsc` ⇒ TS/JS; `go build` / `go vet` / `go test`
  ⇒ `{.go}`; `cargo` / `rustc` ⇒ `{.rs}`; `javac` / `gradle` / `mvn` ⇒
  `{.java,.kt}`. *Recognised lint/format/test* — `eslint` / `prettier` / `biome` /
  `ruff` / `mypy` / `pytest` … ⇒ `{}` (covers nothing, but RECOGNISED so it does
  not fall through to the conservative unknown rule). (Vocabulary drawn from what
  setup actually emits — `_detect_build_check_command`, `setup.py:287-319`.)
- **Conservative-on-unknown (preserves today's behavior, avoids nag fatigue).** If
  a configured command is non-empty but matches **no** token and has **no**
  recognized parser id — an opaque bundler/aggregate like `pnpm run build`,
  `npm run build`, `make`, `bazel build`, `./scripts/ci.sh` — treat it as
  **covering all compiled suffixes** (suppress everything). Such commands very
  often *do* typecheck the whole tree; flagging them would regress the "operator
  who configured a gate is gate-aware" stance. **W4 therefore only ever *narrows*
  suppression for gates positively attributable to a strict language subset** (the
  ruff-only case); it is a strict no-op for every opaque command and for the
  no-gate-at-all hole (which still flags everything).

Thread `gate_covered_suffixes` **inside `auto_merged_compiled_paths_without_gate`**
(`compile_gate.py:68-82`) so both behavioral consumers —
`report_generation._check_compile_gate_advisory` (`:75-112`) and the opt-in
`judge_review` soft gate (`:281-301`) — become per-language-precise with **zero
call-site signature changes.** (Preflight `_compile_gate_warnings`,
`preflight.py:98-108`, has no merged file-set and stays config-only / whole-gate.)

**Trade-off / risk.** LOW. Contained to one module; the existing
`test_suppressed_when_gate_configured` (`tsc` gate vs `.ts`) stays green because
`tsc` covers `.ts` — a good regression anchor.

**Validation.** Extend `tests/unit/test_p3_compile_gate.py`: ruff-only gate +
merged `.ts` ⇒ still at-risk (the fix); `tsc` gate + co-merged `.go` ⇒ at-risk
returns only `.go`; `go build` ⇒ `.go` suppressed; opaque `pnpm run build` +
`.ts`+`.go` ⇒ empty (conservative-on-unknown, no regression); both the parser-id
and command-token paths exercised.

---

### W5 — Local escalation telemetry + secret-gated Tier-B nightly · closes 04 monitor item + 06 risk-matrix gap · effort M · risk LOW

> **SHIPPED 2026-05-29.** **Part A (telemetry):** `ci_reporter._escalation_by_category(state)`
> joins `state.file_categories` × `state.file_decision_records` into a
> `{category: {auto, escalated, human, other}}` matrix, added as the `by_category`
> key of `build_ci_summary`. Pure local computation, zero new tracking, **no
> network** (asserted by a test that greps the module for net imports). Tests:
> `tests/unit/test_w5_escalation_telemetry.py` (4). **Part B (Tier-B nightly):**
> new `eval-tier-b-realkeys` job in `ci.yml` — `if: schedule || workflow_dispatch`,
> `continue-on-error`, secrets via `env:` + a guard step that self-skips when
> absent (zero spend for forks without keys), running real `merge --ci` over the
> Tier-1 dataset via the tested `scripts.eval.run --use-real-keys` path and
> scoring escalation with `diff_against_golden`. **Wired but NOT exercised by PR
> CI (key-free) — it needs a one-time manual `workflow_dispatch` validation with
> secrets present before it is trusted** (the one Wave-5 item not locally
> verifiable). `mypy --strict` + `ruff` clean; YAML validated.

Two independent parts; ship either alone.

#### Part A — escalation-by-category telemetry (local, no network)

**Problem (grounded).** `build_ci_summary` (`ci_reporter.py:8-79`) emits a flat
run-level tally — `status / total_files / auto_merged / human_required /
human_decided / failed_count / judge_verdict / errors` (`:69-79`) — with **no**
per-change-category breakdown. An operator cannot see whether the escalations
cluster in C-class (expected) or are leaking from B-class (a red flag). 04 named
this ("add escalation-rate telemetry … per change category"); nothing shipped.

**Design — a pure join over existing state at report time. Zero new tracking.**
Both axes already live on `state`:
- **Category:** `state.file_categories` (`state.py:98`), populated unconditionally
  in initialize (`initialize.py:412`) — `A/B/C/D_MISSING/D_EXTRA/E`
  (`diff.py:5-11`).
- **Outcome:** `state.file_decision_records` (`state.py:147`) — each
  `FileDecisionRecord` (`decision.py:24-44`) carries `.decision`
  (`MergeDecision`, `decision.py:8-14`) and `.decision_source` (`DecisionSource`,
  `:17-21`). Reuse the existing predicates verbatim: auto =
  `decision_source.value in ("auto_planner","auto_executor")` (`ci_reporter.py:22-25`);
  escalated = `decision == ESCALATE_HUMAN and decision_source != HUMAN`
  (`report_generation.py:133-138`).

Compute a `{category: {auto, escalate, human_decided}}` matrix by joining the two
maps on file-path (tolerating keys present in one map only):
- **CI summary:** add a `"by_category"` key to the dict at `ci_reporter.py:69-79`
  (a counter loop beside the existing `auto_merged` comprehension).
- **Markdown report:** add an `## Escalation by Category` table immediately after
  the File Decision Records table (`report_writer.py:467-484`), reusing the `t()`
  i18n helper and the markdown-table idiom already there; call it from the same
  place the other report helpers run (`report_generation.py:229-241`).

**No-network confirmation (rule-compliant).** Grep confirms **zero** network
egress in `ci_reporter.py` / `report_generation.py` / `report_writer.py` (stdlib
`json`/`datetime` + internal models only). The matrix is a purely local,
in-report/in-summary computation over `state` — it adds no analytics, no tracking,
no network call, satisfying the global rule "Do NOT add analytics, telemetry,
tracking, or new network calls unless explicitly instructed." It is the *only*
acceptable form, and strictly additive. **Never relax a gate to lower a number it
reports** (04's standing rule).

#### Part B — real-key Tier-B nightly (the one CI gap)

**Problem (grounded).** Tier-A (`pytest tests/integration/`, `ci.yml:82-87`) proves
gate *logic* on mocks every PR. The active nightly cron (`ci.yml:13-14`) drives
only `eval-tier1` (`:106-139`), which runs the `fake_merge.sh` stub — **no CI job
runs the real `merge --ci` binary against a real conflict.** There is no
`secrets.*` in `ci.yml`. 06's risk-matrix "no real-key nightly merge yet" is this.

**Design — assembly, not new code; every primitive already exists.**
- New job `eval-tier-b`, `if: github.event_name == 'schedule'`,
  `continue-on-error: true` (extends the existing `eval-tier1` precedent at
  `ci.yml:113-114`). Map secrets to `env:` (`ANTHROPIC_API_KEY`/`OPENAI_API_KEY`
  from `secrets.*`) and a first "guard" step that `exit 0`s if either is empty
  (`if:` cannot read `secrets.*` directly — gate on `event_name`, then check via
  `env`).
- For each of `t1-0031/0032/0033`
  (`tests/eval/datasets/tier1/samples/`, all `category: C`, **`expected_human:
  true`**): `python -m scripts.eval.git_bootstrap <sample> <tmp_repo>` —
  `bootstrap_synthetic_repo` (`git_bootstrap.py:121-188`) materializes `base.tar`
  ⊕ `fork.patch` (→ `main`) and ⊕ `upstream.patch` (→ `upstream`), returning a
  `RefBundle`.
- Run **real** `merge --ci` via the runner, which already supports it:
  `scripts/eval/run.py --merge-bin merge --use-real-keys --merge-args "--ci"`
  (subprocess at `run.py:191-199`; `_common.eval_subprocess_env(use_real_keys=True)`
  forwards real keys, `_common.py:40-67`). This swaps `fake_merge.sh` for the real
  binary — the **only** delta vs the current `eval-tier1` invocation.
- **Assert escalation** (all three `expected_human: true`): exit code
  `EXIT_NEEDS_HUMAN = 10` (`exit_codes.py:2`), **not** 0 — beware exit 30
  (`partial_failure`) which means a `state.errors` entry leaked. Equivalently, the
  existing `diff_against_golden._is_system_escalated` (`:319-343`,
  `ESCALATED_STATUSES` `:310-317`) already scores `system_escalated=True` from
  `checkpoint.json`, so the existing `diff → gate` chain (`scripts/eval/gate.py`)
  scores "expected_human met" for free — just point it at
  `tests/eval/datasets/tier1` instead of `reference_samples`.

**Cost / runtime / model.** All three are single-file C-class conflicts that halt
at the conflict/human-review stage (like the zod E2E, which "did not reach
`build_check`"), so per-sample cost is well under the zod run's ~19 min / ~181K
tokens; the dominant variable is proxy latency. The `#8C` 8M-token ceiling +
`RunBudgetExceeded` (`state.py:38-52`) cap runaway cost. Pin the **cheapest viable
per-agent models** in the nightly config (Haiku for the `ANTHROPIC_API_KEY` agents,
a low-cost OpenAI tier for `planner_judge`/`executor`); the executor does minimal
work since the samples escalate rather than auto-merge. Keep `max_tokens ≥ 32768`
for any reasoning model (the `_OPENAI_REASONING_MIN_MAX_TOKENS` floor / P4
preflight) to avoid self-truncation noise.

**Trade-off / risk.** LOW (CI + report only). `continue-on-error: true` means a
missing-secret or flaky-proxy nightly never blocks; Part A is purely additive
output.

**Validation.** Part A: unit test asserting `by_category` matrix and the report
table on a synthetic state with mixed B/C decisions; confirm no network import.
Part B: the job is the validation — a green nightly with all three asserting
`system_escalated=True` / exit 10; a forced missing-secret run exits 0 (skipped),
not red.

---

## Inherent limitation — monitor & document, do NOT pretend to close

**06 residual #1 — semantic correctness rests on `build_check` + the (fallible)
Judge.** This is architectural, not a bug. The always-on syntax gate is
balance-only for compiled languages by design (a fragile pseudo-semantic guard
would *mask* real defects, 06 §2). W4 **sharpens the visibility** of this
dependency — an operator can now see exactly which compiled files were auto-merged
without a *language-matched* gate — and W5 Part A shows the escalation shape, but
neither *removes* the dependency. **Operating rule stands: never run a production
compiled-language merge without a real `build_check` for the merged languages.**
No Wave-5 change adds a pseudo-semantic check.

---

## Sequencing (Wave 5)

1. **W4 + W1 first** — precision/visibility, lowest risk, additive. W4 is one
   module; W1 is additive readers. Neither changes routing.
2. **W2** — truncation fail-closed on the 5 sites; independent, pairs with the
   already-shipped P4 preflight (which warns before `max_tokens` bites).
3. **W3** — restore the 12 xfails **and** encode W1/W2/W4's new behaviors as the
   Tier-A regression net (do this *after* W1/W2/W4 so the assertions exist to
   encode), mirroring 04's "P5 last" discipline.
4. **W5** — Part A (telemetry) is trivial and can land any time; Part B (Tier-B
   nightly) last, as the highest-fidelity measurement layer over the corpus.

---

## Updated target risk matrix (post-Wave-5)

| Failure mode | 06 (post-W4) | Wave-5 target | Residual after Wave 5 |
|---|---|---|---|
| Silently-skipped gate, git-broken vs absent conflated | partial_failure only on *unambiguous* skips | **git-broken alarms precisely** (W1) at B-class sanity / executor fork-export / judge interface-drift; absent stays quiet | a third stderr variant or a non-colon ref form would need an extra predicate (narrow) |
| Truncated *partial-but-valid* Judge/analyst JSON → silent pass | fail-closed only when truncation *breaks* JSON (P2) | **fail-closed on `stop_reason`** at all 5 sites (W2) | inert when `use_structured_outputs=True` (default off; documented) |
| Integration corpus / drifted tests | 12 xfailed, no Wave-4 assertions in Tier-A | **0 xfail + W1/W2/W4 encoded as Tier-A assertions** (W3) | corpus still small; widened by W5-B |
| Compiled auto-merge under a language-mismatched gate | advisory suppressed by *any* gate (over-broad) | **per-language-precise advisory** (W4); opaque bundler still suppresses (conservative) | bundler command can't be attributed (intended; avoids nag) |
| Escalation queue shape invisible | flat run-level tally only | **category×outcome matrix** in CI summary + report (W5-A) | local only (by design — no telemetry/network) |
| Real-binary behavior on a real conflict | mocked Tier-A only | **secret-gated real-key nightly** over t1-0031..33 (W5-B) | 3-sample corpus; non-blocking |
| Type-error merge → COMPLETED | visible (P3a) / gated (P3b) | unchanged — **inherent**; W4 sharpens the dependency's visibility | correctness still needs a language-matched compile gate (inherent §1/§2) |

---

## Acceptance criteria

- A run with a **broken** `git_tool` at the B-class sanity / executor fork-export /
  judge interface-drift gate ends `partial_failure` (exit 30) with a `gate_skip`
  entry; a run where the same file is **legitimately absent** does **not** alarm.
  **[W1]**
- A truncated-but-salvageable (`stop_reason="max_tokens"`) Judge per-file /
  analyst response escalates (veto / `ESCALATE_HUMAN`), never silently passes;
  a `stop_reason="stop"` response on the same blob parses cleanly. **[W2]**
- `pytest tests/integration/` returns **0 xfail**, and Tier-A asserts the W1/W2/W4
  behaviors. **[W3]**
- A compiled-language auto-merge under a **language-mismatched** gate (ruff-only +
  merged `.ts`) records `no_compile_gate`; an **opaque bundler** gate
  (`pnpm run build`) does not. **[W4]**
- The CI summary and merge report carry a `by_category` escalation matrix, computed
  locally with **no** network. **[W5-A]**
- A secret-gated nightly runs real `merge --ci` over t1-0031..33 and asserts
  `system_escalated=True` / exit 10; a missing-secret run skips (exit 0), never
  blocks. **[W5-B]**

## Bottom line

Wave 4 made the fail-safe net *honest*; Wave 5 makes it **precise and measured**.
W1 stops the gate-skip alarm from conflating "git broke" with "file absent" — so a
broken `git_tool` alarms exactly where it should and stays quiet where a `None` is
legitimate. W2 closes the last truncation hole (partial-but-valid JSON) at the
five sites the executor's pattern already proved. W3 re-greens the integration
corpus and turns it into the wave's own regression net. W4 makes the compile-gate
advisory language-aware so a Python linter no longer silences a TypeScript merge.
W5 lets the operator *see* the escalation queue's shape and *proves* the real
binary still escalates a real conflict on real keys. None of this makes the system
an autonomous complex-merge bot — 03/06's verdict stands, and the inherent
`build_check`+Judge dependence is sharpened, not removed. What changes is that
every residual 06 could honestly close is closed, and the one it cannot is made
louder still.
