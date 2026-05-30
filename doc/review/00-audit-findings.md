# Audit Findings — Root Causes & Evidence

Date: 2026-05-29. Scope: merge-output correctness + LLM-hallucination containment.
Method: see [`README.md`](README.md). 44 confirmed defects (15 high / 18 medium /
11 low), clustering into 8 root-cause themes.

---

## The 8 root-cause themes

### A. Fail-OPEN terminal state
`COMPLETED` is the default sink for everything past Judge routing. Deterministic
findings (`missing_additive_export`, duplicate symbols, dropped escalations),
report-write failure, and `partial_failure` are **logged-only / non-blocking**,
and `state.errors` is only surfaced under `--ci` (the non-CI interactive branch
in `run.py:196` is effectively dead because browser mode goes through
`web_command_impl`, not `run_command_impl`). A run with recorded defects prints
"Merge completed successfully!" and exits 0.

### B. Vacuous / language-blind syntax gating
`check_syntax` (`src/tools/syntax_checker.py:34-36`) returns `valid=True` for every
extension except `.py/.json/.yaml/.yml`. The only **always-on per-file gate** is a
guaranteed false-negative for TS/JS/Go/Java/Rust — i.e. the entire target-language
surface for zod and most real forks. Worse, it gates the O-J1 high-confidence
Judge-skip, so an uncompilable `.ts` file is waved through as "reviewed clean."

### C. Hallucination guards wired on ONE path only
`find_invented_member_accesses` runs on the **chunked** semantic-merge path but
**not** on the common single-shot whole-file path (≤ `chunk_size_chars`), **not**
in the Judge, and **not** in report verification. The guard itself misses
chained-leaf members (`core.schemas._isoWeek`), uses substring-containment
whitelisting (`ref in src`), and the analyst's `grounding_warnings` fabrication
channel is advisory-only (surfaced to the human, never blocks an auto-merge).

### D. Truncation blindness = fail-open verdicts + runaway cost
Analyst / judge / commit-round all call `_call_llm_with_retry`, which **discards
`stop_reason`**; the parsers then swallow `ParseError` into an all-empty issue
list ("no issues found") or `confidence=0.5` ("auto-merge directional pick"). A
truncated Judge batch silently passes a broken merge as defect-free. The same
root cause drives the ~370k-token bisect re-run waste (see theme H / evidence).

### E. C-class (both-sides-modified) files bypass semantic review
Native git 3-way merge auto-commits C-class files at confidence 0.95 with **zero**
LLM/Judge review (`auto_merge.py:570-626`) and removes them from the batch before
the preservation audit. Dep-bump manifest auto-`TAKE_TARGET` overwrites fork pins
with no category check. Both are deterministic line-level blends of semantically
coupled fork+upstream edits — the highest-risk file category, least reviewed.

### F. Silent fork-feature loss under-detected
`preservation_auditor` only flags exact `worktree == upstream` byte-equality above
a 50-line floor (misses partial / sub-50-line loss), audits the wrong file list
(`batch.file_paths`, drained of native-3way files), and the only feature-drop
detector (`missing_additive_export`) is a non-blocking post-commit report line.

### G. Chunked-merge structural corruption
Equal-count `align_chunks` zips by **index** (mispairs / drops / duplicates whole
functions when an upstream insertion shifts the split point); force-split of an
oversized function splits mid-body producing brace-imbalanced halves; an
empty-upstream chunk invites fork-content drop; `merge_chunks` does no brace
check at the seam; `remove_duplicate_top_level_symbols` cannot dedup a JS/TS
`function` redeclaration.

### H. Elision / length-floor defeated
Gate-4 truncation floor is computed against the **TRIMMED staged size**, not the
real file (accepts 40-80% code loss), and `looks_truncated` short-circuits to
"clean" on any healthy-brace ending regardless of how much was dropped. The
single-shot executor is told to "preserve fork logic" over signature-only/elided
staged content, which itself invites fabrication or silent drop.

---

## Reproducible hard evidence

Run against the project venv from repo root. All four confirm a gap:

```python
from src.tools.syntax_checker import check_syntax
from src.tools.elision_detector import looks_truncated, has_elision
from src.llm.response_parser import parse_merge_result

# 1. TS syntax check is a no-op — broken TS passes.
check_syntax("a.ts", "export function foo( {  // unbalanced\n return bar(\n").valid
#   -> True   (expected False)

# 2. looks_truncated misses clean mid-file elision: 752 chars vs 4000 original (18%),
#    but ends with "}" so the healthy-tail short-circuit returns clean.
looks_truncated("function a(){ return 1 }\n"*30 + "}\n", current_size=4000, target_size=4000)
#   -> (False, None)

# 3. parse_merge_result accepts an elided merge when sizes are the TRIMMED staged sizes
#    (what execute_semantic_merge passes), because the 60% floor is computed against the
#    shrunken baseline.
parse_merge_result("function a(){ return 1 }\n"*30 + "}\n", current_size=760, target_size=760)
#   -> ACCEPTED (751 chars)

# 4. has_elision only catches explicit "...omitted" markers; a silent function drop
#    with no marker is undetected.
has_elision("function a(){return 1}\nfunction c(){return 3}\n")[0]
#   -> False
```

### Real-run evidence (zod run `898b53b5`, deepseek-v4-pro, output `max_tokens=8192`)

From `<zod>/.merge/runs/898b53b5-.../run.log`:

- Conflict analysis of `packages/zod/src/v4/core/schemas.ts` (the file's base blob is
  **148,359 chars**) sent `prompt_chars=219532` (~62,723 tokens), then re-sent ~219k-char
  prompts **six** more times back-to-back (`219588 / 225128 / 219460 / 219239 / 187884`).
  Cause: `_chunked_analyze_file` passes the **full, un-chunked `base_content`** into every
  chunk prompt (`conflict_analyst_agent.py:428-436`). ~370k+ input tokens for one file.
- Each call took 88-144 s; the whole run took 16+ min for only 7 C-class files and was
  interrupted mid-executor.
- `cost_tracker`: "No pricing entry for model 'deepseek-v4-pro' → cost recorded as $0" —
  the dollar budget cap cannot stop the runaway resend on an unpriced/proxy model.

### Note on the last run's config

The interrupted run used `fork_ref: merge/auto-20260529-122120` (a previous merge output)
as the fork, not the purpose-built conflict scenario. The clean, feature-rich scenario for
validation is `test/fork` ↔ `test/upstream` (merge-base `c59d4474`), which carries
fork features that **must** survive a correct merge: `ZodISOWeek` + `.week()`,
`cidrv6Mapped` regex, `ParsePayload.preValidated`, a fork version marker, and the fork-only
file `packages/zod/src/v4/fork/validators.ts`. This is also the origin of the
`core._isoWeek` hallucination referenced in `hallucinated_symbol_guard.py`'s docstring.

---

## Confirmed-findings index

The full machine-generated list (44 items with file:line, impact, proposed fix, and the
adversarial verdict that confirmed reachability) is preserved verbatim in
[`confirmed-findings.txt`](confirmed-findings.txt). Severity tally: 15 high, 18 medium,
11 low. Category tally: 14 gating-gap, 9 correctness-bug, 7 hallucination-gap,
5 prompt-quality, 5 cost-latency, 4 robustness.
