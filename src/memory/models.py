from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field


class MemoryEntryType(str, Enum):
    PATTERN = "pattern"
    DECISION = "decision"
    RELATIONSHIP = "relationship"
    PHASE_SUMMARY = "phase_summary"
    CODEBASE_INSIGHT = "codebase_insight"


class MemoryEntry(BaseModel, frozen=True):
    entry_id: str = Field(default_factory=lambda: str(uuid4()))
    entry_type: MemoryEntryType
    phase: str
    content: str
    file_paths: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    created_at: datetime = Field(default_factory=datetime.now)


class PhaseSummary(BaseModel, frozen=True):
    phase: str
    files_processed: int = 0
    key_decisions: list[str] = Field(default_factory=list)
    patterns_discovered: list[str] = Field(default_factory=list)
    error_summary: str = ""
    statistics: dict[str, int | float] = Field(default_factory=dict)


class MergeMemory(BaseModel):
    entries: list[MemoryEntry] = Field(default_factory=list)
    phase_summaries: dict[str, PhaseSummary] = Field(default_factory=dict)
    codebase_profile: dict[str, str] = Field(default_factory=dict)
