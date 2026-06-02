# Risk Levels

## Enum (`src/models/diff.py`)

| Level | Value | Trigger |
|-------|-------|---------|
| **AUTO_SAFE** | `auto_safe` | `risk_score < 0.3` |
| **AUTO_RISKY** | `auto_risky` | `0.3 <= risk_score < 0.6` |
| **HUMAN_REQUIRED** | `human_required` | `risk_score >= 0.6`; or security-sensitive file (`max(score, 0.8)`) |
| **DELETED_ONLY** | `deleted_only` | `file_status == DELETED` and `lines_added == 0` |
| **BINARY** | `binary` | Extension in `binary_extensions`; or `file_status == BINARY` |
| **EXCLUDED** | `excluded` | Path matches `excluded_patterns` (e.g. `*.lock`, `node_modules/**`) |

## Risk Score Calculation (`src/tools/file_classifier.py:compute_risk_score`)

Weighted factors:

| Factor | Weight | Source |
|--------|--------|--------|
| size | 0.15 | `lines_added + lines_deleted` |
| conflict_density | 0.35 | `conflict_count / total_lines` |
| change_ratio | 0.20 | `lines_changed / file_size` |
| file_type | 0.20 | Language-based (e.g. config files score higher) |
| security | 0.10 | Matches `security_sensitive.patterns` |

Special overrides:
- `always_take_target_patterns` -> score = 0.1 (AUTO_SAFE)
- Security-sensitive files -> `max(raw_score, 0.8)` (HUMAN_REQUIRED)

## Thresholds (`MergeConfig.thresholds`)

| Threshold | Default | Purpose |
|-----------|---------|---------|
| `risk_score_low` | 0.30 | Below = AUTO_SAFE |
| `risk_score_high` | 0.60 | Above = HUMAN_REQUIRED |
| `auto_merge_confidence` | 0.85 | Executor confidence for auto-merge |

## Handling by Phase

### Classification (`file_classifier.py:classify_file`)

```
EXCLUDED?  -> EXCLUDED (skip entirely)
BINARY?    -> BINARY
DELETED?   -> DELETED_ONLY
score < 0.3 -> AUTO_SAFE
score < 0.6 -> AUTO_RISKY
score >= 0.6 -> HUMAN_REQUIRED
```

### Executor (`executor_agent.py`)

Only processes **AUTO_SAFE** and **AUTO_RISKY**:

| Category | AUTO_SAFE | AUTO_RISKY | HUMAN_REQUIRED | DELETED_ONLY |
|----------|-----------|------------|----------------|--------------|
| A (unchanged) | SKIP | - | pre-pass → human | pre-pass → human |
| B (upstream only) | TAKE_TARGET | - | pre-pass → human | pre-pass → human |
| C (both changed) | TAKE_TARGET | SEMANTIC_MERGE | pre-pass → human | pre-pass → human |
| D_MISSING (new) | TAKE_TARGET | - | pre-pass → human | pre-pass → human |
| D_EXTRA (current) | SKIP | - | pre-pass → human | pre-pass → human |
| E (current only) | SKIP | - | pre-pass → human | pre-pass → human |

`DELETED_ONLY` is handled in the AutoMerge **pre-pass**: `executor.analyze_deletion()` generates a
`UserDecisionItem` (with LLM-backed rationale) that is queued for human decision before any merge
execution begins. The human then chooses: approve deletion / keep file / take upstream version.

### Orchestrator Phase Routing

| Risk Level | Auto-merge? | Conflict Analyst? | Human Escalation? | Judge Review? |
|------------|-------------|--------------------|--------------------|---------------|
| AUTO_SAFE | YES | NO | NO | YES |
| AUTO_RISKY | YES (parallel) | YES | Conditional | YES |
| HUMAN_REQUIRED | NO | NO | YES (pre-pass) | YES |
| DELETED_ONLY | NO | NO | YES (pre-pass) | YES |
| BINARY | NO | NO | Escalated | YES |
| EXCLUDED | NO | NO | NO | NO |

### Flow Summary

```
file_classifier assigns risk level
    |
    v
Planner creates batches grouped by risk level
    |
    v
PlannerJudge reviews (may reclassify)
    |
    v
Human approves plan
    |
    v
AutoMerge pre-pass:
  HUMAN_REQUIRED -> generate UserDecisionItem -> AWAITING_HUMAN
  DELETED_ONLY   -> analyze_deletion() -> UserDecisionItem -> AWAITING_HUMAN
    |
    v (if no pre-pass decisions pending)
Executor: AUTO_SAFE + AUTO_RISKY -> auto-merge (parallel within each layer)
  after each layer: Judge batch sub-review + Executor↔Judge dispute loop
    |
    v
ConflictAnalyst: AUTO_RISKY -> deeper conflict analysis
    |
    v
Judge: final review of all decisions (Executor↔Judge negotiation, no hard veto)
```

## PlannerJudge Reclassification

The PlannerJudge can flag files whose risk level should be upgraded:

```json
{
  "file_path": "path/to/file",
  "current_classification": "auto_safe",
  "suggested_classification": "human_required",
  "reason": "File contains auth logic",
  "issue_type": "risk_underestimated | security_missed | wrong_batch | missing_dependency"
}
```

Valid `issue_type` values:
- `risk_underestimated`: Risk score too low for the file's actual impact
- `security_missed`: Security-sensitive file not flagged
- `wrong_batch`: File placed in incorrect merge batch
- `missing_dependency`: Cross-file dependency not accounted for
