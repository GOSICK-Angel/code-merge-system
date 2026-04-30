"""Memory hit tracker — observability for layered memory loader utilization.

Tracks how often the layered loader returns non-empty sections per phase
and per layer (L0 profile / L1 phase context / L2 file-relevant). Owned by
the orchestrator, passed through to ``LayeredMemoryLoader`` so all agent
calls share one counter. ``summary()`` is read at run-end by the report
writer to surface a "Memory Utilization" section.

Optionally persists each update to a sidecar JSON file so partial data
survives mid-run aborts and accumulates across resume cycles.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path
from threading import Lock
from typing import Literal

Layer = Literal["l0", "l1_patterns", "l1_decisions", "l2"]

# Schema bump: v2 adds per-file-injection map + per-entry outcome stats so
# we can credit/blame memory entries based on the Judge's final verdict
# (O-M4). v1 sidecars are treated as empty (loader bails on version
# mismatch); the next save migrates to v2.
_SCHEMA_VERSION = 2


class MemoryHitTracker:
    def __init__(self, persist_path: Path | None = None) -> None:
        self._lock = Lock()
        self._calls_by_phase: dict[str, int] = defaultdict(int)
        self._hit_calls_by_phase: dict[str, int] = defaultdict(int)
        self._entries_by_phase_layer: dict[str, dict[str, int]] = defaultdict(
            lambda: defaultdict(int)
        )
        # O-M4: per-file injection map (in-memory; folded into per-entry
        # outcomes when record_outcome fires).
        self._injections_by_file: dict[str, set[str]] = defaultdict(set)
        # entry_id → {"pass": n, "fail": m} (persisted)
        self._entry_outcomes: dict[str, dict[str, int]] = defaultdict(
            lambda: {"pass": 0, "fail": 0}
        )
        self._persist_path: Path | None = None
        if persist_path is not None:
            self.set_persist_path(persist_path)

    def set_persist_path(self, path: Path) -> None:
        with self._lock:
            self._persist_path = path
            if path.exists():
                self._load_unsafe()

    def record_call(self, phase: str, layers_with_content: dict[Layer, int]) -> None:
        with self._lock:
            self._calls_by_phase[phase] += 1
            if any(count > 0 for count in layers_with_content.values()):
                self._hit_calls_by_phase[phase] += 1
            for layer, count in layers_with_content.items():
                if count > 0:
                    self._entries_by_phase_layer[phase][layer] += count
            if self._persist_path is not None:
                self._persist_unsafe()

    def record_injection(self, file_paths: list[str], entry_ids: list[str]) -> None:
        """O-M4: remember which entries were injected for each file_path.

        Folded into per-entry pass/fail counters when ``record_outcome``
        fires for that file_path. Cheap and additive — multiple injections
        for the same file accumulate into the union.
        """
        if not entry_ids:
            return
        with self._lock:
            for fp in file_paths:
                self._injections_by_file[fp].update(entry_ids)

    def record_outcome(self, file_path: str, success: bool) -> None:
        """O-M4: credit or blame entries that were injected for ``file_path``.

        Called by JudgeReviewPhase after the final verdict — once per
        passed_files entry (success=True) and per failed_files entry
        (success=False). Increments per-entry counters and persists.
        """
        with self._lock:
            entry_ids = self._injections_by_file.get(file_path)
            if not entry_ids:
                return
            key = "pass" if success else "fail"
            for eid in entry_ids:
                self._entry_outcomes[eid][key] += 1
            if self._persist_path is not None:
                self._persist_unsafe()

    def _load_unsafe(self) -> None:
        try:
            assert self._persist_path is not None
            data = json.loads(self._persist_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(data, dict) or data.get("schema_version") != _SCHEMA_VERSION:
            return
        for phase, count in (data.get("calls_by_phase") or {}).items():
            self._calls_by_phase[phase] = int(count)
        for phase, count in (data.get("hit_calls_by_phase") or {}).items():
            self._hit_calls_by_phase[phase] = int(count)
        for phase, layers in (data.get("entries_by_phase_layer") or {}).items():
            for layer, count in (layers or {}).items():
                self._entries_by_phase_layer[phase][layer] = int(count)
        for eid, counters in (data.get("entry_outcomes") or {}).items():
            if isinstance(counters, dict):
                self._entry_outcomes[eid] = {
                    "pass": int(counters.get("pass", 0)),
                    "fail": int(counters.get("fail", 0)),
                }

    def _persist_unsafe(self) -> None:
        assert self._persist_path is not None
        path = self._persist_path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": _SCHEMA_VERSION,
            "calls_by_phase": dict(self._calls_by_phase),
            "hit_calls_by_phase": dict(self._hit_calls_by_phase),
            "entries_by_phase_layer": {
                phase: dict(layers)
                for phase, layers in self._entries_by_phase_layer.items()
            },
            "entry_outcomes": {
                eid: dict(counters)
                for eid, counters in self._entry_outcomes.items()
            },
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def summary(self) -> dict[str, object]:
        with self._lock:
            total_calls = sum(self._calls_by_phase.values())
            total_hit_calls = sum(self._hit_calls_by_phase.values())
            hit_rate = total_hit_calls / total_calls if total_calls > 0 else 0.0
            by_phase: dict[str, dict[str, int | float]] = {}
            for phase, calls in self._calls_by_phase.items():
                hits = self._hit_calls_by_phase.get(phase, 0)
                by_phase[phase] = {
                    "calls": calls,
                    "hit_calls": hits,
                    "hit_rate": hits / calls if calls > 0 else 0.0,
                }
            by_layer: dict[str, int] = defaultdict(int)
            for phase_layers in self._entries_by_phase_layer.values():
                for layer, count in phase_layers.items():
                    by_layer[layer] += count
            return {
                "total_calls": total_calls,
                "hit_calls": total_hit_calls,
                "hit_rate": hit_rate,
                "by_phase": by_phase,
                "by_layer": dict(by_layer),
                "outcomes": self._outcomes_summary_unsafe(),
            }

    def _outcomes_summary_unsafe(self) -> dict[str, object]:
        ranked: list[tuple[str, int, int, float]] = []
        for eid, counters in self._entry_outcomes.items():
            p = counters.get("pass", 0)
            f = counters.get("fail", 0)
            total = p + f
            if total == 0:
                continue
            score = (p - f) / total  # +1 always helpful, -1 always harmful
            ranked.append((eid, p, f, score))
        if not ranked:
            return {"tracked_entries": 0, "top_helpful": [], "top_harmful": []}
        ranked.sort(key=lambda x: x[3], reverse=True)
        helpful = [
            {"entry_id": eid, "pass": p, "fail": f, "score": round(s, 3)}
            for eid, p, f, s in ranked[:5]
            if s > 0
        ]
        harmful = [
            {"entry_id": eid, "pass": p, "fail": f, "score": round(s, 3)}
            for eid, p, f, s in reversed(ranked[-5:])
            if s < 0
        ]
        return {
            "tracked_entries": len(ranked),
            "top_helpful": helpful,
            "top_harmful": harmful,
        }

    def entry_outcome(self, entry_id: str) -> dict[str, int]:
        """Return per-entry counters; useful for tests / external scoring."""
        with self._lock:
            counters = self._entry_outcomes.get(entry_id)
            if counters is None:
                return {"pass": 0, "fail": 0}
            return dict(counters)

    def harmful_entry_ids(
        self,
        threshold: float = -0.5,
        min_observations: int = 2,
    ) -> frozenset[str]:
        """O-M6: entry_ids whose outcome score is at/below ``threshold``.

        Score is ``(pass - fail) / (pass + fail)``; values approach -1 when
        an entry is consistently associated with judge failures. Requires
        at least ``min_observations`` total observations to avoid pruning
        entries on a single bad run.
        """
        with self._lock:
            harmful: set[str] = set()
            for eid, counters in self._entry_outcomes.items():
                p = counters.get("pass", 0)
                f = counters.get("fail", 0)
                total = p + f
                if total < min_observations:
                    continue
                score = (p - f) / total
                if score <= threshold:
                    harmful.add(eid)
            return frozenset(harmful)
