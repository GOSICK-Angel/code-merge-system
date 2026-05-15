# Web UI dev harness

Tooling for iterating on the Web UI without running a real `merge <branch>`
flow (which requires a fork repo + API keys).

## Mock WebSocket bridge

`mock-bridge.py` boots a real `MergeWSBridge` against a fabricated
`MergeState` and drip-feeds:

- A completed `analysis` phase + a `plan_review` phase running for ~12 s
  (so `phaseElapsed` renders in the timeline)
- 5 `agent_activity` events spread over ~7 s (planner → planner_judge)
- A `cost_summary` snapshot ($0.42 / 18,452 tokens)
- A transition to `AWAITING_HUMAN` after 10 s with **4 conflict
  requests** injected so the front-end derives the L3 view:
  - `api/auth.py` (priority 8, analyst recommends TAKE_CURRENT, 2
    conflict points: INTERFACE_CHANGE high + DEPENDENCY_UPDATE medium)
  - `config/database.yaml` (priority 6, analyst recommends
    SEMANTIC_MERGE, 1 CONFIGURATION low)
  - `utils/retry.py` (priority 4, analyst recommends TAKE_TARGET, 2
    LOGIC_CONTRADICTION)
  - `docs/CHANGELOG.md` (priority 2, analyst recommends ESCALATE_HUMAN —
    the L3 "Apply recommended to all" button must skip this one)

Because it uses the production `MergeWSBridge` class directly, the
on-wire schema is identical to a real run — no test shims.

## Usage

```bash
# Terminal A: backend
python web/dev/mock-bridge.py

# Terminal B: frontend dev server
cd web && npm run dev

# Browser
open http://localhost:5173/?ws=8765
```

### What you should see

| L1 widget                | Expected on load                                          |
|--------------------------|-----------------------------------------------------------|
| Status banner            | `PLANNING`, conn dot green                                |
| Phase timeline           | `analysis` past (elapsed shown), `plan_reviewing` current |
| Cost card                | `$0.4231` / `18.5k tokens`                                |
| Decisions card           | empty (no records yet)                                    |
| Risk overview            | `No diffs yet`                                            |
| Agent activity stream    | 5 events appear within ~7 s                               |
| Cancel button            | starts disabled; enables after the 10 s transition        |

Click Cancel after the AWAITING_HUMAN transition: the button should
trigger no error banner. Click Cancel before that transition: the red
`cancel_error` banner should appear with `reason: not_in_human_gate`.

### L3 conflict resolution (after the 10 s transition)

| L3 widget                    | Expected                                                                 |
|------------------------------|--------------------------------------------------------------------------|
| View switch                  | `classifyView` auto-routes to L3 (4 conflicts pending)                   |
| File tree                    | 4 entries sorted by priority (8 → 6 → 4 → 2)                             |
| Conflict point markers       | Expand to show upstream/fork intents + risk_factors + suggested_decision |
| Diff viewer                  | Split view (upstream left, fork right) — `react-diff-viewer-continued`   |
| Decision panel               | 5 selectable options (ESCALATE_HUMAN omitted per plan v1.1 §4 L3)        |
| MANUAL_PATCH validation      | Submit disabled until `custom_content` is non-empty                      |
| Apply recommended to all     | Drafts 3 files (skips CHANGELOG since its recommendation is ESCALATE)    |
| Submit all drafts            | Sends `submit_conflict_decisions_batch`; bridge marks decided + broadcast |
| Decided files                | Badge flips to submitted colour; moves to "Decided" section in the tree  |

After all 4 files are submitted, `human_decisions_received` event fires
in the bridge and the run loop would advance — in the mock harness the
state stays at AWAITING_HUMAN (no orchestrator), but the file tree's
"Decided (4)" section will reflect the submitted decisions.

## Extending

When implementing L2/L4/L5 views in later phases, add a new sample
function in `mock-bridge.py` and document the expected widget state in
the table above.
