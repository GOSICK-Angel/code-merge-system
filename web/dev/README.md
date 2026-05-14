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
- A transition to `AWAITING_HUMAN` after 10 s so the Cancel button
  becomes enabled and the `cancel_run` round-trip can be exercised

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

## Extending

When implementing L2/L3/L4 views in later phases, add a new sample
function in `mock-bridge.py` and document the expected widget state in
the table above.
