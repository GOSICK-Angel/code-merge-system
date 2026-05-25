"""P1-1: InterfaceChangeExtractor — detect upstream interface changes.

Language-agnostic regex fallback (tree-sitter planned as future upgrade):
- constructor_signature: ``__init__`` / ``constructor`` parameter list diff
- method_signature: top-level ``def foo(...)`` / ``function foo(...)`` /
  Go ``func foo(...)`` and ``func (recv) foo(...)`` parameter-list diff
- base_class: ``class Foo(Base)`` change
- enum_value: ``KEY = "value"`` diff inside class/module
- export_removed: identifier removed from ``__all__`` or ``export``
- module_path_moved: filename renamed across refs (caller-supplied)

The extractor takes base-content vs upstream-content for a single file and
returns a list of ``InterfaceChange``. All detection is conservative — when
parsing fails we return no change rather than false positives.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel


InterfaceChangeKind = Literal[
    "constructor_signature",
    "method_signature",
    "base_class",
    "enum_value",
    "export_removed",
    "module_path_moved",
    "type_narrowed",
]


class InterfaceChange(BaseModel):
    file_path: str
    symbol: str
    change_kind: InterfaceChangeKind
    before: str = ""
    after: str = ""

    model_config = {"frozen": True}


_METHOD_RE = re.compile(
    r"^\s*(?:async\s+)?(?:def|function)\s+(?P<name>\w+)\s*\((?P<params>[^)]*)\)",
    re.MULTILINE,
)
_GO_METHOD_RE = re.compile(
    r"^\s*func\s+\([^)]*\)\s+(?P<name>\w+)\s*\((?P<params>[^)]*)\)",
    re.MULTILINE,
)
_GO_FUNC_RE = re.compile(
    r"^\s*func\s+(?P<name>\w+)\s*\((?P<params>[^)]*)\)",
    re.MULTILINE,
)
_CLASS_RE = re.compile(
    r"^\s*class\s+(?P<name>\w+)\s*(?:\((?P<bases>[^)]*)\))?\s*[:{]?",
    re.MULTILINE,
)
_ENUM_ASSIGN_RE = re.compile(
    r"^\s*(?P<name>[A-Z][A-Z0-9_]*)\s*=\s*(?P<value>.+?)\s*$",
    re.MULTILINE,
)
_ALL_RE = re.compile(
    r"__all__\s*=\s*\[(?P<body>[^\]]*)\]",
    re.MULTILINE | re.DOTALL,
)


def _normalize_params(raw: str) -> str:
    return re.sub(r"\s+", " ", raw).strip()


def _extract_methods(content: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for regex in (_METHOD_RE, _GO_METHOD_RE, _GO_FUNC_RE):
        for m in regex.finditer(content):
            name = m.group("name")
            params = _normalize_params(m.group("params"))
            out.setdefault(name, params)
    return out


def _extract_classes(content: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in _CLASS_RE.finditer(content):
        name = m.group("name")
        bases = _normalize_params(m.group("bases") or "")
        out[name] = bases
    return out


def _extract_enum_values(content: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in _ENUM_ASSIGN_RE.finditer(content):
        name = m.group("name")
        value = m.group("value").strip().rstrip(",")
        if name and len(name) > 1:
            out[name] = value
    return out


def _extract_exports(content: str) -> set[str]:
    result: set[str] = set()
    for m in _ALL_RE.finditer(content):
        body = m.group("body")
        for token in re.findall(r"['\"](\w+)['\"]", body):
            result.add(token)
    return result


class InterfaceChangeExtractor:
    """Extract upstream interface changes per file.

    Usage::

        extractor = InterfaceChangeExtractor()
        changes = extractor.extract(
            file_path="api/foo.py",
            base_content=base,
            upstream_content=upstream,
        )
    """

    def extract(
        self,
        file_path: str,
        base_content: str | None,
        upstream_content: str | None,
    ) -> list[InterfaceChange]:
        base = base_content or ""
        upstream = upstream_content or ""
        if base == upstream:
            return []

        changes: list[InterfaceChange] = []

        base_methods = _extract_methods(base)
        upstream_methods = _extract_methods(upstream)
        for name, up_params in upstream_methods.items():
            base_params = base_methods.get(name)
            if base_params is None:
                continue
            if base_params == up_params:
                continue
            kind: InterfaceChangeKind = (
                "constructor_signature"
                if name in ("__init__", "constructor")
                else "method_signature"
            )
            changes.append(
                InterfaceChange(
                    file_path=file_path,
                    symbol=name,
                    change_kind=kind,
                    before=base_params,
                    after=up_params,
                )
            )

        base_classes = _extract_classes(base)
        upstream_classes = _extract_classes(upstream)
        for name, up_bases in upstream_classes.items():
            base_bases = base_classes.get(name)
            if base_bases is None or base_bases == up_bases:
                continue
            changes.append(
                InterfaceChange(
                    file_path=file_path,
                    symbol=name,
                    change_kind="base_class",
                    before=base_bases,
                    after=up_bases,
                )
            )

        base_enums = _extract_enum_values(base)
        upstream_enums = _extract_enum_values(upstream)
        all_enum_keys = set(base_enums) | set(upstream_enums)
        for key in all_enum_keys:
            before = base_enums.get(key, "")
            after = upstream_enums.get(key, "")
            if before == after:
                continue
            changes.append(
                InterfaceChange(
                    file_path=file_path,
                    symbol=key,
                    change_kind="enum_value",
                    before=before,
                    after=after,
                )
            )

        base_exports = _extract_exports(base)
        upstream_exports = _extract_exports(upstream)
        removed = base_exports - upstream_exports
        for name in sorted(removed):
            changes.append(
                InterfaceChange(
                    file_path=file_path,
                    symbol=name,
                    change_kind="export_removed",
                    before=name,
                    after="",
                )
            )

        return changes

    def extract_from_paths(
        self,
        pairs: list[tuple[str, str | None, str | None]],
    ) -> list[InterfaceChange]:
        """Batch extraction. Input: list of (file_path, base, upstream)."""
        out: list[InterfaceChange] = []
        for file_path, base, upstream in pairs:
            out.extend(self.extract(file_path, base, upstream))
        return out
