"""Fixtures for the evaluation harness test-suite (``tests/eval/``).

Kept independent from ``tests/unit/conftest.py`` because:

* Eval tests must NOT inherit ``MERGE_DEV`` from the host shell — running
  the same test under a developer's ``MERGE_DEV=1`` shell would otherwise
  silently change subprocess artifact paths in unrelated future phases.
* Eval tests do not need the asyncio event-loop priming hack used by the
  src-side unit tests.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _strip_merge_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure no test in ``tests/eval/`` ever observes ``MERGE_DEV`` in env.

    The merge-system flips into a different artifact-path layout when
    ``MERGE_DEV=1`` (``src/cli/paths.py:39 is_dev_mode``). Eval tests
    assert against the prod-mode layout exclusively, so we strip the var
    upfront and rely on :func:`scripts.eval._common.eval_subprocess_env`
    to do the same for any subprocess we spawn.
    """
    monkeypatch.delenv("MERGE_DEV", raising=False)


@pytest.fixture
def tmp_workdir(tmp_path: Path) -> Path:
    """Per-test scratch directory rooted under ``tmp_path``.

    Distinct from ``tmp_path`` only in name — the alias documents intent
    at the call site (``tmp_workdir`` reads as "evaluation workdir").
    """
    workdir = tmp_path / "eval_workdir"
    workdir.mkdir()
    return workdir


@pytest.fixture
def eval_subprocess_env_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Any]:
    """Build :func:`scripts.eval._common.eval_subprocess_env` for tests.

    Yields a callable that constructs the eval subprocess env from a
    caller-supplied base mapping. The fixture deliberately leaves the
    real ``os.environ`` untouched so individual tests can ``monkeypatch``
    it before invoking the factory and assert the expected scrubbing.
    """
    from scripts.eval._common import eval_subprocess_env

    def _factory(**kwargs: Any) -> dict[str, str]:
        return eval_subprocess_env(**kwargs)

    yield _factory
    # monkeypatch.undo runs automatically; no explicit teardown needed.
    del monkeypatch
