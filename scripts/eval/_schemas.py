"""Pydantic v2 schemas for evaluation artifacts.

Mirrors the json shapes documented in
``doc/evaluation/procedure.md`` §3.2 (``eval_diff_<version>.json``),
§3.3 (``eval_acceptance_<version>.json``), and the per-run metadata that
``run.py`` is required to emit alongside each sample.

All models are :class:`pydantic.BaseModel` with ``frozen=True`` —
evaluation pipeline data is immutable; transformations produce new
instances rather than mutating in place. This mirrors the convention in
``src/models/decision.py`` (``FileDecisionRecord``) and matches CLAUDE.md
"Code Style — Immutable patterns".
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


_FROZEN = ConfigDict(frozen=True, extra="forbid")


# ---------------------------------------------------------------------------
# Per-sample diff entry (procedure.md §3.2)
# ---------------------------------------------------------------------------


class MatchStatus(str, Enum):
    """Per-sample comparison verdict.

    Mirrors the labels surfaced in ``eval_diff_<version>.json.samples[].match``.
    """

    EXACT = "EXACT"
    SEMANTIC = "SEMANTIC"
    MISMATCH = "MISMATCH"


class MismatchLabel(str, Enum):
    """Sub-category of MISMATCH used by metric formulas (metrics.md §1.2).

    ``MISS_UPSTREAM`` / ``MISS_FORK`` feed into MMR; ``WRONG_MERGE`` feeds
    into WMR; ``EXTRA_NOISE`` feeds into noise metrics.
    ``MISSING_REPORT`` (F5) marks a sample that the system attempted but
    failed before producing a merge_report (e.g. the empty_plan guardrail
    aborted analysis); feeds into RR per metrics.md §5.3.
    """

    MISS_UPSTREAM = "MISS_UPSTREAM"
    MISS_FORK = "MISS_FORK"
    WRONG_MERGE = "WRONG_MERGE"
    EXTRA_NOISE = "EXTRA_NOISE"
    MISSING_REPORT = "MISSING_REPORT"


class SystemDecision(BaseModel):
    """The merge system's per-sample decision read from ``merge_report_<run_id>.json``.

    Matches the nested ``system_decision`` shape in procedure.md §3.2.
    """

    model_config = _FROZEN

    strategy: str
    risk: str
    human: bool


_SemanticEngine = Literal["tree-sitter", "fallback-bytes"]


class DiffEntry(BaseModel):
    """One row in :attr:`DiffReport.samples`.

    Per plan decision 1, the schema extends procedure.md §3.2 with three
    optional fields used by RCR / DCRR / SSER metrics.
    """

    model_config = _FROZEN

    sample_id: str
    category: str
    loss_class: str | None = None
    expected_human: bool
    system_decision: SystemDecision
    match: MatchStatus
    label: MismatchLabel | None = None
    missed_lines: int = Field(default=0, ge=0)
    extra_lines: int = Field(default=0, ge=0)

    rationale_length: int = Field(default=0, ge=0)
    discarded_content_present: bool = False
    is_security_sensitive: bool = False


class DiffReportMeta(BaseModel):
    """Metadata block on :class:`DiffReport`.

    ``semantic_engine`` documents whether tree-sitter was used or the byte
    normalize fallback (plan decision 4). Avoids accidentally counting a
    fallback equality as a true AST match.
    """

    model_config = _FROZEN

    semantic_engine: _SemanticEngine
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DiffReport(BaseModel):
    """Top-level shape of ``eval_diff_<version>.json``."""

    model_config = _FROZEN

    tier: int = Field(ge=1, le=3)
    samples: tuple[DiffEntry, ...] = ()
    meta: DiffReportMeta


# ---------------------------------------------------------------------------
# Acceptance gate (procedure.md §3.3 / acceptance.md §1-§3)
# ---------------------------------------------------------------------------


class GateKind(str, Enum):
    """Per-gate comparison strategy (acceptance.md §2 + [plan-amend]).

    Reflects whether a gate compares against a fixed numeric threshold
    or a baseline multiplied by a configured factor. The earlier hard /
    soft distinction is now expressed structurally — gates land in
    :attr:`AcceptanceReport.hard_gates` vs ``soft_gates``.
    """

    ABSOLUTE = "absolute"
    RELATIVE = "relative"


class GateOperator(str, Enum):
    """Comparison operator for a gate.

    Stored as enum (rather than free-form ``str``) so :class:`GateResult`
    cannot drift to operators ``gate.py`` does not implement.
    """

    EQ = "=="
    GE = ">="
    LE = "<="
    LT = "<"
    GT = ">"


class GateVerdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class GateResult(BaseModel):
    """One row in :attr:`AcceptanceReport.gates`.

    Matches the nested object in procedure.md §3.3 example, extended for
    [plan-amend] (team-lead decision C):

    * ``kind`` reflects the comparison strategy (absolute vs relative).
    * ``passed`` is ``None`` when the gate was skipped (e.g. ``kind=relative``
      without a baseline supplied via ``gate.py --baseline``).
    * ``value`` is optional so a SKIP row can still serialise.
    * ``baseline_value`` / ``computed_threshold`` / ``multiplier`` are
      populated only for ``kind=relative`` gates and surface in
      ``eval_acceptance.json`` for auditability.
    * ``skipped_reason`` carries a short tag (e.g. ``"no baseline"``) so
      consumers can render SKIP rows without re-parsing logs.
    """

    model_config = _FROZEN

    id: str
    kind: GateKind
    value: float | None = None
    threshold: float | None = None
    operator: GateOperator | None = None
    passed: bool | None = Field(default=None, alias="pass")
    multiplier: float | None = None
    baseline_value: float | None = None
    computed_threshold: float | None = None
    skipped_reason: str | None = None


class AcceptanceReport(BaseModel):
    """Top-level shape of ``eval_acceptance_<version>.json``.

    Hard / soft gates are kept as separate tuples (acceptance.md §3) for
    rendering ergonomics; the flat ``gates`` field in procedure.md §3.3 is
    redundant with the union of the two and is reconstructable on demand.
    """

    model_config = _FROZEN

    version: str
    baseline: str | None = None
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    datasets: dict[str, str] = Field(default_factory=dict)
    model_matrix: dict[str, str] = Field(default_factory=dict)
    hard_gates: tuple[GateResult, ...] = ()
    soft_gates: tuple[GateResult, ...] = ()
    verdict: GateVerdict


# ---------------------------------------------------------------------------
# Per-run metadata written by ``run.py`` (consumed by summarize / consistency)
# ---------------------------------------------------------------------------


class RunMeta(BaseModel):
    """Per-run metadata persisted at ``runs/<sample_id>/run_meta.json``.

    ``concurrency`` is required so :mod:`scripts.eval.summarize` can
    auto-flag wall_time/cost as "not authoritative" when N>1 (plan
    decision 3 / P1-7).
    """

    model_config = _FROZEN

    sample_id: str
    run_id: str
    seed: int
    concurrency: Annotated[int, Field(ge=1)]
    cache_disabled: bool = False
    wall_time_seconds: Annotated[float, Field(ge=0.0)]
    cost_usd: Annotated[float, Field(ge=0.0)]
    git_sha: str
    model_matrix: dict[str, str] = Field(default_factory=dict)
    status: Literal["success", "failed"] = "success"
    memory_clean_check: Literal["passed", "skipped"] = "passed"
    exit_code: int = 0


# ---------------------------------------------------------------------------
# Dataset manifest (Tier-1/2/3 lock)
# ---------------------------------------------------------------------------


class ManifestEntry(BaseModel):
    """One sample row inside a ``tier{N}.lock.json`` manifest.

    ``content_sha256`` is the sha256 over the canonical concatenation of the
    sample's ``base.tar / upstream.patch / fork.patch / golden.tar / meta.yaml``
    bytes — see ``scripts/eval/lock.py`` (Phase 1) for the exact algorithm.
    """

    model_config = _FROZEN

    sample_id: str
    tier: int = Field(ge=1, le=3)
    relative_path: str
    content_sha256: str = Field(min_length=64, max_length=64)


class TierManifest(BaseModel):
    """Top-level shape of ``tests/eval/manifests/tier{N}.lock.json``."""

    model_config = _FROZEN

    tier: int = Field(ge=1, le=3)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    eval_version: str
    samples: tuple[ManifestEntry, ...] = ()


# ---------------------------------------------------------------------------
# Acceptance thresholds yaml (plan decision 7)
# ---------------------------------------------------------------------------


ThresholdKind = Literal["absolute", "relative"]


class AcceptanceThresholdEntry(BaseModel):
    """One row in ``acceptance_thresholds.yaml.{hard_gates,soft_gates}``.

    Per [plan-amend] (team-lead decision C) a soft-gate entry may be
    either ``kind="absolute"`` (compares ``value`` against a fixed
    ``threshold``) or ``kind="relative"`` (compares ``value`` against
    ``baseline_value * multiplier``; SKIPped when no baseline is
    supplied to ``gate.py``). Hard gates are always absolute.

    The model_validator enforces:
        kind=absolute  ↔  threshold required, multiplier must be absent
        kind=relative  ↔  multiplier required, threshold must be absent
    """

    model_config = _FROZEN

    id: str
    kind: ThresholdKind = "absolute"
    threshold: float | None = None
    multiplier: float | None = None
    operator: GateOperator | None = None
    source: str

    @field_validator("id")
    @classmethod
    def _id_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("gate id must be a non-empty string")
        return value

    @model_validator(mode="after")
    def _validate_kind_consistency(self) -> AcceptanceThresholdEntry:
        if self.kind == "absolute":
            if self.threshold is None:
                raise ValueError(
                    f"threshold required when kind=absolute (gate id={self.id!r})"
                )
            if self.multiplier is not None:
                raise ValueError(
                    "multiplier must be absent when kind=absolute "
                    f"(gate id={self.id!r})"
                )
        else:  # kind == "relative"
            if self.multiplier is None:
                raise ValueError(
                    f"multiplier required when kind=relative (gate id={self.id!r})"
                )
            if self.threshold is not None:
                raise ValueError(
                    f"threshold must be absent when kind=relative (gate id={self.id!r})"
                )
        return self


class AcceptanceThresholds(BaseModel):
    """Top-level shape of ``tests/eval/manifests/acceptance_thresholds.yaml``.

    ``synced_with_sha`` is the sha256 of ``doc/evaluation/acceptance.md`` at
    the time the yaml was last synced. ``lock.py --verify`` cross-checks this
    field — see plan decision 7 / P1-6.
    """

    model_config = _FROZEN

    synced_with_sha: str = Field(min_length=64, max_length=64)
    synced_at: datetime
    hard_gates: tuple[AcceptanceThresholdEntry, ...] = ()
    soft_gates: tuple[AcceptanceThresholdEntry, ...] = ()


# ---------------------------------------------------------------------------
# Ground-truth bundle (consumed by prepare.py + diff_against_golden.py)
# ---------------------------------------------------------------------------


class SampleMeta(BaseModel):
    """Parsed contents of one sample's ``meta.yaml``.

    Mirrors the keys produced by the Phase 1 reference samples
    (``tests/eval/datasets/.../meta.yaml``). Tier-3 entries additionally
    set ``loss_class`` to one of M1..M6; Tier-1/2 leave it ``None``.
    """

    model_config = _FROZEN

    sample_id: str
    tier: int = Field(ge=1, le=3)
    category: str
    loss_class: str | None = None
    expected_human: bool = False
    description: str | None = None


class GoldenFileEntry(BaseModel):
    """One entry inside a golden tarball.

    Stored as base64 of the file bytes — pydantic v2 round-trips bytes via
    base64 by default, so the bundle remains JSON-serialisable for caching.
    """

    model_config = _FROZEN

    relative_path: str
    content: bytes


class GroundTruthBundle(BaseModel):
    """All inputs needed to score one sample against ground truth."""

    model_config = _FROZEN

    meta: SampleMeta
    golden_files: tuple[GoldenFileEntry, ...]


__all__ = [
    "AcceptanceReport",
    "AcceptanceThresholdEntry",
    "AcceptanceThresholds",
    "DiffEntry",
    "DiffReport",
    "DiffReportMeta",
    "GateKind",
    "GateOperator",
    "GateResult",
    "GateVerdict",
    "GoldenFileEntry",
    "GroundTruthBundle",
    "ManifestEntry",
    "MatchStatus",
    "MismatchLabel",
    "RunMeta",
    "SampleMeta",
    "SystemDecision",
    "ThresholdKind",
    "TierManifest",
]
