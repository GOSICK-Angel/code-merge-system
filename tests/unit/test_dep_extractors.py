"""Multi-language dependency extraction + graceful tree-sitter degradation."""

from __future__ import annotations

import importlib.util

import pytest

from src.models.dependency import ConfidenceLabel, DependencyKind
from src.tools.dep_extractors import language_for
from src.tools.dependency_extractor import DependencyExtractor

_HAS_TS = (
    importlib.util.find_spec("tree_sitter") is not None
    and importlib.util.find_spec("tree_sitter_typescript") is not None
)


class TestLanguageRouting:
    def test_language_for_known_extensions(self):
        assert language_for("a/b.py") == "python"
        assert language_for("a/b.ts") == "typescript"
        assert language_for("a/b.tsx") == "tsx"
        assert language_for("a/b.js") == "javascript"
        assert language_for("a/b.go") == "go"

    def test_language_for_unknown_extension(self):
        assert language_for("README.md") is None
        assert language_for("data.json") is None

    def test_languages_allowlist_filters_python(self):
        files = {
            "models.py": "class A: pass\n",
            "main.py": "from models import A\n",
        }
        # Python excluded from the allow-list -> no edges.
        graph = DependencyExtractor.extract_from_sources(files, languages=["go"])
        assert len(graph.edges) == 0
        # file_count still reflects all input files.
        assert graph.file_count == 2


class TestTreeSitterGracefulDegradation:
    def test_js_files_do_not_crash_without_tree_sitter(self):
        """A JS/TS repo must never crash extraction; it degrades to no edges
        when the optional [ast] extra is unavailable."""
        files = {
            "src/util.ts": "export const x = 1;\n",
            "src/main.ts": "import { x } from './util';\n",
        }
        graph = DependencyExtractor.extract_from_sources(files)
        assert graph.file_count == 2
        if not _HAS_TS:
            assert len(graph.edges) == 0


@pytest.mark.skipif(not _HAS_TS, reason="tree-sitter ([ast] extra) not installed")
class TestTreeSitterTypescript:
    def test_relative_ts_import_resolves(self):
        files = {
            "src/util.ts": "export const x = 1;\n",
            "src/main.ts": "import { x } from './util';\n",
        }
        graph = DependencyExtractor.extract_from_sources(files)
        assert any(
            e.source_file == "src/main.ts"
            and e.target_file == "src/util.ts"
            and e.kind == DependencyKind.IMPORTS
            and e.confidence == ConfidenceLabel.EXTRACTED
            for e in graph.edges
        )

    def test_bare_specifier_produces_no_edge(self):
        files = {"src/main.ts": "import React from 'react';\n"}
        graph = DependencyExtractor.extract_from_sources(files)
        assert len(graph.edges) == 0

    def test_tsx_relative_import_resolves(self):
        files = {
            "ui/Button.tsx": "export const Button = () => null;\n",
            "ui/App.tsx": "import { Button } from './Button';\n",
        }
        graph = DependencyExtractor.extract_from_sources(files)
        assert any(
            e.source_file == "ui/App.tsx" and e.target_file == "ui/Button.tsx"
            for e in graph.edges
        )

    def test_js_export_from_resolves(self):
        files = {
            "lib/helpers.js": "export function h() {}\n",
            "lib/index.js": "export { h } from './helpers';\n",
        }
        graph = DependencyExtractor.extract_from_sources(files)
        assert any(
            e.source_file == "lib/index.js" and e.target_file == "lib/helpers.js"
            for e in graph.edges
        )

    def test_go_intra_repo_package_import_resolves(self):
        files = {
            "myrepo/pkg/util/util.go": "package util\n\nfunc U() {}\n",
            "myrepo/main.go": (
                'package main\n\nimport "myrepo/pkg/util"\n\nfunc main() { util.U() }\n'
            ),
        }
        graph = DependencyExtractor.extract_from_sources(files)
        assert any(
            e.source_file == "myrepo/main.go"
            and e.target_file == "myrepo/pkg/util/util.go"
            for e in graph.edges
        )
