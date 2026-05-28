"""Parse ``REQUIRES NEW API: <symbol>`` sentinels from analyst rationale.

PR-D-A.2: when the conflict_analyst's grounding rule (PR-D-A.1) forces
the LLM to declare a missing symbol via the structured sentinel rather
than the old "if available" hedge, we want to surface those declarations
as an *informational* signal — distinct from PR-A's grounding warnings,
which target genuine fabrication.

The marker phrase is uppercase and exact (the prompt mandates it); the
symbol may be a plain identifier or a qualified reference like
``core._isoWeek`` or ``schemas.$ZodISOWeek``. Anything past the symbol
on the same line is treated as the justification and ignored.
"""

from __future__ import annotations

import re

_SENTINEL = re.compile(r"REQUIRES NEW API:\s*([A-Za-z_$][\w$.]*)")


def extract_required_new_apis(rationale: str) -> list[str]:
    """Return symbols declared via the ``REQUIRES NEW API:`` sentinel.

    Order-preserving deduplication keeps the reviewer's first sight of
    a symbol earlier in the list — matches how the rationale reads top
    to bottom.
    """
    if not rationale:
        return []
    seen: set[str] = set()
    ordered: list[str] = []
    for match in _SENTINEL.finditer(rationale):
        sym = match.group(1)
        if sym in seen:
            continue
        seen.add(sym)
        ordered.append(sym)
    return ordered
