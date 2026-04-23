"""P1-2: BaselineParser registry.

Parsers accept command stdout and return a structured dict:

    {"passed": int, "failed": int, "failed_ids": list[str]}

New parsers register via the ``@register_parser`` decorator or via the
``code_merge_system.baseline_parsers`` entry_points group (setuptools).

Built-in parsers:
    pytest_summary, mypy_json, basedpyright_json, ruff_json, eslint_json,
    tsc_errors, go_test_json, cargo_test_json, junit_xml
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable
from typing import TypedDict

logger = logging.getLogger(__name__)


class BaselineSnapshot(TypedDict):
    passed: int
    failed: int
    failed_ids: list[str]


BaselineParser = Callable[[str], BaselineSnapshot]


_REGISTRY: dict[str, BaselineParser] = {}


def register_parser(name: str) -> Callable[[BaselineParser], BaselineParser]:
    def wrapper(fn: BaselineParser) -> BaselineParser:
        _REGISTRY[name] = fn
        return fn

    return wrapper


def get_parser(name: str) -> BaselineParser | None:
    return _REGISTRY.get(name)


def available_parsers() -> list[str]:
    return sorted(_REGISTRY)


def empty_snapshot() -> BaselineSnapshot:
    return {"passed": 0, "failed": 0, "failed_ids": []}


def diff_new_failures(
    baseline: BaselineSnapshot | dict[str, object],
    current: BaselineSnapshot | dict[str, object],
) -> list[str]:
    """Return failed_ids present in *current* but NOT in *baseline*."""
    base_raw = baseline.get("failed_ids", []) or []
    cur_raw = current.get("failed_ids", []) or []
    base_ids: set[str] = set(base_raw) if isinstance(base_raw, list) else set()
    cur_ids: set[str] = set(cur_raw) if isinstance(cur_raw, list) else set()
    return sorted(cur_ids - base_ids)


def load_entry_point_parsers() -> None:
    """Discover parsers published via setuptools entry_points.

    Third-party packages may register under the
    ``code_merge_system.baseline_parsers`` group. Missing-group is not fatal.
    """
    try:
        from importlib.metadata import entry_points
    except ImportError:
        return

    try:
        eps = entry_points()
        group = (
            eps.select(group="code_merge_system.baseline_parsers")
            if hasattr(eps, "select")
            else eps.get("code_merge_system.baseline_parsers", [])  # type: ignore[arg-type]
        )
    except Exception as exc:
        logger.debug("Entry-point discovery failed: %s", exc)
        return

    for ep in group:
        try:
            fn = ep.load()
            _REGISTRY[ep.name] = fn
        except Exception as exc:
            logger.warning("Failed to load parser '%s': %s", ep.name, exc)


for _mod_name in (
    "pytest_summary",
    "mypy_json",
    "basedpyright_json",
    "ruff_json",
    "eslint_json",
    "tsc_errors",
    "go_test_json",
    "cargo_test_json",
    "junit_xml",
):
    try:
        importlib.import_module(f"src.tools.baseline_parsers.{_mod_name}")
    except Exception as _exc:
        logger.warning("Failed to import baseline parser '%s': %s", _mod_name, _exc)


__all__ = [
    "BaselineParser",
    "BaselineSnapshot",
    "available_parsers",
    "diff_new_failures",
    "empty_snapshot",
    "get_parser",
    "load_entry_point_parsers",
    "register_parser",
]
