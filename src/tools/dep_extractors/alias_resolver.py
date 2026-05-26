"""Resolve aliased / bare imports into repo-relative file paths.

Phase C §6.3 (opt-in, ``dependency_graph.resolve_aliases``): parse the three
common ecosystems that remap import specifiers — TypeScript/JS ``tsconfig``
``paths``+``baseUrl``, Go ``go.mod`` ``module`` prefix, and monorepo
``package.json`` package names — into an :class:`AliasMap`. The tree-sitter
extractor consults it before falling back to relative resolution, turning
``@app/foo`` / ``github.com/org/repo/pkg`` / ``@scope/pkg`` imports into edges.

Everything here is best-effort and defensive: a malformed config file is
skipped, never fatal. Target-repo agnostic — no ecosystem is assumed present;
an empty config set yields an empty map and changes nothing.
"""

from __future__ import annotations

import json
import posixpath
import re
from dataclasses import dataclass, field
from typing import Any

_JS_EXTS: tuple[str, ...] = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")

# tsconfig allows ``//`` and ``/* */`` comments + trailing commas (JSONC).
_LINE_COMMENT = re.compile(r"//[^\n\r]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")


def _loads_jsonc(text: str) -> dict[str, Any] | None:
    """Parse JSON tolerating JSONC comments / trailing commas. Returns None on
    failure (caller skips the file)."""
    for candidate in (text, None):
        if candidate is None:
            stripped = _BLOCK_COMMENT.sub("", text)
            stripped = _LINE_COMMENT.sub("", stripped)
            stripped = _TRAILING_COMMA.sub(r"\1", stripped)
            candidate = stripped
        try:
            result = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        return result if isinstance(result, dict) else None
    return None


@dataclass(frozen=True)
class AliasMap:
    # (alias_prefix, target_prefixes) — prefixes are repo-relative, the ``*``
    # already stripped (e.g. "@app/" -> ("src/app/",)).
    ts_paths: tuple[tuple[str, tuple[str, ...]], ...] = ()
    ts_base_url: str = ""  # repo-relative dir for bare non-aliased imports
    go_module: str = ""  # go.mod module prefix
    pkg_names: dict[str, str] = field(default_factory=dict)  # pkg name -> dir

    @property
    def is_empty(self) -> bool:
        return not (
            self.ts_paths or self.ts_base_url or self.go_module or self.pkg_names
        )

    def resolve_js(self, spec: str, path_set: set[str]) -> str | None:
        """Resolve a bare (non-relative) JS/TS specifier via tsconfig paths,
        workspace package names, then baseUrl. Returns None if unresolved."""
        # 1. tsconfig paths aliases.
        for prefix, targets in self.ts_paths:
            rest = _match_prefix(spec, prefix)
            if rest is None:
                continue
            for target_prefix in targets:
                cand = posixpath.normpath(posixpath.join(target_prefix, rest))
                hit = _resolve_with_exts(cand, path_set)
                if hit:
                    return hit
        # 2. workspace package names (exact or ``name/sub``).
        for name, root in self.pkg_names.items():
            rest = _match_prefix(spec, name)
            if rest is None:
                continue
            cand = posixpath.normpath(posixpath.join(root, rest)) if rest else root
            hit = _resolve_with_exts(cand, path_set)
            if hit:
                return hit
        # 3. baseUrl join (bare spec resolved relative to baseUrl).
        if self.ts_base_url:
            cand = posixpath.normpath(posixpath.join(self.ts_base_url, spec))
            hit = _resolve_with_exts(cand, path_set)
            if hit:
                return hit
        return None

    def resolve_go(self, spec: str, path_set: set[str]) -> str | None:
        """Strip the go.mod module prefix and match the remaining package dir."""
        if not self.go_module:
            return None
        rest = _match_prefix(spec, self.go_module)
        if not rest:
            return None
        for fp in path_set:
            norm = fp.replace("\\", "/")
            if norm.endswith(".go") and posixpath.dirname(norm) == rest.strip("/"):
                return fp
        return None


def _match_prefix(spec: str, prefix: str) -> str | None:
    """If ``spec`` equals ``prefix`` (sans trailing slash) or starts with
    ``prefix/``, return the remainder ("" for exact match); else None."""
    p = prefix.rstrip("/")
    if not p:
        return None
    if spec == p:
        return ""
    if spec.startswith(p + "/"):
        return spec[len(p) + 1 :]
    return None


def _resolve_with_exts(cand: str, path_set: set[str]) -> str | None:
    if cand in path_set:
        return cand
    for ext in _JS_EXTS:
        if cand + ext in path_set:
            return cand + ext
    for ext in _JS_EXTS:
        idx = posixpath.join(cand, "index" + ext)
        if idx in path_set:
            return idx
    return None


def build_alias_map(configs: dict[str, str]) -> AliasMap:
    """Build an :class:`AliasMap` from config files (path -> content).

    Recognises ``tsconfig*.json`` / ``jsconfig*.json`` (paths + baseUrl),
    ``go.mod`` (module), and any ``package.json`` carrying a ``name``
    (workspace package). Unknown / malformed files are ignored."""
    ts_paths: list[tuple[str, tuple[str, ...]]] = []
    ts_base_url = ""
    go_module = ""
    pkg_names: dict[str, str] = {}

    for path, content in configs.items():
        base = posixpath.basename(path.replace("\\", "/"))
        cfg_dir = posixpath.dirname(path.replace("\\", "/"))
        if base == "go.mod":
            mod = _parse_go_module(content)
            if mod and not go_module:
                go_module = mod
        elif base == "package.json":
            data = _loads_jsonc(content)
            name = data.get("name") if isinstance(data, dict) else None
            if isinstance(name, str) and name:
                pkg_names.setdefault(name, cfg_dir)
        elif base.startswith("tsconfig") or base.startswith("jsconfig"):
            data = _loads_jsonc(content)
            if not isinstance(data, dict):
                continue
            opts = data.get("compilerOptions")
            if not isinstance(opts, dict):
                continue
            base_url = opts.get("baseUrl")
            base_dir = (
                posixpath.normpath(posixpath.join(cfg_dir, base_url))
                if isinstance(base_url, str)
                else cfg_dir
            )
            base_dir = "" if base_dir == "." else base_dir
            if isinstance(base_url, str) and not ts_base_url:
                ts_base_url = base_dir
            paths = opts.get("paths")
            if isinstance(paths, dict):
                for alias, targets in paths.items():
                    if not isinstance(targets, list):
                        continue
                    prefix = str(alias).split("*", 1)[0]
                    tgt_prefixes = tuple(
                        posixpath.normpath(
                            posixpath.join(base_dir, str(t).split("*", 1)[0])
                        )
                        for t in targets
                        if isinstance(t, str)
                    )
                    if tgt_prefixes:
                        ts_paths.append((prefix, tgt_prefixes))

    return AliasMap(
        ts_paths=tuple(ts_paths),
        ts_base_url=ts_base_url,
        go_module=go_module,
        pkg_names=pkg_names,
    )


def _parse_go_module(content: str) -> str:
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("module "):
            return line[len("module ") :].strip().strip('"')
    return ""
