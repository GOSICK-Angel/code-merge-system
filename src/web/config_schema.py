"""Introspect ``MergeConfig`` into a normalized UI schema tree.

Pure, deterministic, no I/O. The Web UI Setup view renders this tree to
expose *every* ``MergeConfig`` field without hand-wiring each one — a new
config field appears in the UI automatically the moment it is added to
the model, which is the whole point of the schema-driven editor (it
removes the per-field plumbing that historically left new options like
``dependency_graph`` invisible in the form).

Classification of each field's annotation:

- ``bool`` / ``int`` / ``float`` / ``str``      → primitive controls
- ``Literal[...]`` / ``Enum`` subclass          → ``enum`` select
- ``list[str]`` / ``list[Literal[str]]``        → ``list_str`` tag editor
- nested ``BaseModel``                          → ``object`` (recursed)
- everything else (``list[BaseModel]``, ``dict``,
  ``tuple``, multi-type unions)                 → ``yaml`` inline editor

Cycle guard: a ``BaseModel`` already on the ancestry path (e.g.
``AgentLLMConfig.fallback`` → ``AgentLLMConfig``) degrades to ``yaml``
instead of recursing forever.
"""

from __future__ import annotations

import types
from enum import Enum
from functools import lru_cache
from typing import Any, Literal, Union, get_args, get_origin

import annotated_types as at
from pydantic import BaseModel, Field
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

from src.models.config import MergeConfig

FieldKind = Literal["bool", "int", "float", "str", "enum", "list_str", "object", "yaml"]

_UNION_TYPE = getattr(types, "UnionType", None)


# Dotted paths owned by the curated first-screen Setup form (merge target,
# providers/agents, core thresholds). The comprehensive schema editor
# excludes these so a value never has two sources of truth. A node is
# curated when its path — or any ancestor path — is listed here.
CURATED_PATHS: frozenset[str] = frozenset(
    {
        "upstream_ref",
        "fork_ref",
        "project_context",
        "agents",
        "github",
        # Legacy global ``llm`` block — never read at run time (the real
        # per-agent config lives under ``agents``). Hidden from the editor
        # so it can't be mistaken for the model config; provider/model live
        # in AGENT OVERRIDES, the circuit-breaker fallback in CROSS-PROVIDER
        # FALLBACK, and per-model tuning in the MODEL PARAMETERS card.
        "llm",
        "thresholds.auto_merge_confidence",
        "thresholds.risk_score_low",
        "thresholds.risk_score_high",
        "llm_assist.mode",
    }
)


class ConfigFieldNode(BaseModel):
    """One node in the UI schema tree. ``object`` nodes carry ``children``;
    every other kind is a leaf with a concrete ``default``."""

    name: str
    path: str
    kind: FieldKind
    default: Any = None
    description: str | None = None
    required: bool = False
    curated: bool = False
    enum: list[str] | None = None
    minimum: float | None = None
    maximum: float | None = None
    children: list[ConfigFieldNode] = Field(default_factory=list)


ConfigFieldNode.model_rebuild()


def _is_union(origin: Any) -> bool:
    return origin is Union or (_UNION_TYPE is not None and origin is _UNION_TYPE)


def _strip_optional(annotation: Any) -> Any:
    """Unwrap ``X | None`` / ``Optional[X]`` to ``X``; leave multi-type
    unions untouched so they fall through to the ``yaml`` classification."""
    if _is_union(get_origin(annotation)):
        non_none = [a for a in get_args(annotation) if a is not type(None)]
        if len(non_none) == 1:
            return non_none[0]
    return annotation


def _is_basemodel(tp: Any) -> bool:
    return isinstance(tp, type) and issubclass(tp, BaseModel)


def _classify(annotation: Any) -> tuple[FieldKind, list[str] | None, Any]:
    """Return ``(kind, enum_values, child_model)``; ``child_model`` is set
    only for ``object`` so the caller knows what to recurse into."""
    ann = _strip_optional(annotation)
    origin = get_origin(ann)

    if origin is Literal:
        return "enum", [str(v) for v in get_args(ann)], None
    if isinstance(ann, type) and issubclass(ann, Enum):
        return "enum", [str(member.value) for member in ann], None
    # bool before int — bool is a subclass of int.
    if ann is bool:
        return "bool", None, None
    if ann is int:
        return "int", None, None
    if ann is float:
        return "float", None, None
    if ann is str:
        return "str", None, None
    if _is_basemodel(ann):
        return "object", None, ann
    if origin in (list, tuple):
        args = get_args(ann)
        item = _strip_optional(args[0]) if args else str
        if item is str or get_origin(item) is Literal:
            return "list_str", None, None
        return "yaml", None, None
    return "yaml", None, None


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bounds(field: FieldInfo) -> tuple[float | None, float | None]:
    """Extract numeric ``ge/gt`` → minimum and ``le/lt`` → maximum from the
    field's annotated-types metadata."""
    minimum: float | None = None
    maximum: float | None = None
    for meta in field.metadata:
        if isinstance(meta, at.Ge):
            minimum = _as_float(meta.ge)
        elif isinstance(meta, at.Gt):
            minimum = _as_float(meta.gt)
        elif isinstance(meta, at.Le):
            maximum = _as_float(meta.le)
        elif isinstance(meta, at.Lt):
            maximum = _as_float(meta.lt)
    return minimum, maximum


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _to_jsonable(val) for key, val in value.items()}
    if isinstance(value, Enum):
        return value.value
    return value


def _default_for(field: FieldInfo) -> Any:
    if field.is_required():
        return None
    value = field.get_default(call_default_factory=True)
    if value is PydanticUndefined:
        return None
    return _to_jsonable(value)


def _is_curated(path: str) -> bool:
    parts = path.split(".")
    return any(
        ".".join(parts[:depth]) in CURATED_PATHS for depth in range(1, len(parts) + 1)
    )


def _build_node(
    name: str,
    field: FieldInfo,
    parent_path: str,
    seen: frozenset[type],
) -> ConfigFieldNode:
    path = f"{parent_path}.{name}" if parent_path else name
    kind, enum_values, child_model = _classify(field.annotation)
    minimum, maximum = _bounds(field)

    children: list[ConfigFieldNode] = []
    if kind == "object" and child_model is not None:
        if child_model in seen:
            # Self-referential model (e.g. AgentLLMConfig.fallback) — stop
            # recursing and let the user edit it as a yaml blob.
            kind = "yaml"
        else:
            children = _build_children(child_model, path, seen | {child_model})

    return ConfigFieldNode(
        name=name,
        path=path,
        kind=kind,
        default=None if kind == "object" else _default_for(field),
        description=field.description,
        required=field.is_required(),
        curated=_is_curated(path),
        enum=enum_values,
        minimum=minimum,
        maximum=maximum,
        children=children,
    )


def _build_children(
    model: type[BaseModel], parent_path: str, seen: frozenset[type]
) -> list[ConfigFieldNode]:
    return [
        _build_node(field_name, field_info, parent_path, seen)
        for field_name, field_info in model.model_fields.items()
    ]


@lru_cache(maxsize=1)
def build_config_schema() -> ConfigFieldNode:
    """Return the root ``object`` node whose children are every top-level
    ``MergeConfig`` field. Cached because the structure is static — callers
    must treat the result as read-only (they ``model_dump()`` it)."""
    return ConfigFieldNode(
        name="",
        path="",
        kind="object",
        children=_build_children(MergeConfig, "", frozenset({MergeConfig})),
    )
