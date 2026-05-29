"""OPP-10: dependency-graph degree-weighted relevance scoring.

``_reference_score`` returned a flat 0.3 for any referenced symbol, so a
god-node interface imported by 30 files scored the same as a leaf helper
imported once — wasting FULL budget on the wrong chunks. Relevance now accepts
optional per-symbol weights derived from the dependency graph's fan-in, while
an empty weight map reproduces the prior flat behaviour exactly.
"""

from __future__ import annotations

from src.llm.chunker import ChunkKind, CodeChunk
from src.llm.relevance import (
    RelevanceScorer,
    ScoringContext,
    weights_from_fanin,
)
from src.models.dependency import DependencyEdge, DependencyKind, FileDependencyGraph


def _chunk(name: str, kind: ChunkKind = ChunkKind.FUNCTION) -> CodeChunk:
    return CodeChunk(
        name=name,
        kind=kind,
        start_line=1,
        end_line=3,
        content=f"def {name}():\n    pass\n",
        signature=f"def {name}()",
    )


def test_symbol_fanin_counts_distinct_importers():
    graph = FileDependencyGraph(
        edges=(
            DependencyEdge(
                source_file="a.py",
                target_file="hub.py",
                kind=DependencyKind.IMPORTS,
                target_symbol="Hub",
            ),
            DependencyEdge(
                source_file="b.py",
                target_file="hub.py",
                kind=DependencyKind.IMPORTS,
                target_symbol="Hub",
            ),
            # duplicate (source, symbol) must count once
            DependencyEdge(
                source_file="a.py",
                target_file="hub.py",
                kind=DependencyKind.INHERITS,
                target_symbol="Hub",
            ),
            DependencyEdge(
                source_file="c.py",
                target_file="hub.py",
                kind=DependencyKind.IMPORTS,
                target_symbol="Leaf",
            ),
        )
    )
    assert graph.symbol_fanin("hub.py") == {"Hub": 2, "Leaf": 1}
    assert graph.symbol_fanin("missing.py") == {}


def test_weights_from_fanin_scales_and_anchors_single_importer():
    w = weights_from_fanin({"Hub": 8, "Leaf": 1})
    assert w["Hub"] > w["Leaf"]
    # a single importer matches the prior flat reference boost (0.30)
    assert abs(w["Leaf"] - 0.30) < 1e-9
    # weights are capped at 1.0
    assert weights_from_fanin({"Mega": 100})["Mega"] <= 1.0


def test_high_fanin_chunk_scores_higher_than_leaf():
    ctx = ScoringContext(
        diff_ranges=[],
        referenced_names=frozenset({"Hub", "Leaf"}),
        symbol_weights=weights_from_fanin({"Hub": 8, "Leaf": 1}),
    )
    scorer = RelevanceScorer(ctx)
    assert scorer.score_chunk(_chunk("Hub", ChunkKind.CLASS)) > scorer.score_chunk(
        _chunk("Leaf")
    )


def test_empty_weights_reproduce_flat_reference_behaviour():
    chunk = _chunk("X")
    flat = RelevanceScorer(
        ScoringContext(diff_ranges=[], referenced_names=frozenset({"X"}))
    )
    weighted = RelevanceScorer(
        ScoringContext(
            diff_ranges=[],
            referenced_names=frozenset({"X"}),
            symbol_weights={},
        )
    )
    assert flat.score_chunk(chunk) == weighted.score_chunk(chunk)
