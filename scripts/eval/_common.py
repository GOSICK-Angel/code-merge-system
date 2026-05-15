"""Shared utilities for the evaluation harness.

Lightweight, pure-python helpers used by every ``scripts/eval/*.py`` CLI:

- :func:`eval_subprocess_env` — build the subprocess environment that drives
  ``merge`` from inside the evaluation runner. Strips ``MERGE_DEV`` so the
  subprocess always lands in production-mode artifact paths
  (``<cwd>/.merge/runs/<run_id>/``) regardless of the host shell, and injects
  dummy LLM keys so the merge CLI does not abort on key validation.
- :func:`resolve_workdir` — normalise/create an absolute work directory.
- JSON IO helpers (atomic, UTF-8, sorted keys) for deterministic artifacts.

This module deliberately stays under ~250 lines and has no dependency on
``src/`` so the evaluation harness remains decoupled from the merge runtime.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

DUMMY_LLM_KEY = "DUMMY-EVAL-KEY"
"""Sentinel value injected into evaluation subprocess env vars.

Selecting an obviously-fake value (vs. an empty string) keeps stack traces
self-explanatory if it ever leaks into a real provider call — which would be
a bug, since ``run.py`` should always be pointed at a fake ``merge-bin``.
"""

LLM_API_KEY_ENV_VARS: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
)


def eval_subprocess_env(
    *,
    base_env: Mapping[str, str] | None = None,
    use_real_keys: bool = False,
) -> dict[str, str]:
    """Build the env dict for a ``merge`` subprocess launched from eval/run.py.

    Args:
        base_env: Source environment to copy. Defaults to ``os.environ``.
        use_real_keys: When True, keep the host's LLM API keys verbatim
            (used by ``--use-real-keys`` runs that talk to a real provider).
            When False (default and CI), substitute :data:`DUMMY_LLM_KEY` so
            we never accidentally bill real credentials during fixture runs.

    Returns:
        A new ``dict[str, str]`` (never the original mapping) with:

        * ``MERGE_DEV`` removed (forces prod-mode artifact paths in subprocess
          regardless of the host developer's shell — see
          ``src/cli/paths.py:39 is_dev_mode``).
        * ``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY`` set to dummy unless
          ``use_real_keys=True``.

    The function never mutates ``base_env`` (immutable pattern).
    """
    source = dict(base_env if base_env is not None else os.environ)
    source.pop("MERGE_DEV", None)
    if not use_real_keys:
        for key in LLM_API_KEY_ENV_VARS:
            source[key] = DUMMY_LLM_KEY
    return source


def resolve_workdir(workdir: str | os.PathLike[str], *, create: bool = True) -> Path:
    """Resolve ``workdir`` to an absolute :class:`Path`, optionally creating it.

    Args:
        workdir: User-supplied path (relative or absolute).
        create: When True, ensure the directory exists (``mkdir -p`` semantics).

    Returns:
        Absolute :class:`Path`.

    Raises:
        FileExistsError: If the path exists but is not a directory.
    """
    path = Path(workdir).expanduser().resolve()
    if path.exists() and not path.is_dir():
        raise FileExistsError(f"workdir path exists but is not a directory: {path}")
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def read_json(path: str | os.PathLike[str]) -> Any:
    """Read a JSON file (UTF-8) and return parsed payload.

    Raises :class:`FileNotFoundError` if the file does not exist and
    :class:`json.JSONDecodeError` on malformed input — neither is silenced.
    """
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(
    path: str | os.PathLike[str],
    payload: Any,
    *,
    sort_keys: bool = True,
    indent: int = 2,
) -> Path:
    """Atomically write ``payload`` as JSON to ``path``.

    Atomicity is via tmp-file + ``os.replace`` so a concurrent reader never
    sees a half-written file. Default ``sort_keys=True`` produces stable diffs
    across runs, which matters for ``lock.py`` content hashes.

    Returns the resolved :class:`Path` for chaining.
    """
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, sort_keys=sort_keys, indent=indent, ensure_ascii=False)
    return _atomic_write_text(target, body + "\n")


def atomic_write_text(path: str | os.PathLike[str], content: str) -> Path:
    """Atomically write text (UTF-8) to ``path``. Returns the resolved path."""
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    return _atomic_write_text(target, content)


def _atomic_write_text(target: Path, content: str) -> Path:
    fd, tmp_name = tempfile.mkstemp(
        prefix=target.name + ".", suffix=".tmp", dir=str(target.parent)
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_path, target)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    return target


__all__ = [
    "DUMMY_LLM_KEY",
    "LLM_API_KEY_ENV_VARS",
    "atomic_write_text",
    "eval_subprocess_env",
    "read_json",
    "resolve_workdir",
    "write_json",
]
