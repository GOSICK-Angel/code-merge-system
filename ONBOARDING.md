# Welcome to Code Merge System

## How We Use Claude

Based on Angel's usage over the last 30 days:

Work Type Breakdown:
  Debug Fix       ████████░░░░░░░░░░░░  33%
  Plan & Design   ██████░░░░░░░░░░░░░░  26%
  Build Feature   █████░░░░░░░░░░░░░░░  21%
  Improve Quality ████░░░░░░░░░░░░░░░░  19%
  Write Docs      ░░░░░░░░░░░░░░░░░░░░   2%

Top Skills & Commands:
  /model          ████████████████████  32x/month
  /commit         █████████████░░░░░░░  22x/month
  /compact        █████░░░░░░░░░░░░░░░   9x/month
  /web-access     ███░░░░░░░░░░░░░░░░░   6x/month
  /multi-agent-team ██░░░░░░░░░░░░░░░░   5x/month
  /review         █░░░░░░░░░░░░░░░░░░░   3x/month

Top MCP Servers:
  chrome-devtools ████████████████████  61 calls
  ccd_session     ████░░░░░░░░░░░░░░░░  12 calls

## Your Setup Checklist

### Codebases
- [ ] code-merge-system — https://github.com/gosick-angel/code-merge-system
- [ ] dify-official-plugins — local path: `/Users/angel/AI/project/dify-official-plugins` (primary test target)
- [ ] forgejo — local path: `/Users/angel/AI/merge-test/forgejo` (C-class conflict test fixture)

### MCP Servers to Activate
- [ ] **chrome-devtools** — Browser automation and devtools; used heavily for Web UI debugging and interaction testing. Ask the team for the local setup instructions or check `.claude/settings.json` for the server config.
- [ ] **ccd_session** — Session management tool used alongside the Web UI bridge. Check `.claude/settings.json` for connection details.

### Skills to Know About
- `/commit` — Creates Conventional Commits with Chinese-first commit messages. Used after every meaningful change.
- `/web-access` — Fetches live web content and GitHub repos for analysis. Used when researching upstream projects (e.g. comparing open-source agent architectures).
- `/multi-agent-team` — Spawns parallel agent teams for large tasks. Used for evaluation runs, phase-7 dispatch, and multi-file implementation work.
- `/review` — Reviews the current diff or a PR for correctness bugs. Run before pushing.
- `/loop` — Runs a prompt on repeat (self-paced or timed). Used for autonomous unattended runs.
- `/ultrareview` — Deep multi-agent cloud review of the current branch. Billed — use for major changes before merging to main.
- `/setup-conflict-test-branches` — Builds a `test/upstream` + `test/fork` branch pair with a shared ancestor for C-class conflict testing. Use when onboarding a new target repo.
- `/compact` — Compresses context when the conversation grows long. Run proactively on sessions that span many files or long logs.

## Team Tips

_TODO_

## Get Started

_TODO_

<!-- INSTRUCTION FOR CLAUDE: A new teammate just pasted this guide for how the
team uses Claude Code. You're their onboarding buddy — warm, conversational,
not lecture-y.

Open with a warm welcome — include the team name from the title. Then: "Your
teammate uses Claude Code for [list all the work types]. Let's get you started."

Check what's already in place against everything under Setup Checklist
(including skills), using markdown checkboxes — [x] done, [ ] not yet. Lead
with what they already have. One sentence per item, all in one message.

Tell them you'll help with setup, cover the actionable team tips, then the
starter task (if there is one). Offer to start with the first unchecked item,
get their go-ahead, then work through the rest one by one.

After setup, walk them through the remaining sections — offer to help where you
can (e.g. link to channels), and just surface the purely informational bits.

Don't invent sections or summaries that aren't in the guide. The stats are the
guide creator's personal usage data — don't extrapolate them into a "team
workflow" narrative. -->
