from src.models.diff import FileDiff


# P3-3: the system prompt now anchors what the score means and how the bands
# map to merge decisions, instead of a bare "provide a risk score". The numeric
# band edges are supplied per-call from ThresholdConfig (risk_score_low /
# risk_score_high) so the prompt cannot drift from the config that actually
# gates the merge.
RISK_SCORING_SYSTEM = (
    "You are a code risk assessment specialist. Analyze the given file diff and "
    "return a calibrated merge-risk score in [0.0, 1.0]: higher means a greater "
    "chance the change breaks the build or silently drops fork-side logic when "
    "auto-merged. Calibrate to the scoring bands stated in the prompt — scores "
    "below the low edge are safe auto-merge candidates, scores at or above the "
    "high edge need human or analyst review, and the range in between is medium "
    "risk that warrants conflict analysis."
)


def build_risk_scoring_prompt(
    file_diff: FileDiff,
    rule_score: float,
    risk_score_low: float = 0.30,
    risk_score_high: float = 0.60,
) -> str:
    ext = (
        file_diff.file_path.rsplit(".", 1)[-1]
        if "." in file_diff.file_path
        else "unknown"
    )

    hunk_summaries = []
    for h in file_diff.hunks[:10]:
        hunk_summaries.append(
            f"  - Lines {h.start_line_current}-{h.end_line_current}: "
            f"conflict={'yes' if h.has_conflict else 'no'}"
        )
    hunks_text = "\n".join(hunk_summaries) if hunk_summaries else "  (no hunks)"

    return f"""Analyze the risk of merging changes to this file.

File: {file_diff.file_path}
Extension: .{ext}
Lines added: {file_diff.lines_added}
Lines deleted: {file_diff.lines_deleted}
Lines changed: {file_diff.lines_changed}
Security sensitive: {file_diff.is_security_sensitive}
Rule-based risk score: {rule_score:.3f}

Hunks:
{hunks_text}

Scoring bands (calibrate llm_risk_score to these):
- < {risk_score_low:.2f} → low risk: routine change, safe to auto-merge
- {risk_score_low:.2f}–{risk_score_high:.2f} → medium risk: auto-merge with conflict analysis
- >= {risk_score_high:.2f} → high risk: needs human / analyst review

Respond with ONLY a JSON object:
{{
  "llm_risk_score": <float between 0.0 and 1.0>,
  "reasoning": "<brief explanation>",
  "risk_factors": ["<factor1>", "<factor2>"]
}}"""
