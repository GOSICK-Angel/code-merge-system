"""Deterministic guard against hallucinated cross-module member accesses.

方案3.2: a chunked semantic merge can invent a member access on a *real*
imported object — observed in the zod merge, where a genuine ``core`` gained a
fabricated ``core._isoWeek`` / ``core.$ZodISOWeek`` present in neither fork nor
upstream (the conflict analyst had pre-warned that ``core._isoWeek`` would need
to be created).

Like :func:`executor_agent._foreign_chars`, the scan is LLM-free and
deliberately narrow. A faithful merge recombines references that already exist
in a source, so a ``base.member`` reference is flagged only when:

- it is absent from *every* source (the merge produced it), and
- ``base.`` appears in some source — so ``base`` is a real, referenced object
  rather than a brand-new import the merge legitimately added; that narrows the
  signal to the "real module, fabricated member" case.

Anything recombined from a source, and any fully-new ``base`` (a new import),
is left untouched, keeping false positives near zero. Restricted to languages
where ``.`` is member access so dotted tokens in JSON / yaml / markdown are
never flagged.
"""

from __future__ import annotations

import re
from pathlib import Path

_MEMBER_ACCESS_EXTS = frozenset(
    {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".go", ".rs"}
)

# base.member where base starts with a letter/_/$ (so version tokens like
# ``1.2`` never match) and both halves are plain identifiers.
_QUALIFIED_REF = re.compile(r"\b([A-Za-z_$][\w$]*)\.([A-Za-z_$][\w$]*)\b")


def scan_rationale_for_hallucinations(
    rationale: str,
    sources: list[str],
    file_path: str,
    limit: int = 5,
) -> list[str]:
    """Scan analyst rationale prose for fabricated ``base.member`` references.

    PR-A: the conflict_analyst's free-text rationale occasionally invents a
    symbol on a real imported object (the zod run produced ``core._isoWeek``
    where neither fork nor upstream defines it). Semantics match
    :func:`find_invented_member_accesses` — a real ``base`` is required so
    brand-new imports and English noise stay quiet — only the input is prose
    rather than merged code. ``file_path`` gates the language: rationale for
    JSON/YAML/Markdown files is skipped because ``.`` is not member access
    there and natural prose ``version.major`` would false-positive.
    """
    return find_invented_member_accesses(rationale, sources, file_path, limit)


def find_invented_member_accesses(
    merged: str,
    sources: list[str],
    file_path: str,
    limit: int = 5,
) -> list[str]:
    """Return ``base.member`` refs the merge invented on an existing object.

    Empty for unsupported file types, empty input, or when every qualified
    reference is either recombined from a source or rooted at a brand-new
    ``base``. Results are sorted and capped at *limit* for stable output.
    """
    if Path(file_path).suffix.lower() not in _MEMBER_ACCESS_EXTS or not merged:
        return []

    invented: set[str] = set()
    for match in _QUALIFIED_REF.finditer(merged):
        ref = match.group(0)
        if any(ref in src for src in sources):
            continue  # recombined from a source — allowed
        base_dot = match.group(1) + "."
        if any(base_dot in src for src in sources):
            invented.add(ref)  # real base, fabricated member
    return sorted(invented)[:limit]
