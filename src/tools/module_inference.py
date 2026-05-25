"""Group file paths into functional modules.

Target-repo agnostic: a file's module is decided by, in order of
precedence, an explicit glob override, the fork's declared
rewritten-module paths, or directory topology (a monorepo container's
immediate child, else the top-level directory). No fork or module name
is ever hardcoded — everything flows from :class:`ModuleConfig` and the
forks-profile.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable

from src.models.config import ModuleConfig

DEFAULT_MODULE = "default"


def _glob_match(file_path: str, pattern: str) -> bool:
    if fnmatch.fnmatch(file_path, pattern):
        return True
    # A wildcard-free pattern is treated as a directory prefix so a
    # rewritten-module path like ``api/auth`` captures everything beneath
    # it without the author having to append ``/**``.
    if not any(ch in pattern for ch in "*?["):
        prefix = pattern.rstrip("/")
        return file_path == prefix or file_path.startswith(prefix + "/")
    return False


def _module_name_from_pattern(pattern: str) -> str:
    """Derive a stable module name from a rewritten-module glob/path by
    keeping the literal prefix before the first wildcard."""
    stem = pattern.split("*", 1)[0].rstrip("/")
    return stem or DEFAULT_MODULE


def _topology_module(file_path: str, container_dirs: list[str]) -> str:
    parts = file_path.split("/")
    # ``<container>/<module>/...`` — the child of a known container dir
    # names the module; a file sitting directly in the container falls
    # through to the top-level rule below.
    if len(parts) >= 3 and parts[0] in container_dirs:
        return parts[1]
    if len(parts) >= 2:
        return parts[0]
    return DEFAULT_MODULE


def infer_modules(
    file_paths: Iterable[str],
    config: ModuleConfig,
    rewritten_module_paths: list[str] | None = None,
) -> dict[str, str]:
    """Map each path to a module name.

    ``rewritten_module_paths`` is the list of ``RewrittenModule.path``
    globs from the active forks-profile (caller extracts them so this
    tool stays decoupled from the profile model).
    """
    rewritten = rewritten_module_paths or []
    result: dict[str, str] = {}

    for path in file_paths:
        if config.mode == "off":
            result[path] = DEFAULT_MODULE
            continue

        matched: str | None = None
        for pattern, name in config.explicit.items():
            if _glob_match(path, pattern):
                matched = name
                break
        if matched is None:
            for pattern in rewritten:
                if _glob_match(path, pattern):
                    matched = _module_name_from_pattern(pattern)
                    break
        if matched is None:
            matched = (
                _topology_module(path, config.container_dirs)
                if config.mode == "auto"
                else DEFAULT_MODULE
            )
        result[path] = matched

    return result
