"""Tests for ``scripts.eval._report_render`` — Verifier T5-R1..T5-R2."""

from __future__ import annotations

from typing import Any

import pytest
from jinja2 import UndefinedError

from scripts.eval._report_render import render_report


def _minimal_context(**overrides: Any) -> dict[str, Any]:
    """Build a complete context dict so the strict-undefined template renders."""
    ctx: dict[str, Any] = {
        "tier": 1,
        "evaluated_at": "2026-05-15T00:00:00+00:00",
        "git_sha": "deadbeef",
        "model_matrix_repr": "<empty>",
        "samples_total": 1,
        "samples_failed": 0,
        "dataset_lock_sha": "abc123",
        "semantic_engine": "fallback-bytes",
        "max_concurrency": 1,
        "not_authoritative": False,
        "metrics": {
            "OA": "1.0000",
            "WMR": "0.0000",
            "MMR": "0.0000",
            "WDR": "0.0000",
            "SSER": "1.0000",
            "DCRR": "1.0000",
            "SRSR": "N/A",
            "RR": "1.0000",
            "RCR": "1.0000",
            "Recall": {
                "M1": "N/A",
                "M2": "N/A",
                "M3": "N/A",
                "M4": "N/A",
                "M5": "N/A",
                "M6": "N/A",
            },
            "CRA": "1.0000",
            "OverEscalationRate": "0.0000",
            "JA": "N/A",
            "DET": "N/A",
            "CPC": "N/A",
            "BCP": "N/A",
            "cost_usd_per_run_p95": "0.0",
            "wall_time_seconds_p95": "0.0",
            "plan_revision_rounds_p95": "N/A",
        },
        "samples": [
            {
                "sample_id": "t1-0001",
                "category": "C",
                "loss_class": None,
                "match": "EXACT",
                "label": None,
                "missed_lines": 0,
                "extra_lines": 0,
            }
        ],
        "failures": [],
        "baseline": None,
        "baseline_diff": [],
        "known_issues": [],
    }
    ctx.update(overrides)
    return ctx


# ---------------------------------------------------------------------------
# T5-R1 — six section anchors
# ---------------------------------------------------------------------------


class TestSixSections:
    def test_template_emits_all_six_section_headers(self) -> None:
        md = render_report(_minimal_context())
        for n in range(1, 7):
            assert f"## {n}. " in md

    def test_first_section_anchors_meta_keys(self) -> None:
        md = render_report(_minimal_context())
        assert "git_sha" in md
        assert "evaluated_at" in md
        assert "dataset_lock_sha" in md


# ---------------------------------------------------------------------------
# T5-R2 — strict undefined raises on missing key
# ---------------------------------------------------------------------------


class TestStrictUndefined:
    def test_missing_metrics_oa_raises(self) -> None:
        ctx = _minimal_context()
        del ctx["metrics"]["OA"]
        with pytest.raises(UndefinedError):
            render_report(ctx)

    def test_missing_top_level_field_raises(self) -> None:
        ctx = _minimal_context()
        del ctx["evaluated_at"]
        with pytest.raises(UndefinedError):
            render_report(ctx)


# ---------------------------------------------------------------------------
# Concurrency banner
# ---------------------------------------------------------------------------


class TestConcurrencyBanner:
    def test_banner_omitted_when_serial(self) -> None:
        md = render_report(_minimal_context())
        assert "wall_time/cost not authoritative" not in md

    def test_banner_inserted_when_not_authoritative(self) -> None:
        ctx = _minimal_context(not_authoritative=True, max_concurrency=4)
        md = render_report(ctx)
        assert "wall_time/cost not authoritative" in md
        assert "max=4" in md


# ---------------------------------------------------------------------------
# Failure list rendering
# ---------------------------------------------------------------------------


class TestFailureList:
    def test_empty_failures_show_placeholder(self) -> None:
        md = render_report(_minimal_context())
        assert "No failed samples" in md

    def test_failures_listed_in_provided_order(self) -> None:
        ctx = _minimal_context(
            failures=[
                {
                    "sample_id": "a",
                    "label": "WRONG_MERGE",
                    "strategy": "SEMANTIC_MERGE",
                    "rationale_excerpt": "...",
                },
                {
                    "sample_id": "b",
                    "label": "MISS_UPSTREAM",
                    "strategy": "TAKE_TARGET",
                    "rationale_excerpt": "...",
                },
            ]
        )
        md = render_report(ctx)
        assert md.index("| a |") < md.index("| b |")


# ---------------------------------------------------------------------------
# Recall expansion + baseline / known_issues sections
# ---------------------------------------------------------------------------


class TestExpandedSections:
    def test_recall_m1_through_m6_anchors_present(self) -> None:
        md = render_report(_minimal_context())
        for label in ("M1", "M2", "M3", "M4", "M5", "M6"):
            assert f"Recall_{label}" in md

    def test_baseline_placeholder_when_absent(self) -> None:
        md = render_report(_minimal_context())
        assert "No baseline supplied" in md

    def test_baseline_diff_rendered_when_present(self) -> None:
        ctx = _minimal_context(
            baseline="something",
            baseline_diff=[
                {"id": "OA", "current": "0.9", "baseline": "0.8", "delta": "+0.1"}
            ],
        )
        md = render_report(ctx)
        assert "| OA | 0.9 | 0.8 | +0.1 |" in md

    def test_known_issues_listed(self) -> None:
        ctx = _minimal_context(known_issues=["x.py rationale too short"])
        md = render_report(ctx)
        assert "x.py rationale too short" in md
