<div align="center">

[中文](README_zh.md) | **English**

# 🔀 Code Merge System

### Ship upstream upgrades to long-lived forks — without the 500-file conflict nightmare.

A multi-agent pipeline that turns months of upstream drift into an **auditable, resumable, and safe** merge — preserving every fork customization along the way.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-3776AB.svg?logo=python&logoColor=white)](https://python.org)
[![Tests](https://img.shields.io/badge/tests-passing-brightgreen.svg)](#development)
[![Coverage](https://img.shields.io/badge/coverage-80%25+-brightgreen.svg)](#development)
[![License](https://img.shields.io/badge/license-TBD-lightgrey.svg)](#license)
[![Anthropic](https://img.shields.io/badge/powered%20by-Claude%20%2B%20GPT-orange.svg)](https://anthropic.com)

![Code Merge System Dashboard](doc/project-1.png)

</div>

---

## The Problem

Teams that maintain a long-lived fork face a brutal reality when syncing with upstream:

- **Hundreds to thousands of file conflicts** — impossible to handle manually, one by one
- **Line-level diffs hide semantic intent** — LLMs and humans both make the wrong call
- **Fork-only customizations get silently overwritten** — APIs, routes, CI jobs, sentinels disappear without a trace
- **One wrong merge creates runtime vulnerabilities or missing features** — and they're hard to roll back

`git merge` gives you a list of conflicts. Code Merge System gives you a **decision pipeline**.

---

## Quick Start

```bash
pip install code-merge-system

export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...

cd /path/to/your-fork-repo
merge upstream/main --dry-run    # preview the plan before touching any files
```

> First run opens a browser UI and walks you through a one-time setup wizard. Your config is saved to `.merge/config.yaml` — no wizard on subsequent runs.

---

## See It In Action

<table>
<tr>
<td width="50%">

**Plan Review** — 124 files analyzed, 87.9% auto-merge confidence, risk distribution across A–E change categories.

![Plan Review](doc/project-2.png)

</td>
<td width="50%">

**Conflict Resolution** — Side-by-side intent analysis of fork vs. upstream changes, with LLM-recommended merge strategy (SEMANTIC_MERGE 85% confidence).

![Conflict Resolution](doc/project-4.png)

</td>
</tr>
<tr>
<td width="50%">

**Judge Verdict** — Independent review agent audits every merged file; CRITICAL/HIGH/MEDIUM/LOW issue breakdown with repair rounds.

![Judge Verdict](doc/project-5.png)

</td>
<td width="50%">

**Run Report** — Full cost accounting ($0.04 for 124 files), per-agent token breakdown, learned memory entries for future runs.

![Run Report](doc/project-6.png)

</td>
</tr>
</table>

---

## How It Works

Eight phases driven by a state machine. Seven specialized agents. Every write is snapshotted. Any `Ctrl+C` is safe.

```
┌─────────────────────────────────────────────────────────────┐
│  CLI / Web UI                                               │
│         │                                                   │
│   Orchestrator ── 8-phase state machine                    │
│         │                                                   │
│  ┌──────┴───────┐                                          │
│  │              │                                           │
│ Agents        Tools              Memory                     │
│ (7 roles)   (50+ deterministic   (L0/L1/L2                  │
│              + AST parsers)       cross-run store)          │
│  │                                                          │
│ LLM layer (Anthropic + OpenAI, credential pool, routing)   │
└─────────────────────────────────────────────────────────────┘
```

| Phase | What happens |
|-------|-------------|
| `INITIALIZE` | 3-way classification, risk scoring, fork-profile routing |
| `PLANNING` | Planner generates merge plan with per-file strategy |
| `PLAN_REVIEW` | PlannerJudge audits the plan; up to 2 revision rounds |
| `AWAITING_HUMAN` | You review the plan report; fill in any `HUMAN_REQUIRED` decisions |
| `AUTO_MERGING` | Executor applies auto-safe files with snapshot-before-write |
| `CONFLICT_ANALYSIS` | ConflictAnalyst does semantic analysis on risky conflicts |
| `JUDGE_REVIEW` | Judge + 50+ deterministic scanners audit all merged output |
| `COMPLETED` | Full report generated; you decide when to `git commit` |

| Agent | Role | Default Model |
|-------|------|---------------|
| Planner | Generates merge plan | Claude Opus |
| PlannerJudge | Reviews plan (read-only) | GPT-4o |
| ConflictAnalyst | Semantic analysis of high-risk conflicts | Claude Sonnet |
| Executor | **Sole write authority** — applies merges | GPT-4o |
| Judge | Reviews merged output + runs deterministic checks | Claude Opus |
| HumanInterface | Generates decision templates | Claude Haiku |
| SmokeTest | Post-merge smoke testing | — |

> **Why two LLM providers?** Planner/Judge use Anthropic; Executor/PlannerJudge use OpenAI. Different providers for reviewer vs. writer eliminates collusion bias.

---

## Features

### [Six Lost-Pattern Detectors](doc/modules/tools.md)
Shadow conflicts, interface reverse impacts, top-level call drops, config line preservation, scar auto-learning, and business sentinel scanning — the failure modes that `git merge` misses entirely.

### [Snapshot-Before-Write](doc/modules/core.md)
Every file write creates a snapshot of the original. Any failure triggers automatic rollback. You never end up with a half-merged file.

### [Full-Run Checkpointing](doc/modules/core.md)
State is persisted after every phase. `merge resume --run-id <id>` picks up exactly where you left off — useful for large merges that take hours.

### [Explicit Human Decisions](doc/modules/agents.md)
No `TIMEOUT_DEFAULT`. No silent fallbacks. Files that need human judgment generate a `decisions.yaml` template; skipped decisions stay as `AWAITING_HUMAN` until explicitly resolved.

### [Multi-Language AST Chunking](doc/modules/tools.md)
Python, TypeScript, JavaScript, Go, Rust, Java, and C all use tree-sitter for semantic-level diff — not just line-level.

### [Cross-Run Memory](doc/modules/memory.md)
Decisions, disputes, and metrics are summarized into a SQLite store. Future runs on the same repo load relevant history to inform planning.

### [Baseline-Diff Gate](doc/modules/tools.md)
CI validation only flags *newly introduced* failures — not pre-existing ones. Merging into a repo with a known broken test won't block you.

### [Browser Web UI](doc/modules/web-ui.md)
Real-time pipeline progress, conflict resolution UI, plan review, judge verdict — all in a local browser app. Use `--no-web` for pure terminal output or `--ci` for JSON output in CI.

---

## Compared to Alternatives

| | Code Merge System | `git merge` / `git rebase` | GitHub/GitLab UI | LLM chat (ChatGPT etc.) |
|--|--|--|--|--|
| Handles 500+ file conflicts | ✅ | ❌ Manual, one-by-one | ❌ | ❌ Context limit |
| Preserves fork-only features | ✅ Auto-detected via scar/sentinel | ❌ Easy to overwrite | ❌ | ❌ No repo context |
| Auditable decision trail | ✅ Per-file, with rationale | ❌ | Partial (PR comments) | ❌ |
| Resumable after interrupt | ✅ Checkpoint after every phase | ❌ | ❌ | ❌ |
| Deterministic safety checks | ✅ 50+ scanners post-merge | ❌ | ❌ | ❌ |
| Cost | ~$0.04 for 124 files | Free | Free | Per-token, no automation |

---

## Can You Trust the Output?

A merge tool is only worth as much as the evidence that its output is correct. This project ships a **formal evaluation framework** and an **auditable self-learning loop** — and reports their results honestly, including where the numbers are not yet impressive.

### Evaluation against human golden merges

We do **not** ask the LLM judge to grade its own verdict. The framework under [`doc/evaluation/`](doc/evaluation/README.md) measures system output against **expert human golden merges as ground truth**, scoring five trust dimensions at once — a system that blindly takes upstream and scores 100% "coverage" while losing half the fork's work must still fail:

| Dimension | Question it answers | Key metrics |
|-----------|--------------------|-------------|
| **Correctness** | Did it merge what should merge, correctly? | miss-merge rate, wrong-merge rate, conflict-resolution accuracy |
| **Safety** | Did it silently drop private changes? | M1–M6 semantic-loss recall, security-sensitive escalation rate, snapshot rollback rate |
| **Process Trust** | Does it escalate uncertainty instead of guessing? | over-escalation rate, plan-dispute hit rate, Judge↔ground-truth agreement |
| **Explainability** | Can every decision be replayed? | rationale completeness, `discarded_content` retention, trace replayability |
| **Operational** | Stable across re-runs and models? Cost bounded? | decision consistency, $/run, wall-time P95 |

Three dataset tiers feed it: **Tier-1** micro-bench (30–60 PRs, runs in CI), **Tier-2** real long-span replays (human merge diff = oracle), **Tier-3** adversarial injections (does it actually catch M1–M6?). The harness lives in [`scripts/eval/`](scripts/eval/) (`prepare.py → run.py → diff_against_golden.py → summarize.py → gate.py`).

**Hard gates that veto a release** ([`acceptance.md`](doc/evaluation/acceptance.md)): wrong-merge rate **= 0%**, security-sensitive escalation **= 100%**, private-content retention **= 100%**, snapshot rollback **= 100%**, duplicate top-level symbols **= 0**, hallucinated cross-module references **= 0**; miss-merge **≤ 2%** (Tier-1), each M1–M6 recall **≥ 95%**. Soft gates track overall accuracy (≥ 92% Tier-1), determinism (≥ 90% across 3 runs), cross-model consistency (≥ 85%), and cost/latency drift caps.

> **Honesty over marketing:** the version-baseline table in `acceptance.md` is still seeded with a template row — no release has cleared the full gate yet, so we make **no "evaluated & trusted" claim**. The framework exists precisely so that claim, when made, is backed by lockable dataset SHAs and per-file golden diffs rather than a "99% merge success" headline.

### Self-learning — measured, not assumed

The system improves across runs **without weight fine-tuning and without embeddings** — a deliberate choice backed by a 24-source survey (see [`doc/plan/self-learning-system.md`](doc/plan/self-learning-system.md)): non-parametric, auditable SQLite memory + execution-grounded reflection beats opaque RL on cost and deletability.

| Phase | What it does | Status |
|-------|-------------|--------|
| **P0** Effectiveness metric | Ablation harness: `memory=on` vs `memory=off` decision lift | **Landed** — `merge eval-memory` |
| **P1** Grounded feedback loop | Persistent auditable suppression of harmful entries · confidence write-back from `judge`+`compile`+`ci` signals · verified-repair recipe library | **Landed**, feedback loops **opt-in** until ablation proves net gain |
| **P2** Memory-quality hardening | High-information entries enforced · key invariants pinned against summarization drift | **Landed** |
| **P3** Offline prompt optimization | `merge optimize-prompts` ranks gate-prompt variants against a golden set, emits a **human-review report — never auto-applies** | **Landed**, opt-in |

The governing rule is **measure before you activate**: a feedback loop only flips to on-by-default after `merge eval-memory` shows lift **> 0** *and* causally-attributed harm **= 0** on a fixed dataset. First baseline (forgejo, 124 files): lift measured at **0.0000** — so the loops stay opt-in. That run was dominated by deterministic mechanisms (take-target + veto), leaving memory no room to act; it does **not** prove memory worthless, and an LLM-judgment-dense dataset is needed to measure real lift. We report the zero rather than hide it — that *is* the trust signal.

---

## Prerequisites

| | |
|--|--|
| Python 3.11+ | mypy strict / Pydantic v2 / async throughout |
| `ANTHROPIC_API_KEY` | Planner, ConflictAnalyst, Judge, HumanInterface |
| `OPENAI_API_KEY` | PlannerJudge, Executor (dual-provider anti-collusion) |
| `GITHUB_TOKEN` *(optional)* | GitHub integration — pull PR comments, push merge results |
| Node.js *(optional)* | Web UI development only; the installed wheel bundles `web/dist/` |

**Target repo must:**
- Be a git repo with a clean working tree (`git status` shows no uncommitted changes)
- Have upstream accessible locally — either as a branch or via `git fetch <remote>`

```bash
# If you haven't added upstream yet:
git remote add upstream https://github.com/<owner>/<repo>.git
git fetch upstream
```

---

## Full Workflow

### 1. Plan (dry-run)

```bash
cd /path/to/your-fork-repo
merge upstream/main --dry-run
```

The browser UI opens and runs through `INITIALIZE → PLANNING → PLAN_REVIEW → AWAITING_HUMAN` then stops. Check the output reports:

```
.merge/plans/MERGE_PLAN_<run_id>.md   # file-by-file merge strategy
.merge/runs/<run_id>/plan_review.md   # PlannerJudge audit record
```

### 2. Merge

```bash
merge upstream/main     # remove --dry-run to run for real
```

Any `Ctrl+C` is safe — resume with `merge resume --run-id <id>`.

### 3. Handle Human Decisions

When the system pauses at `AWAITING_HUMAN`, fill in `.merge/runs/<id>/decisions.yaml`:

```yaml
- file_path: "backend/services/auth/auth.service.ts"
  decision: take_current          # take_target / take_current / semantic_merge / escalate_human
  rationale: "Fork uses SSO — must preserve"
```

Then resume:

```bash
merge resume --run-id <id> --decisions .merge/runs/<id>/decisions.yaml
```

### 4. Review and commit

```
.merge/runs/<run_id>/merge_report.md    # final report
.merge/runs/<run_id>/checkpoint.json    # full state
.merge/runs/<run_id>/logs/run_<id>.log  # complete execution log
```

The system stops at the working tree. **It never auto-commits or auto-pushes** — you review, then decide.

---

## All Commands

```bash
# Daily use
merge <target-branch>                         # default: browser Web UI
merge <target-branch> --dry-run               # plan only, no file writes
merge <target-branch> --no-web                # terminal output
merge <target-branch> -r                      # re-run setup wizard

# Resume / decisions
merge resume --run-id <id>
merge resume --run-id <id> --decisions decisions.yaml
merge resume --run-id <id> --web              # view history in browser

# Validate
merge validate --config <path>                # check config + all API keys

# Fork profile (only needed when fork deleted ≥30 files)
merge forks-profile init -o .merge/forks-profile.yaml
merge forks-profile diff
merge forks-profile validate

# CI
merge <target-branch> --ci                    # non-interactive, JSON summary to stdout
merge <target-branch> --ci --auto-decisions <yaml>
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `API Key not set` | Run `merge validate --config .merge/config.yaml`; check shell env → `.merge/.env` → `~/.config/code-merge-system/.env` |
| `working tree dirty` | `git stash` or `git commit`, then re-run |
| `upstream ref not found` | Run `git fetch upstream`; use `upstream/main` not `main` |
| Plan review stuck in multiple rounds | Normal — Planner and PlannerJudge are negotiating; after `max_plan_revision_rounds=2` it transitions to `AWAITING_HUMAN`. Check `plan_review.md`. |
| Run interrupted mid-way | `merge resume --run-id <id>` (find `run_id` under `.merge/runs/`) |
| Want to start over | `rm -rf .merge/runs/<id>/`, then re-run |

---

## Development

```bash
git clone <repo-url> && cd code-merge-system
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest tests/unit/ -q               # unit tests (no LLM calls)
pytest tests/integration/ -v        # integration tests (real API, local only)
mypy src                            # type check (strict)
ruff check src/ && ruff format src/ # lint + format

# Web UI (only needed for frontend changes)
cd web && npm install
cd web && npm run dev               # Vite dev server at localhost:5173
cd web && npm run build             # tsc + build → web/dist/
cd web && npm test                  # vitest
```

**Architecture constraints enforced by unit tests — do not violate:**

- No `TIMEOUT_DEFAULT` on `DecisionSource` — human decisions must be explicit
- `Judge` / `PlannerJudge` receive `ReadOnlyStateView` — no state writes from reviewer agents
- `Executor` uses `apply_with_snapshot()` — no direct file writes
- `plan_revision_rounds >= max` → `AWAITING_HUMAN`, not `FAILED`
- `HumanInterface` never fills in default decisions

---

## Contributing

Contributions are welcome — whether it's a bug report, a feature idea, or a pull request.

**Good places to start:**

- 🐛 **[Report a bug](../../issues/new?template=bug_report.md)** — include your Python version, the command you ran, and the relevant log from `.merge/runs/<id>/logs/`
- 💡 **[Request a feature](../../issues/new?template=feature_request.md)** — describe your fork/upstream scenario and what the system currently gets wrong
- 🔧 **[Browse open issues](../../issues)** — look for `good first issue` labels if you want a guided starting point

**Before submitting a PR:**

1. Run `pytest tests/unit/` — all tests must pass
2. Run `mypy src` — no new type errors
3. Run `ruff check src/` — no lint errors
4. Keep new files under 800 lines; organize by feature layer (`models → tools → llm → agents → core → cli`)
5. New agents require a contract yaml under `src/agents/contracts/` — see [`src/agents/contracts/_schema.md`](src/agents/contracts/_schema.md)

**Key docs for contributors:**

- [System Architecture](doc/architecture.md) — layers, data flow, persistence, extension points
- [State Machine & Phases](doc/flow.md) — all 13 states and 8 phases
- [Agent Contracts](src/agents/contracts/_schema.md) — how to add a new agent correctly
- [Adding a New Agent](doc/modules/agents.md) — step-by-step recipe

---

## Documentation

Full index: [`doc/README.md`](doc/README.md)

| | |
|--|--|
| [Onboarding Guide](doc/modules/onboarding.md) | Start here if you're new to the project |
| [Architecture](doc/architecture.md) | Layers, data flow, persistence, extension points |
| [Flow & State Machine](doc/flow.md) | 13 states, 8 phases |
| [Six Lost Patterns + P0/P1/P2 Hardening](doc/multi-agent-optimization-from-merge-experience.md) | How we catch what `git merge` misses |
| [Evaluation Framework](doc/evaluation/README.md) | Golden-merge ground truth, 5 trust dimensions, 3 dataset tiers, acceptance gates |
| [Self-Learning System](doc/plan/self-learning-system.md) | Non-parametric memory + grounded feedback loop, phased rollout |
| [Migration-Aware Merge](doc/migration-aware-merge.md) | Handling bulk-copy scenarios |
| [Risk Levels](doc/risk-levels.md) | How files are classified A–E |
| [Web UI User Journey](doc/web-ui.md) | Browser-side walkthrough |

---

## License

MIT

---

<div align="center">
  <sub>Built for teams that maintain long-lived forks and need more than <code>git merge</code>.</sub>
</div>
