"""Skeleton evaluation harness for the code-merge system.

This package provides scripts/utilities to drive the evaluation procedure
described in ``doc/evaluation/{README,metrics,dataset,procedure,acceptance}.md``.

NOTE: The bundled sample datasets under ``tests/eval/datasets/`` are skeletal —
they exist solely to verify the schema and the evaluation pipeline plumbing.
They do NOT constitute a trustworthy evaluation result. Real release-grade
evaluation requires the full sampling matrix described in
``doc/evaluation/dataset.md`` (e.g. category x risk grid x >= 5 samples each).
"""

from __future__ import annotations

EVAL_VERSION = "0.1.0"

__all__ = ["EVAL_VERSION"]
