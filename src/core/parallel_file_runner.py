"""Bounded-concurrency parallel runner for independent per-file async tasks (O-C).

Concurrency defaults to the number of active API keys so throughput matches
available credentials without guessing a magic number.  A failure in one
file's handler never cancels sibling tasks.
"""

from __future__ import annotations

import asyncio
import os
from collections import Counter
from typing import Any, Callable, TypeVar, Awaitable

K = TypeVar("K")
T = TypeVar("T")


class FileShardOverlap(ValueError):
    """Raised by ``assert_disjoint_file_shards`` when two shards share a
    file. The orchestrator's fan-out contract assumes disjoint shards so
    duplicates would cause double-processing (and, for write-heavy paths,
    last-writer-wins corruption).
    """


def assert_disjoint_file_shards(shards: list[list[str]]) -> None:
    """U5: refuse to fan out across overlapping file shards.

    Each call site that hands a list of shards to ``ParallelFileRunner``
    must call this first so violations surface as a typed error close to
    the bug, not as silent double-writes downstream. Pure function — does
    not mutate ``shards`` and never reorders elements.
    """
    seen: Counter[str] = Counter()
    for shard in shards:
        for fp in shard:
            seen[fp] += 1
    duplicates = sorted(fp for fp, count in seen.items() if count > 1)
    if duplicates:
        raise FileShardOverlap(f"file shards overlap on: {duplicates}")


class ParallelFileRunner:
    def __init__(self, concurrency: int) -> None:
        self._concurrency = max(1, concurrency)

    @classmethod
    def from_api_key_env_list(
        cls,
        api_key_env_list: list[str],
        override: int | None = None,
    ) -> "ParallelFileRunner":
        """Build a runner whose concurrency matches available credentials.

        ``override`` (from ``MergeConfig.parallel_file_concurrency``) takes
        precedence when set.  Otherwise we count how many env-var names in
        *api_key_env_list* actually resolve to a non-empty value.
        """
        if override is not None:
            return cls(concurrency=override)
        active = sum(1 for name in api_key_env_list if os.environ.get(name, "").strip())
        return cls(concurrency=max(1, active))

    async def run_files(
        self,
        keys: list[Any],
        handler: Callable[[Any], Awaitable[Any]],
    ) -> dict[Any, Any]:
        """Run *handler* for each key concurrently, bounded by concurrency limit.

        Returns ``{key: result}`` for successes and ``{key: BaseException}``
        for failures.  Keys are processed independently: a single failure
        does not cancel or affect sibling tasks.
        """
        if not keys:
            return {}

        semaphore = asyncio.Semaphore(self._concurrency)

        async def _bounded(key: Any) -> tuple[Any, Any]:
            async with semaphore:
                try:
                    return key, await handler(key)
                except Exception as exc:
                    return key, exc

        pairs: list[tuple[Any, Any]] = await asyncio.gather(
            *[_bounded(k) for k in keys]
        )
        return dict(pairs)
