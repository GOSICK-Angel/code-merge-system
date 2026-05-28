"""Strip hedging phrases from ConflictAnalyst rationale text.

ConflictAnalyst sometimes writes rationale like
"Both sides added version entries; without seeing actual content,
semantic merge is appropriate". Downstream Agents (Judge) include the
rationale verbatim in their prompts and echo the hedging back as a real
defect — producing false-positive FAIL verdicts.

The sanitizer detects the hedging segment, replaces it with a neutral
marker, and leaves the rest of the rationale intact. Business fields
(strategy, confidence, conflict_type) are NOT modified.
"""

from __future__ import annotations

import re

_HEDGING_MARKER = "[analyst lacked source access]"

_HEDGING_PATTERN = re.compile(
    r"(?:without seeing (?:the )?actual content"
    r"|cannot be verified"
    r"|cannot confirm"
    r"|unable to verify"
    r"|missing original(?: file content)?)",
    re.IGNORECASE,
)


def sanitize_hedging(text: str) -> str:
    if not text:
        return text
    return _HEDGING_PATTERN.sub(_HEDGING_MARKER, text)
