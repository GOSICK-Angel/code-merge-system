"""P1 (Wave 4): the "silent gate-skip" alarm primitive.

Several deterministic safety gates read git content best-effort and degrade to
"skip" on a read failure / unresolvable precondition (``git_tool`` swallows
``GitCommandError`` → ``None``; ``_safe_read_text`` swallows ``OSError`` →
``None``). A systematically misconfigured ``git_tool`` could thus silently
disable a whole class of gates while the run still reported a clean
``COMPLETED`` — "the gate did not fire" read as "the gate passed".

This module gives those sites one standard way to record that a gate could not
run. The entry lands in ``state.errors`` (the existing partial-failure channel),
so the CI summary flips ``success → partial_failure`` (exit
``EXIT_PARTIAL_FAILURE``) and the interactive/resume terminal prints
"completed WITH WARNINGS" instead of a green success line — without a new
``SystemStatus`` or state-machine edge.

Deliberately dependency-free (no ``MergeState`` import): it returns a plain
dict so read-only reviewer agents (the Judge receives a ``ReadOnlyStateView``
and may not write ``state``) can accumulate entries on the agent and hand them
back through the phase-completion payload, while non-reviewer phases append
directly to ``state.errors``.

Scope note (intentional): only *unambiguous* gate-disablement is recorded — a
caught exception, or a precondition that is never legitimately absent
(``git_tool is None``, ``merge_base``/``upstream_ref`` missing at Judge time).
A bare ``get_file_hash(...) is None`` for a file that may simply not exist at a
ref is NOT recorded blindly, because ``None`` there is ambiguous (legitimate
absence vs broken git) and would over-alarm. Callers decide per-site.
"""

from __future__ import annotations

from datetime import datetime

GATE_SKIP_PHASE = "gate_skip"


def gate_skip_entry(gate: str, path: str, reason: str) -> dict[str, str]:
    """Build a standardized ``state.errors`` entry for a skipped gate.

    ``gate`` is a stable identifier (e.g. ``"fork_export_preservation"``),
    ``path`` the file or ``"(all)"`` for a pipeline-wide skip, ``reason`` a
    short human explanation. The message is prefixed ``GATE_SKIPPED`` so the
    report and CI summary can group these distinctly from real defect findings.
    """
    return {
        "timestamp": datetime.now().isoformat(),
        "phase": GATE_SKIP_PHASE,
        "message": f"GATE_SKIPPED [{gate}] {path}: {reason}",
    }
