"""P1-4: structured-config field-level sensitivity evaluation.

Generic mechanism — no project-specific knowledge baked in. Rules are
supplied via ``FileClassifierConfig.field_sensitivity_rules`` in the
per-repo config.yaml; this module only evaluates them.

Pipeline per file:

    1. Path glob match → otherwise skip (no IO).
    2. Parse base and target content as yaml or json (by extension).
       Failures (binary, malformed, unknown extension) → skip silently.
    3. Flatten both sides into ``{dot.path: value}`` with array indices
       normalised to ``*``.
    4. ``changed = added ∪ removed ∪ value_differs``.
    5. If any sensitive_fields glob matches a changed field → return
       the rule's ``escalate_to`` level.

The module never *demotes* a classification — callers only treat the
returned RiskLevel as a floor.
"""

from __future__ import annotations

import fnmatch
import json
import logging
from typing import Any

from src.models.config import FieldSensitivityRule
from src.models.diff import RiskLevel

logger = logging.getLogger(__name__)


def _is_yaml(file_path: str) -> bool:
    return file_path.endswith((".yaml", ".yml"))


def _is_json(file_path: str) -> bool:
    return file_path.endswith(".json")


def parse_structured(content: str | None, file_path: str) -> Any:
    """Parse content as yaml or json based on the file extension. Returns
    ``None`` for unsupported extensions, parse errors, or
    ``content is None``. Never raises."""
    if content is None:
        return None
    try:
        if _is_yaml(file_path):
            import yaml

            return yaml.safe_load(content)
        if _is_json(file_path):
            return json.loads(content)
    except Exception as exc:
        logger.debug("field_sensitivity: parse failed for %s: %s", file_path, exc)
    return None


def flatten_field_paths(obj: Any, prefix: str = "") -> dict[str, Any]:
    """Walk a nested mapping/sequence and produce ``{dot.path: value}``.

    Strategy:

    * dicts → recurse with ``prefix.key``
    * list of *primitives* → record the entire collection at ``prefix``
      as a sorted tuple, so that adding/removing/reordering scope values
      shows up as a value-diff (a per-element entry under ``prefix.*``
      would let ``setdefault`` pin the first element and silently mask
      additions).
    * list of *objects/lists* → recurse under ``prefix.*`` for each
      child; nested fields like ``endpoints.*.url`` remain reachable.
    * mixed lists fall back to the primitives branch with non-primitive
      members converted to ``repr()`` so the tuple is still hashable
      and order-sensitive.
    """
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, (dict, list)):
                nested = flatten_field_paths(v, key)
                for nk, nv in nested.items():
                    out.setdefault(nk, nv)
            else:
                out.setdefault(key, v)
    elif isinstance(obj, list):
        if all(not isinstance(item, (dict, list)) for item in obj):
            target_key = prefix or "*"
            out.setdefault(
                target_key,
                tuple(sorted((repr(item) for item in obj))),
            )
        else:
            child_prefix = f"{prefix}.*" if prefix else "*"
            for item in obj:
                if isinstance(item, (dict, list)):
                    nested = flatten_field_paths(item, child_prefix)
                    for nk, nv in nested.items():
                        out.setdefault(nk, nv)
                else:
                    out.setdefault(child_prefix, item)
    else:
        if prefix:
            out.setdefault(prefix, obj)
    return out


def compute_changed_fields(base: Any, target: Any) -> set[str]:
    """Return the set of dot-path fields whose value differs between
    base and target (added / removed / changed)."""
    base_flat = flatten_field_paths(base) if base is not None else {}
    target_flat = flatten_field_paths(target) if target is not None else {}
    keys = set(base_flat) | set(target_flat)
    return {k for k in keys if base_flat.get(k) != target_flat.get(k)}


def field_path_matches(field_path: str, glob_pattern: str) -> bool:
    """fnmatch-style match on dot-paths. Treats ``.`` as a literal
    separator — ``oauth.scopes`` matches ``oauth.scopes`` exactly,
    not ``oauthXscopes``."""
    return fnmatch.fnmatchcase(field_path, glob_pattern)


def evaluate(
    file_path: str,
    base_content: str | None,
    target_content: str | None,
    rules: list[FieldSensitivityRule],
) -> RiskLevel | None:
    """Return the strictest ``escalate_to`` level demanded by any rule
    whose path_glob matches ``file_path`` AND whose sensitive_fields
    intersect the changed-field set. Returns ``None`` when no rule
    fires."""
    if not rules:
        return None

    matching = [r for r in rules if fnmatch.fnmatchcase(file_path, r.path_glob)]
    if not matching:
        return None

    base = parse_structured(base_content, file_path)
    target = parse_structured(target_content, file_path)
    if base is None and target is None:
        return None
    changed = compute_changed_fields(base, target)
    if not changed:
        return None

    fired_levels: list[RiskLevel] = []
    for rule in matching:
        for field_glob in rule.sensitive_fields:
            if any(field_path_matches(c, field_glob) for c in changed):
                fired_levels.append(RiskLevel(rule.escalate_to))
                break

    if not fired_levels:
        return None

    return max(fired_levels, key=lambda lv: lv.severity() or 0)


def is_at_least(level: RiskLevel | None, floor: RiskLevel) -> bool:
    """Helper: ``level`` reaches the severity floor."""
    if level is None:
        return False
    return (level.severity() or 0) >= (floor.severity() or 0)
