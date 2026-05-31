"""Pure content-based wrapper around ``git merge-file`` for outcome prediction.

The conflict_analyst phase used to pass ``FileDiff.conflict_count`` (computed
against the original refs, which are clean branches — always 0) into the LLM
prompt as the "is this file conflicted?" signal. The LLM correctly read 0 as
"no conflict" and produced rationales that ignored the actual semantic
collision a 3-way merge would produce.

``predict_native_3way_outcome`` runs the real merge on three raw strings and
returns a tri-state outcome the prompt can quote verbatim:

  * ``"clean"``    — git merged the three sides without markers
  * ``"conflict"`` — git produced ``<<<<<<<`` markers
  * ``"missing"``  — at least one of base / fork / upstream is None
                     (e.g. file present on only one side)

We piggyback on ``git merge-file`` via subprocess so we get the same engine
that ``GitTool.three_way_merge_file`` uses; no new merge logic to maintain.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Literal

NativeMergeOutcome = Literal["clean", "conflict", "missing"]


def predict_native_3way_outcome(
    base: str | None,
    fork: str | None,
    upstream: str | None,
) -> NativeMergeOutcome:
    if base is None or fork is None or upstream is None:
        return "missing"

    with tempfile.TemporaryDirectory() as td:
        base_p = Path(td) / "base"
        fork_p = Path(td) / "fork"
        up_p = Path(td) / "upstream"
        base_p.write_text(base, encoding="utf-8")
        fork_p.write_text(fork, encoding="utf-8")
        up_p.write_text(upstream, encoding="utf-8")

        result = subprocess.run(
            [
                "git",
                "merge-file",
                "--stdout",
                "-L",
                "fork",
                "-L",
                "base",
                "-L",
                "upstream",
                str(fork_p),
                str(base_p),
                str(up_p),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    if result.returncode < 0:
        return "conflict"
    output = result.stdout
    if "<<<<<<<" in output or ">>>>>>>" in output:
        return "conflict"
    if result.returncode > 0:
        return "conflict"
    return "clean"
