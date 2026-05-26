"""Unit tests for the MergeConfig → UI schema introspection.

Covers the four classification buckets (primitive / enum / list_str /
object), the yaml fallback for complex containers, the self-reference
cycle guard, curated-by-path tagging, and JSON-serializability of the
whole tree."""

from __future__ import annotations

import json

from src.web.config_schema import ConfigFieldNode, build_config_schema


def _find(root: ConfigFieldNode, dotted: str) -> ConfigFieldNode:
    node = root
    for part in dotted.split("."):
        match = next((c for c in node.children if c.name == part), None)
        assert match is not None, f"missing {part!r} while resolving {dotted!r}"
        node = match
    return node


class TestSchemaShape:
    def test_root_is_object_with_top_level_fields(self) -> None:
        root = build_config_schema()
        assert root.kind == "object"
        names = {c.name for c in root.children}
        assert {
            "upstream_ref",
            "fork_ref",
            "max_files_per_run",
            "thresholds",
            "file_classifier",
            "dependency_graph",
            "agents",
        } <= names

    def test_required_ref_has_no_default(self) -> None:
        node = _find(build_config_schema(), "upstream_ref")
        assert node.kind == "str"
        assert node.required is True
        assert node.default is None

    def test_int_field_carries_bound_and_default(self) -> None:
        node = _find(build_config_schema(), "max_files_per_run")
        assert node.kind == "int"
        assert node.default == 500
        assert node.minimum == 1.0

    def test_float_field_carries_both_bounds(self) -> None:
        node = _find(build_config_schema(), "dependency_graph.god_node_risk_bump")
        assert node.kind == "float"
        assert node.default == 0.15
        assert node.minimum == 0.0
        assert node.maximum == 1.0

    def test_literal_field_is_enum(self) -> None:
        node = _find(build_config_schema(), "llm_assist.mode")
        assert node.kind == "enum"
        assert node.enum == ["off", "auto", "always"]
        assert node.default == "auto"

    def test_list_of_str_is_list_str(self) -> None:
        node = _find(build_config_schema(), "file_classifier.excluded_patterns")
        assert node.kind == "list_str"
        assert "**/*.lock" in node.default

    def test_nested_model_is_object_with_children(self) -> None:
        node = _find(build_config_schema(), "file_classifier.security_sensitive")
        assert node.kind == "object"
        assert node.default is None
        child_names = {c.name for c in node.children}
        assert {"patterns", "always_require_human", "risk_hint_bump"} <= child_names


class TestYamlFallback:
    def test_list_of_models_is_yaml(self) -> None:
        node = _find(build_config_schema(), "customizations")
        assert node.kind == "yaml"
        assert node.default == []

    def test_dict_field_is_yaml(self) -> None:
        node = _find(build_config_schema(), "module_config.explicit")
        assert node.kind == "yaml"

    def test_field_sensitivity_rules_is_yaml(self) -> None:
        node = _find(build_config_schema(), "file_classifier.field_sensitivity_rules")
        assert node.kind == "yaml"


class TestCycleGuard:
    def test_agent_fallback_degrades_to_yaml(self) -> None:
        planner = _find(build_config_schema(), "agents.planner")
        assert planner.kind == "object"
        fallback = next(c for c in planner.children if c.name == "fallback")
        # AgentLLMConfig.fallback is itself an AgentLLMConfig — recursing
        # would loop forever, so it must collapse to a yaml editor.
        assert fallback.kind == "yaml"

    def test_tree_is_finite_and_json_serializable(self) -> None:
        root = build_config_schema()
        # A non-terminating recursion would never reach here; json.dumps
        # also asserts every default survived _to_jsonable.
        json.dumps(root.model_dump(mode="json"))


class TestCuratedTagging:
    def test_curated_paths_and_inheritance(self) -> None:
        root = build_config_schema()
        assert _find(root, "upstream_ref").curated is True
        assert _find(root, "agents").curated is True
        # Inherited from the curated ``agents`` ancestor.
        assert _find(root, "agents.planner.model").curated is True
        assert _find(root, "thresholds.auto_merge_confidence").curated is True
        # Legacy global ``llm`` block is curated out (dead config; the editor
        # must not surface it). Children inherit the curated flag.
        assert _find(root, "llm").curated is True
        assert _find(root, "llm.max_tokens").curated is True

    def test_non_curated_fields(self) -> None:
        root = build_config_schema()
        assert _find(root, "thresholds.human_escalation").curated is False
        assert _find(root, "dependency_graph.enabled").curated is False
        assert _find(root, "max_files_per_run").curated is False
