"""Tests for O-B Workflow Catalog.

Covers:
* shipped ``config/workflows.yaml`` loads and parses;
* ``review_mode`` maps to the documented threshold pair;
* ``apply_workflow`` is pure (does not mutate input);
* ``overrides`` take precedence over ``review_mode`` when they collide;
* unknown workflow name raises.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.models.config import MergeConfig
from src.models.workflow import (
    REVIEW_MODE_THRESHOLDS,
    WorkflowCatalog,
    WorkflowDefinition,
)
from src.core.workflow_loader import (
    apply_workflow,
    apply_workflow_by_name,
    default_workflows_path,
    load_workflows,
)


def _base_config() -> MergeConfig:
    return MergeConfig(upstream_ref="upstream/main", fork_ref="fork/main")


# ---------- shipped catalog ----------


def test_default_catalog_loads() -> None:
    catalog = load_workflows()
    assert {"standard", "careful", "fast", "analysis-only"}.issubset(catalog.workflows)
    for name, wf in catalog.workflows.items():
        assert wf.name == name
        assert wf.review_mode in REVIEW_MODE_THRESHOLDS


def test_default_catalog_path_exists() -> None:
    assert default_workflows_path().exists()


def test_workflow_name_must_match_key(tmp_path: Path) -> None:
    bad = tmp_path / "workflows.yaml"
    bad.write_text(
        yaml.safe_dump(
            {
                "workflows": {
                    "foo": {"name": "bar", "review_mode": "medium"},
                }
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="key/name mismatch"):
        load_workflows(bad)


def test_missing_catalog_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_workflows(tmp_path / "nope.yaml")


# ---------- review_mode threshold mapping ----------


@pytest.mark.parametrize("mode", ["high", "medium", "low"])
def test_review_mode_maps_to_thresholds(mode: str) -> None:
    base = _base_config()
    wf = WorkflowDefinition(name="t", review_mode=mode)  # type: ignore[arg-type]
    result = apply_workflow(base, wf)
    auto_conf, human_esc = REVIEW_MODE_THRESHOLDS[mode]  # type: ignore[index]
    assert result.thresholds.auto_merge_confidence == auto_conf
    assert result.thresholds.human_escalation == human_esc


def test_review_mode_preserves_other_threshold_fields() -> None:
    base = _base_config()
    original_low = base.thresholds.risk_score_low
    original_high = base.thresholds.risk_score_high
    wf = WorkflowDefinition(name="t", review_mode="high")
    result = apply_workflow(base, wf)
    assert result.thresholds.risk_score_low == original_low
    assert result.thresholds.risk_score_high == original_high


# ---------- purity + override precedence ----------


def test_apply_workflow_does_not_mutate_input() -> None:
    base = _base_config()
    original = base.model_dump()
    wf = WorkflowDefinition(name="t", review_mode="low")
    _ = apply_workflow(base, wf)
    assert base.model_dump() == original


def test_overrides_beat_review_mode_on_collision() -> None:
    base = _base_config()
    wf = WorkflowDefinition(
        name="t",
        review_mode="low",  # would set auto_merge_confidence=0.70
        overrides={"thresholds.auto_merge_confidence": 0.99},
    )
    result = apply_workflow(base, wf)
    assert result.thresholds.auto_merge_confidence == 0.99
    # human_escalation still comes from review_mode because override didn't touch it
    assert result.thresholds.human_escalation == REVIEW_MODE_THRESHOLDS["low"][1]


def test_overrides_apply_to_nested_paths() -> None:
    base = _base_config()
    wf = WorkflowDefinition(
        name="t",
        review_mode="medium",
        overrides={"migration.sync_detection_threshold": 0.25},
    )
    result = apply_workflow(base, wf)
    assert result.migration.sync_detection_threshold == 0.25


# ---------- apply_workflow_by_name ----------


def test_apply_workflow_by_name_resolves_shipped_preset() -> None:
    base = _base_config()
    result = apply_workflow_by_name(base, "careful")
    assert result.thresholds.auto_merge_confidence == REVIEW_MODE_THRESHOLDS["high"][0]


def test_apply_workflow_by_name_unknown_raises() -> None:
    base = _base_config()
    with pytest.raises(KeyError, match="Unknown workflow"):
        apply_workflow_by_name(base, "does-not-exist")


def test_shipped_presets_apply_cleanly() -> None:
    """Every shipped preset must apply without error on a minimal config."""
    base = _base_config()
    catalog = load_workflows()
    for name in catalog.workflows:
        result = apply_workflow_by_name(base, name, catalog)
        assert isinstance(result, MergeConfig)


# ---------- yaml shape sanity ----------


def test_workflow_catalog_top_level_must_be_mapping(tmp_path: Path) -> None:
    bad = tmp_path / "workflows.yaml"
    bad.write_text("- not\n- a\n- mapping\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping at top level"):
        load_workflows(bad)
