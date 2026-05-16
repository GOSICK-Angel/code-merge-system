"""Tests for ``scripts.eval._schemas`` (pydantic v2 models)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from scripts.eval._schemas import (
    AcceptanceReport,
    AcceptanceThresholdEntry,
    AcceptanceThresholds,
    DiffEntry,
    DiffReport,
    DiffReportMeta,
    GateKind,
    GateOperator,
    GateResult,
    GateVerdict,
    ManifestEntry,
    MatchStatus,
    MismatchLabel,
    RunMeta,
    SystemDecision,
    TierManifest,
)

SHA256_HEX = "0" * 64


def _system_decision() -> SystemDecision:
    return SystemDecision(strategy="SEMANTIC_MERGE", risk="AUTO_RISKY", human=False)


class TestSystemDecision:
    def test_valid(self) -> None:
        decision = _system_decision()
        assert decision.strategy == "SEMANTIC_MERGE"
        assert decision.human is False

    def test_frozen(self) -> None:
        decision = _system_decision()
        with pytest.raises(ValidationError):
            decision.strategy = "TAKE_TARGET"


class TestDiffEntry:
    def test_minimal_valid_payload(self) -> None:
        entry = DiffEntry(
            sample_id="t1-0001",
            category="C",
            expected_human=False,
            system_decision=_system_decision(),
            match=MatchStatus.MISMATCH,
            label=MismatchLabel.WRONG_MERGE,
            missed_lines=12,
            extra_lines=3,
        )
        assert entry.sample_id == "t1-0001"
        assert entry.label is MismatchLabel.WRONG_MERGE
        assert entry.rationale_length == 0  # default
        assert entry.discarded_content_present is False
        assert entry.is_security_sensitive is False

    def test_negative_line_counts_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DiffEntry(
                sample_id="t1-0002",
                category="C",
                expected_human=False,
                system_decision=_system_decision(),
                match=MatchStatus.EXACT,
                missed_lines=-1,
            )

    def test_invalid_match_value_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DiffEntry(
                sample_id="t1-0003",
                category="C",
                expected_human=False,
                system_decision=_system_decision(),
                match="WHATEVER",  # type: ignore[arg-type]
            )

    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DiffEntry(
                sample_id="t1-0004",
                category="C",
                expected_human=False,
                system_decision=_system_decision(),
                match=MatchStatus.EXACT,
                extra_unknown_field="whatever",  # type: ignore[call-arg]
            )


class TestDiffReport:
    def test_default_meta_timestamp_is_utc(self) -> None:
        meta = DiffReportMeta(semantic_engine="fallback-bytes")
        assert meta.generated_at.tzinfo is not None
        assert meta.generated_at.utcoffset() == timezone.utc.utcoffset(None)

    def test_tier_must_be_in_range(self) -> None:
        with pytest.raises(ValidationError):
            DiffReport(
                tier=4,
                samples=(),
                meta=DiffReportMeta(semantic_engine="fallback-bytes"),
            )

    def test_round_trip_preserves_samples(self) -> None:
        entry = DiffEntry(
            sample_id="t1-0001",
            category="C",
            expected_human=False,
            system_decision=_system_decision(),
            match=MatchStatus.EXACT,
        )
        report = DiffReport(
            tier=1,
            samples=(entry,),
            meta=DiffReportMeta(semantic_engine="tree-sitter"),
        )
        dumped = report.model_dump_json()
        restored = DiffReport.model_validate_json(dumped)
        assert restored.samples[0].sample_id == "t1-0001"
        assert restored.meta.semantic_engine == "tree-sitter"


class TestGateResult:
    def test_alias_pass_field(self) -> None:
        gate = GateResult(
            id="WMR",
            kind=GateKind.ABSOLUTE,
            value=0.0,
            threshold=0.0,
            operator=GateOperator.EQ,
            **{"pass": True},  # type: ignore[arg-type]
        )
        assert gate.passed is True
        dumped = gate.model_dump(by_alias=True)
        assert dumped["pass"] is True
        assert "passed" not in dumped

    def test_invalid_kind_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GateResult(
                id="WMR",
                kind="medium",  # type: ignore[arg-type]
                value=0.0,
                threshold=0.0,
                operator=GateOperator.EQ,
                **{"pass": True},  # type: ignore[arg-type]
            )


class TestAcceptanceReport:
    def test_minimal_valid(self) -> None:
        report = AcceptanceReport(
            version="v0.7.1",
            verdict=GateVerdict.PASS,
        )
        assert report.verdict is GateVerdict.PASS
        assert report.hard_gates == ()
        assert report.soft_gates == ()

    def test_invalid_verdict_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AcceptanceReport(
                version="v0.7.1",
                verdict="MAYBE",  # type: ignore[arg-type]
            )


class TestRunMeta:
    def test_valid(self) -> None:
        meta = RunMeta(
            sample_id="t1-0001",
            run_id="abc123",
            seed=42,
            concurrency=1,
            wall_time_seconds=12.5,
            cost_usd=0.01,
            git_sha="deadbeef",
        )
        assert meta.concurrency == 1
        assert meta.cache_disabled is False

    def test_concurrency_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            RunMeta(
                sample_id="t1-0001",
                run_id="abc123",
                seed=42,
                concurrency=0,
                wall_time_seconds=1.0,
                cost_usd=0.0,
                git_sha="deadbeef",
            )

    def test_negative_cost_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RunMeta(
                sample_id="t1-0001",
                run_id="abc123",
                seed=42,
                concurrency=1,
                wall_time_seconds=1.0,
                cost_usd=-0.01,
                git_sha="deadbeef",
            )


class TestManifest:
    def test_manifest_entry_requires_64_char_hash(self) -> None:
        with pytest.raises(ValidationError):
            ManifestEntry(
                sample_id="t1-0001",
                tier=1,
                relative_path="tier1/samples/t1-0001",
                content_sha256="too-short",
            )

    def test_tier_manifest_round_trip(self) -> None:
        entry = ManifestEntry(
            sample_id="t1-0001",
            tier=1,
            relative_path="tier1/samples/t1-0001",
            content_sha256=SHA256_HEX,
        )
        manifest = TierManifest(
            tier=1,
            eval_version="0.1.0",
            samples=(entry,),
        )
        restored = TierManifest.model_validate_json(manifest.model_dump_json())
        assert restored.samples[0].sample_id == "t1-0001"
        assert restored.eval_version == "0.1.0"


class TestAcceptanceThresholds:
    def test_threshold_entry_validates_id(self) -> None:
        with pytest.raises(ValidationError):
            AcceptanceThresholdEntry(
                id=" ",
                threshold=0.0,
                operator=GateOperator.EQ,
                source="Tier-1",
            )

    def test_thresholds_round_trip(self) -> None:
        wmr = AcceptanceThresholdEntry(
            id="WMR",
            threshold=0.0,
            operator=GateOperator.EQ,
            source="Tier-1 + Tier-2 + Tier-3",
        )
        oa = AcceptanceThresholdEntry(
            id="OA",
            threshold=0.92,
            operator=GateOperator.GE,
            source="Tier-1",
        )
        thresholds = AcceptanceThresholds(
            synced_with_sha=SHA256_HEX,
            synced_at=datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc),
            hard_gates=(wmr,),
            soft_gates=(oa,),
        )
        restored = AcceptanceThresholds.model_validate_json(
            thresholds.model_dump_json()
        )
        assert restored.hard_gates[0].operator is GateOperator.EQ
        assert restored.soft_gates[0].id == "OA"

    def test_short_synced_with_sha_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AcceptanceThresholds(
                synced_with_sha="abc123",
                synced_at=datetime.now(timezone.utc),
            )

    def test_t0_s4_relative_soft_gate_accepts_multiplier(self) -> None:
        cost = AcceptanceThresholdEntry(
            id="cost_usd_per_run_p95",
            kind="relative",
            multiplier=1.15,
            source="full",
        )
        assert cost.kind == "relative"
        assert cost.multiplier == 1.15
        assert cost.threshold is None

    def test_t0_s4_absolute_soft_gate_accepts_threshold(self) -> None:
        oa = AcceptanceThresholdEntry(
            id="OA",
            kind="absolute",
            threshold=0.92,
            operator=GateOperator.GE,
            source="Tier-1",
        )
        assert oa.kind == "absolute"
        assert oa.threshold == 0.92
        assert oa.multiplier is None

    def test_t0_s4b_kind_must_be_absolute_or_relative(self) -> None:
        with pytest.raises(ValidationError):
            AcceptanceThresholdEntry(
                id="OA",
                kind="weird",  # type: ignore[arg-type]
                threshold=0.92,
                source="Tier-1",
            )

    def test_t0_s4c_relative_kind_requires_multiplier(self) -> None:
        with pytest.raises(ValidationError) as exc:
            AcceptanceThresholdEntry(
                id="cost_usd_per_run_p95",
                kind="relative",
                source="full",
            )
        assert "multiplier required when kind=relative" in str(exc.value)

    def test_absolute_kind_rejects_extra_multiplier(self) -> None:
        with pytest.raises(ValidationError) as exc:
            AcceptanceThresholdEntry(
                id="OA",
                kind="absolute",
                threshold=0.92,
                multiplier=1.15,
                source="Tier-1",
            )
        assert "multiplier must be absent when kind=absolute" in str(exc.value)

    def test_relative_kind_rejects_extra_threshold(self) -> None:
        with pytest.raises(ValidationError) as exc:
            AcceptanceThresholdEntry(
                id="cost_usd_per_run_p95",
                kind="relative",
                multiplier=1.15,
                threshold=0.05,
                source="full",
            )
        assert "threshold must be absent when kind=relative" in str(exc.value)

    def test_absolute_kind_requires_threshold(self) -> None:
        with pytest.raises(ValidationError) as exc:
            AcceptanceThresholdEntry(
                id="OA",
                kind="absolute",
                source="Tier-1",
            )
        assert "threshold required when kind=absolute" in str(exc.value)
