"""Workflow catalog loader + applier (O-B).

Reads ``config/workflows.yaml`` and applies a named workflow preset onto a
:class:`~src.models.config.MergeConfig`.  The applier is pure — it returns a
new config object without mutating the input.  All effects are expressed via
existing config fields; no state-machine or phase-level changes are
introduced by this layer.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from src.models.config import MergeConfig
from src.models.workflow import (
    REVIEW_MODE_THRESHOLDS,
    WorkflowCatalog,
    WorkflowDefinition,
)


def default_workflows_path() -> Path:
    """Return the shipped ``config/workflows.yaml`` path."""
    return Path(__file__).resolve().parents[2] / "config" / "workflows.yaml"


def load_workflows(path: Path | None = None) -> WorkflowCatalog:
    """Load workflow definitions from yaml.

    Parameters
    ----------
    path:
        Optional override; defaults to the shipped ``config/workflows.yaml``.

    Raises
    ------
    FileNotFoundError
        If the file is missing.
    ValueError
        If the yaml is not a mapping or contains duplicate names.
    """
    target = path or default_workflows_path()
    if not target.exists():
        raise FileNotFoundError(f"Workflow catalog not found: {target}")
    with target.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Workflow yaml must be a mapping at top level: {target}")
    catalog = WorkflowCatalog.model_validate(data)
    for key, wf in catalog.workflows.items():
        if wf.name != key:
            raise ValueError(
                f"Workflow key/name mismatch: key={key!r}, name={wf.name!r}"
            )
    return catalog


def _set_dotted(obj_dict: dict[str, Any], dotted_path: str, value: Any) -> None:
    """Set a nested dict value addressed by a dotted path.

    Creates intermediate dicts on demand.  Intended for MergeConfig dumps
    (Pydantic ``model_dump()``) which produce nested plain dicts.
    """
    parts = dotted_path.split(".")
    node: dict[str, Any] = obj_dict
    for part in parts[:-1]:
        existing = node.get(part)
        if not isinstance(existing, dict):
            existing = {}
            node[part] = existing
        node = existing
    node[parts[-1]] = value


def apply_workflow(
    config: MergeConfig,
    workflow: WorkflowDefinition,
) -> MergeConfig:
    """Return a new MergeConfig with *workflow* applied on top of *config*.

    Order of application:
      1. ``review_mode`` → rewrites ``thresholds.auto_merge_confidence`` and
         ``thresholds.human_escalation`` to the preset values.
      2. ``overrides`` dict (dotted paths) overwrite any fields, including
         those set by step 1 if the user explicitly names them.

    The input ``config`` is not mutated.  ``dry_run`` is *not* stamped onto
    the config here — the CLI layer is responsible for routing dry-run to
    the phase scheduler, since MergeConfig has no dry_run field today.
    """
    data = config.model_dump(mode="python")

    auto_conf, human_esc = REVIEW_MODE_THRESHOLDS[workflow.review_mode]
    data.setdefault("thresholds", {})
    data["thresholds"]["auto_merge_confidence"] = auto_conf
    data["thresholds"]["human_escalation"] = human_esc

    for dotted, value in workflow.overrides.items():
        _set_dotted(data, dotted, deepcopy(value))

    return MergeConfig.model_validate(data)


def apply_workflow_by_name(
    config: MergeConfig,
    name: str,
    catalog: WorkflowCatalog | None = None,
) -> MergeConfig:
    """Convenience: look up a workflow by name and apply it."""
    cat = catalog or load_workflows()
    if name not in cat.workflows:
        raise KeyError(f"Unknown workflow {name!r}. Available: {sorted(cat.workflows)}")
    return apply_workflow(config, cat.workflows[name])
