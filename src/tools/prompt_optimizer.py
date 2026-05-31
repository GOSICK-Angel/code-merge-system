"""Phase 3: offline, opt-in prompt/strategy optimization harness.

GEPA / MIPROv2-style prompt evolution, scoped to the **deterministic, testable**
core: generate named candidate variants of a gate's system prompt, score each
against a labelled golden set given precomputed rollouts, rank, and emit a
**human-review** report. It NEVER mutates `gate_registry` — gates are code
builders, so applying a winning candidate is a manual edit a human makes after
reviewing the report (the plan's "人工评审后才生效").

The expensive part — running the model with each candidate to produce decisions
— is intentionally OUT of this module. It is injected as a ``rollouts`` mapping
(candidate_id -> {case_id: produced_decision}) so the pure harness stays
unit-testable and offline; producing rollouts is the operator's documented,
cost-bearing step (PromptBreeder ~$60/10k calls — see
doc/plan/self-learning-system.md Phase 3).
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel, Field

# --- mutation operators (deterministic, reflective-instruction injection) ----

# GEPA's reflective mutations need an LLM; these are the safe deterministic
# subset — each appends one well-known prompting directive. An LLM-reflective
# generator can be layered on later behind the same PromptCandidate interface.
_DIRECTIVES: dict[str, str] = {
    "stepwise": (
        "Before deciding, reason step by step through the specific evidence; "
        "do not pattern-match to prior cases."
    ),
    "selfcheck": (
        "After drafting your decision, re-read the inputs and verify each claim "
        "is grounded in the provided content; revise if any is unsupported."
    ),
    "output_format": (
        "Return ONLY the required structured output — no preamble, no commentary "
        "outside the specified fields."
    ),
    "evidence_first": (
        "Cite the exact lines or symbols you relied on before stating any "
        "conclusion; an unsupported conclusion is a failure."
    ),
}


def _append_directive(directive: str) -> Callable[[str], str]:
    def _mutate(base: str) -> str:
        return f"{base.rstrip()}\n\n{directive}"

    return _mutate


MUTATION_STRATEGIES: dict[str, Callable[[str], str]] = {
    name: _append_directive(text) for name, text in _DIRECTIVES.items()
}

BASELINE_ID = "baseline"


# --- models -----------------------------------------------------------------


class PromptCandidate(BaseModel, frozen=True):
    candidate_id: str
    gate_id: str
    strategy: str
    prompt_text: str


class GoldenCase(BaseModel, frozen=True):
    case_id: str
    expected_decision: str


class CandidateScore(BaseModel, frozen=True):
    candidate_id: str
    cases_scored: int = Field(ge=0)
    correct: int = Field(ge=0)
    accuracy: float = Field(ge=0.0, le=1.0)


class CostLedger(BaseModel):
    llm_calls: int = Field(default=0, ge=0)
    est_usd: float = Field(default=0.0, ge=0.0)

    def record(self, *, llm_calls: int, est_usd: float) -> "CostLedger":
        return CostLedger(
            llm_calls=self.llm_calls + llm_calls,
            est_usd=round(self.est_usd + est_usd, 4),
        )


class OptimizationReport(BaseModel, frozen=True):
    gate_id: str
    baseline_id: str
    candidates: list[PromptCandidate]
    scores: list[CandidateScore]
    winner_id: str | None
    margin: float
    cost: CostLedger
    notes: list[str] = Field(default_factory=list)


# --- harness ----------------------------------------------------------------


def propose_variants(
    gate_id: str,
    base_prompt: str,
    strategies: list[str] | None = None,
) -> list[PromptCandidate]:
    """Baseline + one candidate per requested strategy.

    Strategies that produce text identical to the baseline (or to an earlier
    candidate) are dropped — a no-op mutation is not a distinct candidate."""
    names = strategies if strategies is not None else list(MUTATION_STRATEGIES)
    seen: set[str] = {base_prompt}
    candidates = [
        PromptCandidate(
            candidate_id=BASELINE_ID,
            gate_id=gate_id,
            strategy=BASELINE_ID,
            prompt_text=base_prompt,
        )
    ]
    for name in names:
        mutate = MUTATION_STRATEGIES.get(name)
        if mutate is None:
            continue
        text = mutate(base_prompt)
        if text in seen:
            continue
        seen.add(text)
        candidates.append(
            PromptCandidate(
                candidate_id=name,
                gate_id=gate_id,
                strategy=name,
                prompt_text=text,
            )
        )
    return candidates


def score_candidates(
    candidates: list[PromptCandidate],
    rollouts: dict[str, dict[str, str]],
    golden: list[GoldenCase],
) -> list[CandidateScore]:
    """Decision accuracy per candidate against ``golden``.

    ``rollouts[candidate_id][case_id]`` is the decision that candidate's prompt
    produced for that case (operator-supplied). A candidate with no rollout
    scores ``cases_scored=0`` rather than a fabricated number — unscored is
    surfaced, never silently treated as zero-correct."""
    expected = {c.case_id: c.expected_decision for c in golden}
    scores: list[CandidateScore] = []
    for cand in candidates:
        produced = rollouts.get(cand.candidate_id, {})
        scored = 0
        correct = 0
        for case_id, exp in expected.items():
            if case_id not in produced:
                continue
            scored += 1
            if produced[case_id] == exp:
                correct += 1
        accuracy = round(correct / scored, 4) if scored else 0.0
        scores.append(
            CandidateScore(
                candidate_id=cand.candidate_id,
                cases_scored=scored,
                correct=correct,
                accuracy=accuracy,
            )
        )
    return scores


def select_winner(
    scores: list[CandidateScore],
    baseline_id: str = BASELINE_ID,
    margin: float = 0.02,
) -> str | None:
    """The highest-accuracy candidate, but only if it beats the baseline by at
    least ``margin`` AND was actually scored. Ties and within-margin gains keep
    the baseline — never churn a production prompt for noise."""
    by_id = {s.candidate_id: s for s in scores}
    base = by_id.get(baseline_id)
    if base is None or base.cases_scored == 0:
        return None
    best: CandidateScore | None = None
    for s in scores:
        if s.candidate_id == baseline_id or s.cases_scored == 0:
            continue
        if best is None or s.accuracy > best.accuracy:
            best = s
    if best is None:
        return None
    if best.accuracy - base.accuracy >= margin:
        return best.candidate_id
    return None


def build_report(
    gate_id: str,
    candidates: list[PromptCandidate],
    golden: list[GoldenCase],
    rollouts: dict[str, dict[str, str]],
    margin: float = 0.02,
    cost: CostLedger | None = None,
) -> OptimizationReport:
    scores = score_candidates(candidates, rollouts, golden)
    winner = select_winner(scores, margin=margin)
    notes: list[str] = []
    if not golden:
        notes.append("No golden set supplied — candidates generated but unscored.")
    unscored = [s.candidate_id for s in scores if s.cases_scored == 0]
    if golden and unscored:
        notes.append(
            "Unscored candidates (no rollout supplied): " + ", ".join(unscored)
        )
    if golden and winner is None and any(s.cases_scored for s in scores):
        notes.append(
            f"No candidate beat baseline by the {margin:.0%} margin — keep current."
        )
    return OptimizationReport(
        gate_id=gate_id,
        baseline_id=BASELINE_ID,
        candidates=candidates,
        scores=scores,
        winner_id=winner,
        margin=margin,
        cost=cost or CostLedger(),
        notes=notes,
    )


def render_report_markdown(report: OptimizationReport) -> str:
    lines = [
        f"# Prompt optimization report — gate `{report.gate_id}`",
        "",
        "> Candidates are NOT auto-applied. Gates are code builders; to adopt a "
        "winner, a human reviews its `prompt_text` below and edits the gate's "
        "prompt source manually.",
        "",
        f"- baseline: `{report.baseline_id}`",
        "- winner: "
        + (f"`{report.winner_id}`" if report.winner_id else "_none (kept baseline)_"),
        f"- margin: {report.margin:.0%}",
        f"- cost: {report.cost.llm_calls} LLM calls, ~${report.cost.est_usd:.2f}",
        "",
        "## Scores",
        "",
        "| candidate | strategy | scored | correct | accuracy |",
        "|---|---|---|---|---|",
    ]
    strategy_by_id = {c.candidate_id: c.strategy for c in report.candidates}
    for s in report.scores:
        lines.append(
            f"| `{s.candidate_id}` | {strategy_by_id.get(s.candidate_id, '?')} "
            f"| {s.cases_scored} | {s.correct} | {s.accuracy:.2%} |"
        )
    if report.notes:
        lines += ["", "## Notes", ""]
        lines += [f"- {n}" for n in report.notes]
    return "\n".join(lines)
