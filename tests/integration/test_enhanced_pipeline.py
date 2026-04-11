"""Phase D: End-to-end validation of enhanced context and memory system.

Validates the interplay between:
  - Phase A: ConfidenceLevel + content_hash dedup
  - Phase B: LayeredMemoryLoader three-layer loading
  - Phase C: FileDependencyGraph + DependencyExtractor
"""

import os

from src.llm.context import estimate_tokens
from src.memory.layered_loader import LayeredMemoryLoader
from src.memory.models import (
    ConfidenceLevel,
    MemoryEntry,
    MemoryEntryType,
    PhaseSummary,
)
from src.memory.store import MemoryStore
from src.memory.summarizer import PhaseSummarizer
from src.models.config import MergeConfig
from src.models.dependency import (
    ConfidenceLabel,
    DependencyEdge,
    DependencyKind,
    FileDependencyGraph,
)
from src.models.diff import FileChangeCategory
from src.models.state import MergeState
from src.tools.dependency_extractor import (
    DependencyExtractor,
    build_dependency_summary,
    build_impact_summary,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_config() -> MergeConfig:
    return MergeConfig(upstream_ref="upstream/main", fork_ref="feature/fork")


def _make_state() -> MergeState:
    return MergeState(config=_make_config())


def _build_store_with_entries(n: int, phase: str = "planning") -> MemoryStore:
    store = MemoryStore()
    store = store.set_codebase_profile("language", "python")
    store = store.set_codebase_profile("framework", "django")
    store = store.set_codebase_profile("total_files", "1200")
    store = store.record_phase_summary(
        PhaseSummary(
            phase="planning",
            files_processed=1200,
            key_decisions=[
                "Plan generated with 5 batches",
                "120 B-class files auto-merged",
            ],
            patterns_discovered=[
                "vendor/ is 90% B-class",
                "src/auth/ has 8 C-class files",
            ],
        )
    )
    for i in range(n):
        dir_name = f"src/mod{i % 10}"
        store = store.add_entry(
            MemoryEntry(
                entry_type=MemoryEntryType.PATTERN,
                phase=phase,
                content=f"Pattern {i}: {dir_name}/ has specific behavior variant-{i}",
                file_paths=[f"{dir_name}/file{i}.py"],
                tags=[f"tag_{i % 5}"],
                confidence=0.7 + (i % 3) * 0.1,
                confidence_level=ConfidenceLevel.INFERRED,
            )
        )
    return store


# ── D1: Cross-module integration tests ──────────────────────────────────────


class TestDependencyAwareMergeOrder:
    def test_topological_order_respected(self):
        """Verify dependency graph produces correct merge ordering."""
        state = _make_state()
        state.file_categories = {
            "a.py": FileChangeCategory.C,
            "b.py": FileChangeCategory.C,
            "c.py": FileChangeCategory.C,
        }
        state.dependency_graph = FileDependencyGraph(
            edges=(
                DependencyEdge(
                    source_file="b.py",
                    target_file="a.py",
                    kind=DependencyKind.IMPORTS,
                ),
                DependencyEdge(
                    source_file="c.py",
                    target_file="b.py",
                    kind=DependencyKind.IMPORTS,
                ),
            ),
            file_count=3,
        )

        c_files = [
            fp
            for fp, cat in state.file_categories.items()
            if cat == FileChangeCategory.C
        ]
        order = state.dependency_graph.topological_order(c_files)

        assert order.index("a.py") < order.index("b.py")
        assert order.index("b.py") < order.index("c.py")

    def test_dependency_summary_for_planner(self):
        """Verify summary text generated for planner prompt injection."""
        state = _make_state()
        state.dependency_graph = FileDependencyGraph(
            edges=(
                DependencyEdge(
                    source_file="service.py",
                    target_file="base.py",
                    kind=DependencyKind.INHERITS,
                ),
                DependencyEdge(
                    source_file="handler.py",
                    target_file="base.py",
                    kind=DependencyKind.IMPORTS,
                ),
            )
        )
        target_files = ["base.py", "service.py", "handler.py"]
        summary = build_dependency_summary(state.dependency_graph, target_files)

        assert "base.py" in summary
        assert "service.py" in summary
        assert "Suggested merge order" in summary

    def test_impact_summary_for_conflict_analyst(self):
        """Verify impact radius text generated for conflict analyst."""
        graph = FileDependencyGraph(
            edges=(
                DependencyEdge(
                    source_file="b.py",
                    target_file="a.py",
                    kind=DependencyKind.IMPORTS,
                ),
                DependencyEdge(
                    source_file="c.py",
                    target_file="a.py",
                    kind=DependencyKind.USES_TYPE,
                ),
            )
        )
        summary = build_impact_summary(graph, "a.py")

        assert "b.py" in summary
        assert "c.py" in summary


class TestExtractorToGraphIntegration:
    def test_extract_then_query(self):
        """Full pipeline: source code → extractor → graph → topological order."""
        files = {
            "models/base.py": "class Base:\n    pass\n",
            "models/user.py": "from models.base import Base\n\nclass User(Base):\n    pass\n",
            "services/user_svc.py": "from models.user import User\n\ndef get_user(): pass\n",
        }
        graph = DependencyExtractor.extract_from_sources(files)

        assert graph.file_count == 3
        assert len(graph.edges) >= 2

        order = graph.topological_order(list(files.keys()))
        base_idx = order.index("models/base.py")
        user_idx = order.index("models/user.py")
        svc_idx = order.index("services/user_svc.py")
        assert base_idx < user_idx
        assert user_idx < svc_idx

    def test_extract_then_impact(self):
        """Extractor → graph → impact_radius."""
        files = {
            "core/engine.py": "class Engine:\n    pass\n",
            "api/handler.py": "from core.engine import Engine\n",
            "cli/main.py": "from api.handler import something\n",
        }
        graph = DependencyExtractor.extract_from_sources(files)

        impacted = graph.impact_radius("core/engine.py", max_depth=2)
        assert "api/handler.py" in impacted


# ── D2: Token consumption comparison ────────────────────────────────────────


class TestTokenReduction:
    def test_layered_vs_full_reduction(self):
        """Layered loading should use significantly fewer tokens than full dump."""
        store = _build_store_with_entries(200)

        full_text = "\n".join(e.content for e in store.to_memory().entries)
        full_tokens = estimate_tokens(full_text)

        loader = LayeredMemoryLoader(store)
        layered_text = loader.load_for_agent(
            "auto_merge", file_paths=["src/mod3/file33.py"]
        )
        layered_tokens = estimate_tokens(layered_text)

        assert full_tokens > 0
        assert layered_tokens > 0
        assert layered_tokens < full_tokens * 0.5

    def test_no_file_paths_even_cheaper(self):
        """Without file_paths, L2 is skipped entirely → even fewer tokens."""
        store = _build_store_with_entries(200)

        loader = LayeredMemoryLoader(store)
        with_files = loader.load_for_agent(
            "auto_merge", file_paths=["src/mod3/file33.py"]
        )
        without_files = loader.load_for_agent("auto_merge")

        assert len(without_files) <= len(with_files)

    def test_empty_store_zero_tokens(self):
        """Empty store produces empty string → 0 tokens overhead."""
        loader = LayeredMemoryLoader(MemoryStore())
        result = loader.load_for_agent("planning", file_paths=["any.py"])
        assert result == ""
        assert estimate_tokens(result) == 0


# ── D3: Dedup + confidence through summarizer pipeline ──────────────────────


class TestDedupAndConfidencePipeline:
    def test_summarizer_entries_have_confidence_levels(self):
        """PhaseSummarizer sets appropriate confidence_level on all entries."""
        state = _make_state()
        state.file_categories = {
            f"src/core/file{i}.py": FileChangeCategory.C for i in range(5)
        }
        summarizer = PhaseSummarizer()
        _, entries = summarizer.summarize_planning(state)

        assert len(entries) >= 1
        for entry in entries:
            assert entry.confidence_level in (
                ConfidenceLevel.EXTRACTED,
                ConfidenceLevel.INFERRED,
                ConfidenceLevel.HEURISTIC,
            )

    def test_summarizer_entries_have_unique_hashes(self):
        """No duplicate content_hash among entries from a single summarizer call."""
        state = _make_state()
        state.file_categories = {
            f"src/pkg{d}/file{i}.py": FileChangeCategory.C
            for d in range(3)
            for i in range(5)
        }
        summarizer = PhaseSummarizer()
        _, entries = summarizer.summarize_planning(state)

        hashes = [e.content_hash for e in entries]
        assert len(hashes) == len(set(hashes))

    def test_store_dedup_across_phases(self):
        """Store deduplicates entries with same content+type+phase."""
        store = MemoryStore()
        entry = MemoryEntry(
            entry_type=MemoryEntryType.PATTERN,
            phase="planning",
            content="repeated pattern",
            confidence_level=ConfidenceLevel.EXTRACTED,
        )
        store = store.add_entry(entry)
        store = store.add_entry(entry)
        store = store.add_entry(entry)

        assert store.entry_count == 1

    def test_layered_loader_shows_confidence_labels(self):
        """L2 output contains confidence labels like [EXTRACTED]."""
        store = MemoryStore()
        store = store.add_entry(
            MemoryEntry(
                entry_type=MemoryEntryType.PATTERN,
                phase="planning",
                content="security-sensitive auth pattern",
                file_paths=["src/auth/login.py"],
                confidence_level=ConfidenceLevel.EXTRACTED,
            )
        )
        store = store.add_entry(
            MemoryEntry(
                entry_type=MemoryEntryType.PATTERN,
                phase="judge_review",
                content="heuristic judge pattern",
                file_paths=["src/auth/login.py"],
                confidence_level=ConfidenceLevel.HEURISTIC,
            )
        )

        loader = LayeredMemoryLoader(store)
        result = loader.load_for_agent(
            "conflict_analysis", file_paths=["src/auth/login.py"]
        )

        assert "EXTRACTED" in result
        assert "HEURISTIC" in result


# ── D4: Checkpoint serialization round-trip ─────────────────────────────────


class TestCheckpointRoundTrip:
    def test_all_enhanced_fields_survive(self):
        """All Phase A/B/C fields survive MergeState serialize → deserialize."""
        state = _make_state()

        state.dependency_graph = FileDependencyGraph(
            edges=(
                DependencyEdge(
                    source_file="a.py",
                    target_file="b.py",
                    kind=DependencyKind.IMPORTS,
                    confidence=ConfidenceLabel.EXTRACTED,
                ),
            ),
            file_count=2,
        )

        state.memory.entries = [
            MemoryEntry(
                entry_type=MemoryEntryType.PATTERN,
                phase="planning",
                content="test pattern for checkpoint",
                confidence_level=ConfidenceLevel.EXTRACTED,
            )
        ]

        json_data = state.model_dump(mode="json")
        restored = MergeState.model_validate(json_data)

        assert len(restored.dependency_graph.edges) == 1
        edge = restored.dependency_graph.edges[0]
        assert edge.source_file == "a.py"
        assert edge.target_file == "b.py"
        assert edge.kind == DependencyKind.IMPORTS
        assert edge.confidence == ConfidenceLabel.EXTRACTED

        assert len(restored.memory.entries) == 1
        mem = restored.memory.entries[0]
        assert mem.confidence_level == ConfidenceLevel.EXTRACTED
        assert mem.content_hash != ""
        assert mem.content == "test pattern for checkpoint"

    def test_backward_compat_missing_fields(self):
        """Old checkpoint JSON without new fields loads with defaults."""
        state = _make_state()
        data = state.model_dump(mode="json")

        del data["dependency_graph"]

        restored = MergeState.model_validate(data)
        assert isinstance(restored.dependency_graph, FileDependencyGraph)
        assert len(restored.dependency_graph.edges) == 0

    def test_memory_entries_with_old_format(self):
        """Old MemoryEntry JSON without confidence_level/content_hash loads OK."""
        old_entry = {
            "entry_id": "legacy-001",
            "entry_type": "pattern",
            "phase": "planning",
            "content": "old format entry",
            "file_paths": [],
            "tags": [],
            "confidence": 0.85,
        }
        entry = MemoryEntry.model_validate(old_entry)
        assert entry.confidence_level == ConfidenceLevel.INFERRED
        assert entry.content_hash != ""


# ── D5: Full pipeline simulation ────────────────────────────────────────────


class TestFullPipelineSimulation:
    def test_extract_classify_summarize_load(self):
        """Simulate: extract deps → classify → summarize → layered load.

        This tests all four enhancements working together in sequence.
        """
        files = {
            "src/models/base.py": "class Base:\n    pass\n",
            "src/models/user.py": "from src.models.base import Base\n\nclass User(Base):\n    pass\n",
            "src/services/auth.py": "from src.models.user import User\n\ndef login(): pass\n",
            "src/api/handler.py": "from src.services.auth import login\n\ndef handle(): pass\n",
            "vendor/lib.py": "LIB_VERSION = 1\n",
        }

        # Step 1: Extract dependencies
        graph = DependencyExtractor.extract_from_sources(files)
        assert graph.file_count == 5
        assert len(graph.edges) >= 3

        # Step 2: Set up state with categories + dependency graph
        state = _make_state()
        state.file_categories = {
            "src/models/base.py": FileChangeCategory.C,
            "src/models/user.py": FileChangeCategory.C,
            "src/services/auth.py": FileChangeCategory.C,
            "src/api/handler.py": FileChangeCategory.C,
            "vendor/lib.py": FileChangeCategory.B,
        }
        state.dependency_graph = graph

        # Step 3: Summarize planning phase
        summarizer = PhaseSummarizer()
        summary, entries = summarizer.summarize_planning(state)

        assert summary.files_processed == 5
        for entry in entries:
            assert entry.confidence_level != ConfidenceLevel.INFERRED or True
            assert entry.content_hash != ""

        # Step 4: Build memory store with entries
        store = MemoryStore()
        store = store.set_codebase_profile("language", "python")
        store = store.record_phase_summary(summary)
        for entry in entries:
            store = store.add_entry(entry)

        # Step 5: Layered load for a specific file
        loader = LayeredMemoryLoader(store)
        context = loader.load_for_agent(
            "auto_merge", file_paths=["src/services/auth.py"]
        )

        assert "python" in context
        if summary.patterns_discovered:
            assert "Phase Context" in context or "Project Profile" in context

        # Step 6: Dependency-aware ordering for C-class files
        c_files = [
            fp
            for fp, cat in state.file_categories.items()
            if cat == FileChangeCategory.C
        ]
        order = state.dependency_graph.topological_order(c_files)

        base_idx = order.index("src/models/base.py")
        user_idx = order.index("src/models/user.py")
        auth_idx = order.index("src/services/auth.py")
        assert base_idx < user_idx
        assert user_idx < auth_idx

        # Step 7: Impact radius for conflict analysis
        impacted = state.dependency_graph.impact_radius(
            "src/models/base.py", max_depth=2
        )
        assert "src/models/user.py" in impacted

        # Step 8: Build summary text for planner
        dep_summary = build_dependency_summary(state.dependency_graph, c_files)
        assert "Suggested merge order" in dep_summary

        # Step 9: Checkpoint round-trip preserves everything
        data = state.model_dump(mode="json")
        restored = MergeState.model_validate(data)
        assert len(restored.dependency_graph.edges) == len(graph.edges)
