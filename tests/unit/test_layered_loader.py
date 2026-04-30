"""Tests for LayeredMemoryLoader — three-layer memory loading (L0/L1/L2)."""

from src.memory.models import (
    ConfidenceLevel,
    MemoryEntry,
    MemoryEntryType,
    PhaseSummary,
)
from src.memory.store import MemoryStore


class TestLayeredLoaderImport:
    def test_can_import(self):
        from src.memory.layered_loader import LayeredMemoryLoader

        assert LayeredMemoryLoader is not None


class TestL0ProjectProfile:
    def test_l0_always_included(self):
        from src.memory.layered_loader import LayeredMemoryLoader

        store = MemoryStore()
        store = store.set_codebase_profile("language", "python")
        store = store.set_codebase_profile("framework", "django")

        loader = LayeredMemoryLoader(store)
        result = loader.load_for_agent("planning")

        assert "python" in result
        assert "django" in result

    def test_l0_present_even_without_file_paths(self):
        from src.memory.layered_loader import LayeredMemoryLoader

        store = MemoryStore()
        store = store.set_codebase_profile("language", "go")

        loader = LayeredMemoryLoader(store)
        result = loader.load_for_agent("auto_merge")

        assert "go" in result

    def test_l0_empty_profile_no_section(self):
        from src.memory.layered_loader import LayeredMemoryLoader

        store = MemoryStore()
        loader = LayeredMemoryLoader(store)
        result = loader.load_for_agent("planning")

        assert "Project Profile" not in result


class TestL1PhaseEssentials:
    def test_l1_includes_current_phase_patterns(self):
        from src.memory.layered_loader import LayeredMemoryLoader

        store = MemoryStore()
        summary = PhaseSummary(
            phase="planning",
            patterns_discovered=["vendor/ is B-class dominant", "5 C-class in api/"],
        )
        store = store.record_phase_summary(summary)

        loader = LayeredMemoryLoader(store)
        result = loader.load_for_agent("planning")

        assert "vendor/" in result

    def test_l1_includes_previous_phase_decisions(self):
        from src.memory.layered_loader import LayeredMemoryLoader

        store = MemoryStore()
        summary = PhaseSummary(
            phase="planning",
            key_decisions=["Plan generated with 3 batches"],
        )
        store = store.record_phase_summary(summary)

        loader = LayeredMemoryLoader(store)
        result = loader.load_for_agent("auto_merge")

        assert "3 batches" in result

    def test_l1_no_previous_phase_for_planning(self):
        from src.memory.layered_loader import LayeredMemoryLoader

        store = MemoryStore()
        loader = LayeredMemoryLoader(store)
        result = loader.load_for_agent("planning")

        assert "Prior phase" not in result

    def test_l1_phase_chain(self):
        from src.memory.layered_loader import LayeredMemoryLoader

        store = MemoryStore()
        store = store.record_phase_summary(
            PhaseSummary(
                phase="auto_merge",
                key_decisions=["Processed 100 files"],
            )
        )

        loader = LayeredMemoryLoader(store)
        result = loader.load_for_agent("conflict_analysis")

        assert "100 files" in result


class TestL2FileRelevant:
    def test_l2_loaded_with_file_paths(self):
        from src.memory.layered_loader import LayeredMemoryLoader

        entry = MemoryEntry(
            entry_type=MemoryEntryType.PATTERN,
            phase="planning",
            content="auth module has high conflict risk",
            file_paths=["src/auth/handler.py"],
            confidence_level=ConfidenceLevel.EXTRACTED,
        )
        store = MemoryStore()
        store = store.add_entry(entry)

        loader = LayeredMemoryLoader(store)
        result = loader.load_for_agent("auto_merge", file_paths=["src/auth/handler.py"])

        assert "auth module" in result

    def test_l2_not_loaded_without_file_paths(self):
        from src.memory.layered_loader import LayeredMemoryLoader

        entry = MemoryEntry(
            entry_type=MemoryEntryType.PATTERN,
            phase="planning",
            content="auth module secret pattern",
            file_paths=["src/auth/handler.py"],
        )
        store = MemoryStore()
        store = store.add_entry(entry)

        loader = LayeredMemoryLoader(store)
        result = loader.load_for_agent("auto_merge")

        assert "auth module secret" not in result

    def test_l2_shows_confidence_label(self):
        from src.memory.layered_loader import LayeredMemoryLoader

        entry = MemoryEntry(
            entry_type=MemoryEntryType.PATTERN,
            phase="planning",
            content="extracted pattern here",
            file_paths=["src/core/engine.py"],
            confidence_level=ConfidenceLevel.EXTRACTED,
        )
        store = MemoryStore()
        store = store.add_entry(entry)

        loader = LayeredMemoryLoader(store)
        result = loader.load_for_agent("auto_merge", file_paths=["src/core/engine.py"])

        assert "EXTRACTED" in result

    def test_l2_unrelated_paths_excluded(self):
        from src.memory.layered_loader import LayeredMemoryLoader

        entry = MemoryEntry(
            entry_type=MemoryEntryType.PATTERN,
            phase="planning",
            content="vendor only pattern",
            file_paths=["vendor/lib.py"],
        )
        store = MemoryStore()
        store = store.add_entry(entry)

        loader = LayeredMemoryLoader(store)
        result = loader.load_for_agent("auto_merge", file_paths=["src/auth/handler.py"])

        assert "vendor only" not in result


class TestEmptyStore:
    def test_empty_store_returns_empty(self):
        from src.memory.layered_loader import LayeredMemoryLoader

        loader = LayeredMemoryLoader(MemoryStore())
        result = loader.load_for_agent("planning")

        assert result == ""

    def test_empty_store_with_file_paths(self):
        from src.memory.layered_loader import LayeredMemoryLoader

        loader = LayeredMemoryLoader(MemoryStore())
        result = loader.load_for_agent("auto_merge", file_paths=["foo.py"])

        assert result == ""


class TestCombinedLayers:
    def test_all_three_layers_present(self):
        from src.memory.layered_loader import LayeredMemoryLoader

        store = MemoryStore()
        store = store.set_codebase_profile("language", "python")
        store = store.record_phase_summary(
            PhaseSummary(
                phase="planning",
                patterns_discovered=["planning phase pattern"],
            )
        )
        store = store.add_entry(
            MemoryEntry(
                entry_type=MemoryEntryType.PATTERN,
                phase="planning",
                content="file specific pattern",
                file_paths=["src/main.py"],
                confidence_level=ConfidenceLevel.INFERRED,
            )
        )

        loader = LayeredMemoryLoader(store)
        result = loader.load_for_agent("planning", file_paths=["src/main.py"])

        assert "python" in result
        assert "planning phase pattern" in result
        assert "file specific pattern" in result

    def test_output_has_section_headers(self):
        from src.memory.layered_loader import LayeredMemoryLoader

        store = MemoryStore()
        store = store.set_codebase_profile("language", "rust")
        store = store.add_entry(
            MemoryEntry(
                entry_type=MemoryEntryType.PATTERN,
                phase="planning",
                content="rust pattern",
                file_paths=["src/lib.rs"],
            )
        )

        loader = LayeredMemoryLoader(store)
        result = loader.load_for_agent("auto_merge", file_paths=["src/lib.rs"])

        assert "## Project Profile" in result
        assert "## Relevant Patterns" in result


class TestOM3DynamicCaps:
    """O-M3: layered loader tightens L2 cap and applies relevance threshold
    when MemoryStore grows past configured thresholds."""

    def _build_store_with_n_entries(self, n: int) -> MemoryStore:
        store = MemoryStore()
        for i in range(n):
            store = store.add_entry(
                MemoryEntry(
                    entry_type=MemoryEntryType.PATTERN,
                    phase="planning",
                    content=f"entry-{i}",
                    file_paths=["src/main.py"],
                    confidence=0.9,
                    confidence_level=ConfidenceLevel.INFERRED,
                )
            )
        return store

    def test_default_cap_under_threshold(self):
        from src.memory.layered_loader import LayeredMemoryLoader

        store = self._build_store_with_n_entries(20)
        loader = LayeredMemoryLoader(store)
        result = loader.load_for_agent("planning", file_paths=["src/main.py"])
        relevant_lines = [ln for ln in result.splitlines() if ln.startswith("- [")]
        assert len(relevant_lines) <= 8

    def test_tightened_cap_when_over_100(self):
        from src.memory.layered_loader import LayeredMemoryLoader

        store = self._build_store_with_n_entries(150)
        loader = LayeredMemoryLoader(store)
        result = loader.load_for_agent("planning", file_paths=["src/main.py"])
        relevant_lines = [ln for ln in result.splitlines() if ln.startswith("- [")]
        assert len(relevant_lines) <= 6

    def test_tightened_cap_when_over_200(self):
        from src.memory.layered_loader import LayeredMemoryLoader

        store = self._build_store_with_n_entries(250)
        loader = LayeredMemoryLoader(store)
        result = loader.load_for_agent("planning", file_paths=["src/main.py"])
        relevant_lines = [ln for ln in result.splitlines() if ln.startswith("- [")]
        assert len(relevant_lines) <= 4

    def test_min_relevance_only_active_above_threshold(self):
        """min_relevance is gated by relevance_filter_threshold so small
        stores keep their original behavior."""
        from src.memory.layered_loader import LayeredMemoryLoader

        store = self._build_store_with_n_entries(20)
        loader = LayeredMemoryLoader(
            store,
            min_relevance=0.99,
            relevance_filter_threshold=100,
        )
        result = loader.load_for_agent("planning", file_paths=["src/main.py"])
        relevant_lines = [ln for ln in result.splitlines() if ln.startswith("- [")]
        assert len(relevant_lines) > 0


class TestM5TokenSimilarity:
    """M5 route B: lightweight token Jaccard similarity for L2 retrieval.

    Validation report §6.4 (M5): pure prefix matching misses sibling
    paths that share most segments but no common parent. The loader now
    falls back to token Jaccard so cross-package patterns are recalled.
    """

    def test_path_tokens_strip_extension(self):
        from src.memory.store import _path_tokens

        tokens = _path_tokens("pkg/plugin_manager/manager.go")
        assert "go" not in tokens
        assert "pkg" in tokens
        assert "plugin" in tokens
        assert "manager" in tokens

    def test_path_jaccard_sibling_paths(self):
        from src.memory.store import _path_jaccard

        score = _path_jaccard(
            "pkg/plugin_manager/manager.go",
            "pkg/plugin_runtime/runtime.go",
        )
        assert score >= 0.4

    def test_path_jaccard_unrelated_zero(self):
        from src.memory.store import _path_jaccard

        score = _path_jaccard("docs/intro.md", "vendor/foo/bar.go")
        assert score == 0.0

    def test_l2_recalls_sibling_path_via_jaccard(self):
        """End-to-end: an entry tagged with one plugin manager file is
        injected as L2 context for a sibling plugin runtime file even
        though they share no common prefix beyond ``pkg/``."""
        from src.memory.layered_loader import LayeredMemoryLoader

        store = MemoryStore()
        store = store.add_entry(
            MemoryEntry(
                entry_type=MemoryEntryType.PATTERN,
                phase="planning",
                file_paths=["pkg/plugin_manager/manager.go"],
                content="plugin lifecycle pattern",
                confidence=0.9,
                confidence_level=ConfidenceLevel.EXTRACTED,
            )
        )

        loader = LayeredMemoryLoader(store)
        result = loader.load_for_agent(
            "planning", file_paths=["pkg/plugin_runtime/runtime.go"]
        )

        assert "plugin lifecycle pattern" in result
