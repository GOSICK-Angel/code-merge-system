from __future__ import annotations

import json
import logging
from typing import Any

from src.agents.base_agent import BaseAgent
from src.agents.registry import AgentRegistry
from src.llm.client import ModelOutputError
from src.llm.prompts.gate_registry import get_gate
from src.memory.models import ConfidenceLevel, MemoryEntry, MemoryEntryType
from src.models.dependency import ConfidenceLabel
from src.models.diff import FileChangeCategory
from src.models.message import AgentMessage, AgentType, MessageType
from src.models.plan import MergePhase
from src.models.state import MergeState

logger = logging.getLogger(__name__)

_VALID_ENTRY_TYPES = {e.value for e in MemoryEntryType}


class MemoryExtractorAgent(BaseAgent):
    """LLM-assisted memory extraction for high-information-value events.

    Called by Orchestrator._update_memory() when llm_extraction is enabled
    and trigger conditions are met.  Returns a list of MemoryEntry objects
    (confidence_level="inferred") to be merged into the active store.
    """

    agent_type = AgentType.MEMORY_EXTRACTOR
    contract_name = "memory_extractor"

    async def extract(self, phase: str, state: MergeState) -> list[MemoryEntry]:
        """Main entry point called by Orchestrator (not via Phase loop)."""
        view = self.restricted_view(state)
        max_insights: int = getattr(
            getattr(view.config, "memory", None), "max_insights_per_phase", 5
        )

        events = {
            "errors": list(view.errors)[-10:],
            "plan_disputes": [
                (d.model_dump() if hasattr(d, "model_dump") else dict(d))
                for d in (view.plan_disputes or [])
            ],
            "judge_verdicts_log": list(view.judge_verdicts_log)[-5:],
            "judge_repair_rounds": view.judge_repair_rounds,
            "coordinator_directives": [
                (d.model_dump() if hasattr(d, "model_dump") else dict(d))
                for d in (getattr(view, "coordinator_directives", None) or [])
            ][-5:],
        }

        existing_hashes: set[str] = set()
        if self._memory_store is not None:
            try:
                for e in self._memory_store.to_memory().entries:
                    existing_hashes.add(e.content_hash)
            except Exception:
                pass

        # Phase C §6.4 / §4: deterministic, AST-confident graph insights
        # (God Node hubs + cross-directory "surprising connections") take
        # priority and share the per-phase budget; the LLM fills the rest.
        graph_entries = _graph_insights(view, phase, existing_hashes, max_insights)
        remaining = max_insights - len(graph_entries)

        llm_entries: list[MemoryEntry] = []
        if remaining > 0:
            system = get_gate("M-SYSTEM").render()
            prompt = get_gate("M-EXTRACT-INSIGHT").render(phase, events, remaining)
            raw = await self._call_llm_with_retry(
                [{"role": "user", "content": prompt}],
                system=system,
            )
            llm_entries = _parse_entries(str(raw), phase, existing_hashes, remaining)

        entries = graph_entries + llm_entries
        logger.info(
            "MemoryExtractorAgent: phase=%s, %d new insights (%d graph, %d llm)",
            phase,
            len(entries),
            len(graph_entries),
            len(llm_entries),
        )
        return entries

    def can_handle(self, state: MergeState) -> bool:
        return False

    async def run(self, state: MergeState) -> AgentMessage:
        return AgentMessage(
            sender=AgentType.MEMORY_EXTRACTOR,
            receiver=AgentType.ORCHESTRATOR,
            phase=MergePhase.ANALYSIS,
            message_type=MessageType.INFO,
            subject="MemoryExtractorAgent invoked via extract(), not run()",
            payload={},
        )


_GRAPH_ACTIONABLE = {
    FileChangeCategory.B,
    FileChangeCategory.C,
    FileChangeCategory.D_MISSING,
}


def _top_dir(path: str) -> str:
    norm = path.replace("\\", "/")
    return norm.split("/", 1)[0] if "/" in norm else ""


def _graph_insights(
    view: Any,
    phase: str,
    existing_hashes: set[str],
    max_insights: int,
) -> list[MemoryEntry]:
    """Deterministic dependency-graph memories (Phase C).

    Persists two AST-confident facts about changed files so future runs
    inherit them: God Node hubs (high direct-dependent count) as
    CODEBASE_INSIGHT, and cross-top-directory EXTRACTED couplings (a
    "surprising connection" proxy that does not need community detection) as
    RELATIONSHIP. Empty graph -> ``[]`` (safe degrade)."""
    graph = getattr(view, "dependency_graph", None)
    if graph is None or not graph.edges:
        return []
    categories = getattr(view, "file_categories", None) or {}
    changed = sorted(fp for fp, cat in categories.items() if cat in _GRAPH_ACTIONABLE)
    if not changed:
        return []

    cfg = getattr(view.config, "dependency_graph", None)
    min_dep = getattr(cfg, "god_node_min_dependents", 8)
    max_depth = getattr(cfg, "max_depth", 3)

    entries: list[MemoryEntry] = []

    def _add(entry: MemoryEntry) -> bool:
        if entry.content_hash in existing_hashes:
            return False
        existing_hashes.add(entry.content_hash)
        entries.append(entry)
        return len(entries) >= max_insights

    for fp in changed:
        hint = graph.impact_hint(
            fp, max_depth=max_depth, god_node_min_dependents=min_dep
        )
        if hint.is_god_node:
            entry = MemoryEntry(
                entry_type=MemoryEntryType.CODEBASE_INSIGHT,
                phase=phase,
                content=(
                    f"Dependency hub: {fp} has {hint.direct_dependents} direct "
                    f"dependents (impact radius {hint.impact_radius}). Changes "
                    "here ripple widely — review and preserve its interface."
                )[:120],
                confidence=0.9,
                confidence_level=ConfidenceLevel.EXTRACTED,
                file_paths=[fp],
                tags=["dependency_graph", "god_node"],
            )
            if _add(entry):
                return entries

    changed_set = set(changed)
    seen_pairs: set[tuple[str, str]] = set()
    for edge in graph.edges:
        # ConfidenceLabel is a StrEnum; only EXTRACTED edges are reliable
        # enough to persist as a fact (plan §5).
        if edge.confidence != ConfidenceLabel.EXTRACTED:
            continue
        src, tgt = edge.source_file, edge.target_file
        if src not in changed_set or src == tgt:
            continue
        if _top_dir(src) == _top_dir(tgt) or not _top_dir(src) or not _top_dir(tgt):
            continue
        key = (src, tgt)
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        entry = MemoryEntry(
            entry_type=MemoryEntryType.RELATIONSHIP,
            phase=phase,
            content=(
                f"Cross-directory coupling: {src} imports {tgt} "
                f"({_top_dir(src)} -> {_top_dir(tgt)}). Verify both move together."
            )[:120],
            confidence=0.9,
            confidence_level=ConfidenceLevel.EXTRACTED,
            file_paths=[src, tgt],
            tags=["dependency_graph", "surprising_connection"],
        )
        if _add(entry):
            return entries

    return entries


def _parse_entries(
    raw: str,
    phase: str,
    existing_hashes: set[str],
    max_insights: int,
) -> list[MemoryEntry]:
    raw = raw.strip()
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end == -1:
        raise ModelOutputError(
            raw=raw,
            schema_name="MemoryExtractor",
            detail=f"expected JSON array in response, got: {raw[:200]!r}",
        )
    try:
        items: list[dict[str, Any]] = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ModelOutputError(
            raw=raw,
            schema_name="MemoryExtractor",
            detail=f"JSON parse failed: {exc}. Raw: {raw[:200]!r}",
        ) from exc

    if not isinstance(items, list):
        raise ModelOutputError(
            raw=raw,
            schema_name="MemoryExtractor",
            detail="top-level JSON value must be an array",
        )

    entries: list[MemoryEntry] = []
    for item in items[:max_insights]:
        if not isinstance(item, dict):
            continue
        raw_type = item.get("entry_type", "")
        if raw_type not in _VALID_ENTRY_TYPES:
            logger.warning("MemoryExtractor: unknown entry_type %r, skipping", raw_type)
            continue
        content = str(item.get("content", "")).strip()
        if not content:
            continue

        entry = MemoryEntry(
            entry_type=MemoryEntryType(raw_type),
            phase=phase,
            content=content[:120],
            confidence=float(item.get("confidence", 0.6)),
            confidence_level=ConfidenceLevel.INFERRED,
            file_paths=list(item.get("file_paths", [])),
            tags=list(item.get("tags", [])),
        )
        if entry.content_hash in existing_hashes:
            continue
        existing_hashes.add(entry.content_hash)
        entries.append(entry)

    return entries


AgentRegistry.register("memory_extractor", MemoryExtractorAgent)
