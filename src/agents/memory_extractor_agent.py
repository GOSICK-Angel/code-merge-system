from __future__ import annotations

import json
import logging
from typing import Any

from src.agents.base_agent import BaseAgent
from src.agents.registry import AgentRegistry
from src.llm.client import ModelOutputError
from src.llm.prompts.gate_registry import get_gate
from src.memory.models import ConfidenceLevel, MemoryEntry, MemoryEntryType
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

        system = get_gate("M-SYSTEM").render()
        prompt = get_gate("M-EXTRACT-INSIGHT").render(phase, events, max_insights)

        raw = await self._call_llm_with_retry(
            [{"role": "user", "content": prompt}],
            system=system,
        )

        entries = _parse_entries(str(raw), phase, existing_hashes, max_insights)
        logger.info(
            "MemoryExtractorAgent: phase=%s, %d new insights extracted",
            phase,
            len(entries),
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
