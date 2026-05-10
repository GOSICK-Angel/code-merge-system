"""P2-3: stash upstream-side diffs as patch files when executor's
LLM semantic_merge fails.

The original behavior was to call ``create_escalate_record`` and leave
the worktree blob equal to the fork version, silently dropping every
upstream change. That left escalate_human files effectively unmergeable
without the operator computing the diff by hand.

The stash captures ``git diff <merge_base>..<upstream_ref> -- <file>``
into ``<run_dir>/upstream_diff_stashes/<safe_path>.patch`` so the
human reviewer has the exact upstream delta ready to ``git apply``
(or cherry-pick into) the fork blob.
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.tools.git_tool import GitTool

logger = logging.getLogger(__name__)


def safe_patch_filename(file_path: str) -> str:
    """Map an arbitrary repo-relative path to a flat patch filename.

    ``backend/src/services/auth/auth.service.ts`` becomes
    ``backend__src__services__auth__auth.service.ts.patch``. Two
    underscores are used so single-underscore directory names round-trip
    unambiguously.
    """
    flattened = file_path.replace("/", "__").replace("\\", "__")
    return f"{flattened}.patch"


def stash_upstream_diff(
    file_path: str,
    base_ref: str,
    upstream_ref: str,
    git_tool: GitTool,
    stash_dir: Path,
) -> Path | None:
    """Write ``git diff base..upstream -- file_path`` to a patch file.

    Returns the absolute path of the patch file on success, or ``None``
    if the diff is empty / git failed. Creates ``stash_dir`` lazily.
    """
    if not base_ref or not upstream_ref:
        return None

    try:
        diff_text = git_tool.repo.git.diff(
            f"{base_ref}..{upstream_ref}", "--", file_path
        )
    except Exception as exc:
        logger.warning(
            "diff_stasher: git diff %s..%s -- %s failed: %s",
            base_ref,
            upstream_ref,
            file_path,
            exc,
        )
        return None

    if not diff_text or not diff_text.strip():
        return None

    stash_dir.mkdir(parents=True, exist_ok=True)
    patch_path = stash_dir / safe_patch_filename(file_path)
    header = (
        f"# Upstream-side delta stashed by code-merge-system\n"
        f"# file:     {file_path}\n"
        f"# base:     {base_ref}\n"
        f"# upstream: {upstream_ref}\n"
        f"# To apply on top of the fork blob:\n"
        f"#   git apply --3way {patch_path.name}\n"
        f"\n"
    )
    patch_path.write_text(header + diff_text, encoding="utf-8")
    return patch_path
