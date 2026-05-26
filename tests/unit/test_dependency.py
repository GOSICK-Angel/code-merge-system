"""Tests for dependency graph data models."""

import pytest

from src.models.dependency import (
    ConfidenceLabel,
    DependencyEdge,
    DependencyKind,
    FileDependencyGraph,
)


class TestDependencyEdge:
    def test_create_edge(self):
        edge = DependencyEdge(
            source_file="handler.py",
            target_file="base.py",
            kind=DependencyKind.IMPORTS,
        )
        assert edge.source_file == "handler.py"
        assert edge.target_file == "base.py"
        assert edge.confidence == ConfidenceLabel.EXTRACTED

    def test_edge_frozen(self):
        edge = DependencyEdge(
            source_file="a.py",
            target_file="b.py",
            kind=DependencyKind.IMPORTS,
        )
        with pytest.raises(Exception):
            edge.source_file = "c.py"

    def test_edge_with_symbols(self):
        edge = DependencyEdge(
            source_file="service.py",
            target_file="model.py",
            kind=DependencyKind.INHERITS,
            source_symbol="UserService",
            target_symbol="BaseModel",
        )
        assert edge.source_symbol == "UserService"
        assert edge.target_symbol == "BaseModel"

    def test_edge_serialization_roundtrip(self):
        edge = DependencyEdge(
            source_file="a.py",
            target_file="b.py",
            kind=DependencyKind.CALLS,
            confidence=ConfidenceLabel.INFERRED,
        )
        data = edge.model_dump(mode="json")
        restored = DependencyEdge.model_validate(data)
        assert restored.kind == DependencyKind.CALLS
        assert restored.confidence == ConfidenceLabel.INFERRED


class TestFileDependencyGraph:
    def _make_graph(self):
        return FileDependencyGraph(
            edges=(
                DependencyEdge(
                    source_file="handler.py",
                    target_file="base.py",
                    kind=DependencyKind.INHERITS,
                ),
                DependencyEdge(
                    source_file="service.py",
                    target_file="base.py",
                    kind=DependencyKind.IMPORTS,
                ),
                DependencyEdge(
                    source_file="service.py",
                    target_file="utils.py",
                    kind=DependencyKind.IMPORTS,
                ),
            ),
            file_count=4,
        )

    def test_dependents_of(self):
        graph = self._make_graph()
        deps = graph.dependents_of("base.py")
        assert set(deps) == {"handler.py", "service.py"}

    def test_dependents_of_leaf(self):
        graph = self._make_graph()
        assert graph.dependents_of("handler.py") == []

    def test_dependencies_of(self):
        graph = self._make_graph()
        deps = graph.dependencies_of("service.py")
        assert set(deps) == {"base.py", "utils.py"}

    def test_dependencies_of_root(self):
        graph = self._make_graph()
        assert graph.dependencies_of("base.py") == []

    def test_empty_graph(self):
        graph = FileDependencyGraph()
        assert graph.dependents_of("any.py") == []
        assert graph.dependencies_of("any.py") == []
        assert graph.topological_order(["a.py"]) == ["a.py"]
        assert graph.impact_radius("a.py") == set()

    def test_topological_order_linear(self):
        graph = FileDependencyGraph(
            edges=(
                DependencyEdge(
                    source_file="c.py",
                    target_file="b.py",
                    kind=DependencyKind.IMPORTS,
                ),
                DependencyEdge(
                    source_file="b.py",
                    target_file="a.py",
                    kind=DependencyKind.IMPORTS,
                ),
            )
        )
        order = graph.topological_order(["a.py", "b.py", "c.py"])
        assert order.index("a.py") < order.index("b.py")
        assert order.index("b.py") < order.index("c.py")

    def test_topological_order_cycle(self):
        graph = FileDependencyGraph(
            edges=(
                DependencyEdge(
                    source_file="a.py",
                    target_file="b.py",
                    kind=DependencyKind.IMPORTS,
                ),
                DependencyEdge(
                    source_file="b.py",
                    target_file="a.py",
                    kind=DependencyKind.IMPORTS,
                ),
            )
        )
        order = graph.topological_order(["a.py", "b.py"])
        assert set(order) == {"a.py", "b.py"}

    def test_topological_order_partial(self):
        graph = FileDependencyGraph(
            edges=(
                DependencyEdge(
                    source_file="c.py",
                    target_file="b.py",
                    kind=DependencyKind.IMPORTS,
                ),
                DependencyEdge(
                    source_file="b.py",
                    target_file="a.py",
                    kind=DependencyKind.IMPORTS,
                ),
            )
        )
        order = graph.topological_order(["b.py", "c.py"])
        assert order.index("b.py") < order.index("c.py")

    def test_impact_radius_depth_1(self):
        graph = FileDependencyGraph(
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
                DependencyEdge(
                    source_file="d.py",
                    target_file="c.py",
                    kind=DependencyKind.IMPORTS,
                ),
            )
        )
        assert graph.impact_radius("a.py", max_depth=1) == {"b.py"}

    def test_impact_radius_depth_2(self):
        graph = FileDependencyGraph(
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
                DependencyEdge(
                    source_file="d.py",
                    target_file="c.py",
                    kind=DependencyKind.IMPORTS,
                ),
            )
        )
        assert graph.impact_radius("a.py", max_depth=2) == {"b.py", "c.py"}

    def test_impact_radius_no_dependents(self):
        graph = self._make_graph()
        assert graph.impact_radius("handler.py", max_depth=3) == set()

    def test_graph_serialization_roundtrip(self):
        graph = self._make_graph()
        data = graph.model_dump(mode="json")
        restored = FileDependencyGraph.model_validate(data)
        assert len(restored.edges) == 3
        assert restored.file_count == 4

    def test_graph_frozen(self):
        graph = self._make_graph()
        with pytest.raises(Exception):
            graph.file_count = 99


class TestReferencedSymbols:
    def test_collects_inbound_target_symbols(self):
        graph = FileDependencyGraph(
            edges=(
                DependencyEdge(
                    source_file="a.py",
                    target_file="b.py",
                    kind=DependencyKind.IMPORTS,
                    target_symbol="foo",
                ),
                DependencyEdge(
                    source_file="c.py",
                    target_file="b.py",
                    kind=DependencyKind.IMPORTS,
                    target_symbol="bar",
                ),
                DependencyEdge(
                    source_file="a.py",
                    target_file="b.py",
                    kind=DependencyKind.IMPORTS,
                ),  # module import, no symbol — skipped
                DependencyEdge(
                    source_file="a.py",
                    target_file="d.py",
                    kind=DependencyKind.IMPORTS,
                    target_symbol="baz",
                ),
            )
        )
        assert graph.referenced_symbols("b.py") == frozenset({"foo", "bar"})
        assert graph.referenced_symbols("d.py") == frozenset({"baz"})
        assert graph.referenced_symbols("missing.py") == frozenset()
