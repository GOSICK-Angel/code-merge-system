"""Regression for the ``**/`` glob fix in PlannerAgent._matches_layer.

Python's stdlib ``fnmatch`` does not treat ``**`` as a directory
wildcard — it is just another ``*``. The L1 ``dependencies`` layer
patterns (``**/go.mod``, ``**/package.json``, ``**/*.lock``, …) therefore
matched ``sub/dir/go.mod`` but NOT the root-level ``go.mod``. The fix
extends ``_matches_layer`` to also try the trailing tail of a ``**/`` —
prefixed pattern against the file path, so root-level dep manifests
land in L1 instead of falling through to the L2 catch-all.
"""

from __future__ import annotations

from unittest.mock import patch

from src.agents.planner_agent import PlannerAgent
from src.models.config import AgentLLMConfig


def _planner() -> PlannerAgent:
    with patch("src.llm.client.LLMClientFactory.create"):
        return PlannerAgent(AgentLLMConfig())


L1_PATTERNS = [
    "**/pyproject.toml",
    "**/package.json",
    "**/Cargo.toml",
    "**/go.mod",
    "**/go.sum",
    "**/pom.xml",
    "**/build.gradle",
    "**/build.gradle.kts",
    "**/*.lock",
    "**/package-lock.json",
    "**/pnpm-lock.yaml",
    "**/npm-shrinkwrap.json",
    "**/requirements*.txt",
]

L0_PATTERNS = [
    ".github/**",
    ".gitlab/**",
    "ci/**",
    "docker/**",
    "Makefile",
    ".gitignore",
    ".dockerignore",
    ".editorconfig",
]


class TestRootLevelDoubleStarMatch:
    """`**/foo` must match BOTH `foo` (root) and `sub/foo` (nested)."""

    def test_root_go_mod_matches_l1(self):
        p = _planner()
        assert p._matches_layer("go.mod", L1_PATTERNS) is True

    def test_root_go_sum_matches_l1(self):
        p = _planner()
        assert p._matches_layer("go.sum", L1_PATTERNS) is True

    def test_root_package_json_matches_l1(self):
        p = _planner()
        assert p._matches_layer("package.json", L1_PATTERNS) is True

    def test_root_package_lock_json_matches_l1(self):
        p = _planner()
        assert p._matches_layer("package-lock.json", L1_PATTERNS) is True

    def test_root_pyproject_matches_l1(self):
        p = _planner()
        assert p._matches_layer("pyproject.toml", L1_PATTERNS) is True

    def test_nested_dep_still_matches_l1(self):
        # Regression guard: the fix must not break already-working
        # `sub/<file>` paths.
        p = _planner()
        assert p._matches_layer("services/api/go.mod", L1_PATTERNS) is True
        assert p._matches_layer("web/package.json", L1_PATTERNS) is True

    def test_root_lockfile_matches_via_glob(self):
        # `**/*.lock` against root `poetry.lock`.
        p = _planner()
        assert p._matches_layer("poetry.lock", L1_PATTERNS) is True

    def test_non_dep_root_file_does_not_match_l1(self):
        # Negative case: random root-level file must NOT be pulled
        # into L1 just because the new branch trims `**/`.
        p = _planner()
        assert p._matches_layer("README.md", L1_PATTERNS) is False
        assert p._matches_layer("main.py", L1_PATTERNS) is False


class TestL0InfraStillMatches:
    """Sanity: the L0 (infrastructure) patterns are unaffected by the
    fix — they already worked because the patterns don't start with
    ``**/``.
    """

    def test_github_workflow_matches_l0(self):
        p = _planner()
        assert p._matches_layer(".github/workflows/ci.yml", L0_PATTERNS) is True

    def test_makefile_matches_l0(self):
        p = _planner()
        assert p._matches_layer("Makefile", L0_PATTERNS) is True

    def test_dockerfile_matches_l0(self):
        p = _planner()
        assert p._matches_layer("docker/Dockerfile", L0_PATTERNS) is True

    def test_random_source_does_not_match_l0(self):
        p = _planner()
        assert p._matches_layer("src/main.py", L0_PATTERNS) is False


class TestLayerSpecificityOrdering:
    """End-to-end check via the public assignment surface: with default
    layers in place, root-level go.mod must end up in layer_id=1, not
    in the L2 catch-all bucket."""

    def test_root_deps_go_to_l1_not_l2(self):
        from src.models.plan import DEFAULT_LAYERS, MergeLayer

        layers = [MergeLayer(**ly) for ly in DEFAULT_LAYERS]
        p = _planner()

        file_paths = [
            "go.mod",
            "go.sum",
            "package.json",
            "package-lock.json",
            "src/main.go",
        ]
        assigned = p._assign_files_to_layers(file_paths, layers)

        l1_files = set(assigned.get(1, []))
        l2_files = set(assigned.get(2, []))

        assert {"go.mod", "go.sum", "package.json", "package-lock.json"} <= l1_files
        assert "src/main.go" in l2_files
        assert "go.mod" not in l2_files
