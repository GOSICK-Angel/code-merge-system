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

# A full dotted CHAIN (``a.b.c.d``) so every adjacent (parent, child) pair is
# tested — the non-overlapping ``finditer`` over ``_QUALIFIED_REF`` consumes
# ``core.schemas`` and never re-examines ``schemas._isoWeek``, the exact zod
# fabricated-leaf shape. One or more ``.member`` segments are required.
_DOTTED_CHAIN = re.compile(r"\b[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+\b")


def _adjacent_pairs(chain: str) -> list[tuple[str, str]]:
    """Split ``a.b.c`` into ``[(a,b),(b,c)]`` adjacent (parent, child) pairs."""
    parts = chain.split(".")
    return [(parts[k], parts[k + 1]) for k in range(len(parts) - 1)]


def _source_pair_index(sources: list[str]) -> tuple[set[tuple[str, str]], set[str]]:
    """Precompute, across all sources, the set of adjacent member pairs that
    genuinely occur and the set of identifiers used as a ``base.`` (a real,
    referenced object). Set membership replaces the old ``ref in src`` substring
    test, which whitelisted a fabricated ``core._isoWeek`` whenever a longer
    ``core._isoWeekFoo`` (or a comment / string) merely contained it.
    """
    pairs: set[tuple[str, str]] = set()
    bases: set[str] = set()
    for src in sources:
        if not src:
            continue
        for chain in _DOTTED_CHAIN.finditer(src):
            chain_pairs = _adjacent_pairs(chain.group(0))
            pairs.update(chain_pairs)
            for parent, _child in chain_pairs:
                bases.add(parent)
    return pairs, bases


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

    source_pairs, source_bases = _source_pair_index(sources)

    invented: set[str] = set()
    for chain in _DOTTED_CHAIN.finditer(merged):
        # Skip a chain immediately followed by ``*`` — in rationale prose this
        # is wildcard-family notation (``core._iso*`` = "the _iso* family"), and
        # in code it is member-times-multiply; either way, suppressing it keeps
        # the guard's "false positives near zero" contract without reopening the
        # substring bypass for the common ``x.y`` form.
        if chain.end() < len(merged) and merged[chain.end()] == "*":
            continue
        for parent, child in _adjacent_pairs(chain.group(0)):
            if (parent, child) in source_pairs:
                continue  # exact pair recombined from a source — allowed
            if parent in source_bases:
                # real, referenced object but a member that exists nowhere —
                # fabricated. (A brand-new ``parent`` is a legit new import.)
                invented.add(f"{parent}.{child}")
    return sorted(invented)[:limit]
