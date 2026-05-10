"""P1-4: structured-config field-level sensitivity unit tests.

Verifies the generic mechanism — no project-specific knowledge baked
in. Rules are pure config; tests use synthetic schemas.
"""

from __future__ import annotations

from src.models.config import FieldSensitivityRule
from src.models.diff import RiskLevel
from src.tools.field_sensitivity import (
    compute_changed_fields,
    evaluate,
    field_path_matches,
    flatten_field_paths,
    is_at_least,
    parse_structured,
)


# --------------------------------------------------------------------------
# parse_structured
# --------------------------------------------------------------------------


def test_parse_structured_yaml() -> None:
    out = parse_structured("a: 1\nb: [x, y]\n", "config.yaml")
    assert out == {"a": 1, "b": ["x", "y"]}


def test_parse_structured_json() -> None:
    out = parse_structured('{"a": 1, "b": [true, false]}', "config.json")
    assert out == {"a": 1, "b": [True, False]}


def test_parse_structured_unknown_extension_returns_none() -> None:
    assert parse_structured("a: 1", "config.toml") is None


def test_parse_structured_malformed_returns_none() -> None:
    assert parse_structured("[oops: this", "broken.yaml") is None


def test_parse_structured_none_input_returns_none() -> None:
    assert parse_structured(None, "x.yaml") is None


# --------------------------------------------------------------------------
# flatten_field_paths
# --------------------------------------------------------------------------


def test_flatten_flat_dict() -> None:
    assert flatten_field_paths({"a": 1, "b": "x"}) == {"a": 1, "b": "x"}


def test_flatten_primitive_list_collapses_to_sorted_tuple() -> None:
    """List of primitives lives at the *parent* key as a sorted tuple,
    so adding or removing an element produces a real value-diff."""
    out = flatten_field_paths({"oauth": {"scopes": ["write", "read"]}})
    assert out == {"oauth.scopes": ("'read'", "'write'")}


def test_flatten_array_of_objects_uses_star() -> None:
    out = flatten_field_paths(
        {"endpoints": [{"url": "a"}, {"url": "b"}, {"method": "POST"}]}
    )
    # Object arrays still descend with .* — first-wins per child key
    # is acceptable here because changes propagate via the dict diff.
    assert out["endpoints.*.url"] == "a"
    assert out["endpoints.*.method"] == "POST"


# --------------------------------------------------------------------------
# compute_changed_fields
# --------------------------------------------------------------------------


def test_compute_changed_detects_value_change() -> None:
    base = {"a": 1, "b": 2}
    target = {"a": 1, "b": 3}
    assert compute_changed_fields(base, target) == {"b"}


def test_compute_changed_detects_added_and_removed() -> None:
    base = {"a": 1}
    target = {"b": 2}
    assert compute_changed_fields(base, target) == {"a", "b"}


def test_compute_changed_no_diff_returns_empty() -> None:
    obj = {"a": 1, "b": [1, 2, 3]}
    assert compute_changed_fields(obj, obj) == set()


def test_compute_changed_handles_one_side_none() -> None:
    target = {"a": 1}
    assert compute_changed_fields(None, target) == {"a"}
    assert compute_changed_fields(target, None) == {"a"}


# --------------------------------------------------------------------------
# field_path_matches
# --------------------------------------------------------------------------


def test_field_path_matches_exact() -> None:
    assert field_path_matches("oauth.scopes", "oauth.scopes")
    assert not field_path_matches("oauth.scopes", "oauth.tokens")


def test_field_path_matches_wildcard() -> None:
    assert field_path_matches("permissions.read", "permissions.*")
    assert field_path_matches("permissions.write", "permissions.*")
    assert not field_path_matches("permissions", "permissions.*")


def test_field_path_matches_array_path() -> None:
    assert field_path_matches("endpoints.*.url", "endpoints.*.url")
    assert field_path_matches("endpoints.*.url", "endpoints.*.*")


# --------------------------------------------------------------------------
# evaluate (end-to-end)
# --------------------------------------------------------------------------


def _rule(
    path_glob: str, fields: list[str], level: str = "auto_risky"
) -> FieldSensitivityRule:
    return FieldSensitivityRule(
        path_glob=path_glob, sensitive_fields=fields, escalate_to=level
    )


def test_evaluate_returns_none_when_rules_empty() -> None:
    assert evaluate("a.yaml", "a: 1", "a: 2", []) is None


def test_evaluate_returns_none_when_path_does_not_match() -> None:
    rules = [_rule("**/manifest.yaml", ["scopes"])]
    assert evaluate("config.yaml", "scopes: [a]", "scopes: [b]", rules) is None


def test_evaluate_returns_none_when_field_unchanged() -> None:
    rules = [_rule("**/manifest.yaml", ["scopes"])]
    assert (
        evaluate(
            "plugin/manifest.yaml",
            "scopes: [a]\nname: foo\n",
            "scopes: [a]\nname: bar\n",
            rules,
        )
        is None
    )


def test_evaluate_fires_on_sensitive_field_change() -> None:
    rules = [_rule("**/manifest.yaml", ["scopes"])]
    out = evaluate(
        "plugin/manifest.yaml",
        "scopes: [read]\n",
        "scopes: [read, admin]\n",
        rules,
    )
    assert out == RiskLevel.AUTO_RISKY


def test_evaluate_fires_on_nested_oauth_scopes() -> None:
    rules = [_rule("**/manifest.yaml", ["oauth.scopes"])]
    out = evaluate(
        "plugin/manifest.yaml",
        "oauth:\n  scopes: [read]\n",
        "oauth:\n  scopes: [read, write]\n",
        rules,
    )
    assert out == RiskLevel.AUTO_RISKY


def test_evaluate_picks_strictest_level() -> None:
    rules = [
        _rule("**/manifest.yaml", ["scopes"], "auto_risky"),
        _rule("**/manifest.yaml", ["scopes"], "human_required"),
    ]
    out = evaluate("plugin/manifest.yaml", "scopes: [a]", "scopes: [b]", rules)
    assert out == RiskLevel.HUMAN_REQUIRED


def test_evaluate_skips_unparseable_content() -> None:
    rules = [_rule("**/manifest.yaml", ["scopes"])]
    assert evaluate("plugin/manifest.yaml", "[oops", "[also oops", rules) is None


def test_evaluate_works_when_only_target_exists() -> None:
    """New file from upstream — base is None."""
    rules = [_rule("**/manifest.yaml", ["scopes"])]
    out = evaluate("plugin/manifest.yaml", None, "scopes: [a]\n", rules)
    assert out == RiskLevel.AUTO_RISKY


def test_evaluate_json_file() -> None:
    rules = [_rule("**/*.json", ["permissions.*"])]
    out = evaluate(
        "plugin/spec.json",
        '{"permissions": {"read": true}}',
        '{"permissions": {"read": true, "write": true}}',
        rules,
    )
    assert out == RiskLevel.AUTO_RISKY


# --------------------------------------------------------------------------
# is_at_least
# --------------------------------------------------------------------------


def test_is_at_least() -> None:
    assert is_at_least(RiskLevel.AUTO_RISKY, RiskLevel.AUTO_SAFE)
    assert is_at_least(RiskLevel.HUMAN_REQUIRED, RiskLevel.AUTO_RISKY)
    assert not is_at_least(RiskLevel.AUTO_SAFE, RiskLevel.AUTO_RISKY)
    assert not is_at_least(None, RiskLevel.AUTO_SAFE)


# --------------------------------------------------------------------------
# initialize.py wiring: _apply_field_sensitivity escalates only upward
# --------------------------------------------------------------------------


def test_apply_field_sensitivity_escalates_matched_file() -> None:
    from unittest.mock import MagicMock

    from src.core.phases.initialize import _apply_field_sensitivity
    from src.models.diff import FileChangeCategory, FileDiff, FileStatus

    fd_match = FileDiff(
        file_path="plugin/manifest.yaml",
        file_status=FileStatus.MODIFIED,
        risk_level=RiskLevel.AUTO_SAFE,
        risk_score=0.1,
        lines_added=2,
        lines_deleted=1,
        lines_changed=3,
        conflict_count=0,
        hunks=[],
        is_security_sensitive=False,
        change_category=FileChangeCategory.C,
    )
    fd_skip = fd_match.model_copy(update={"file_path": "src/foo.py"})
    rules = [
        FieldSensitivityRule(
            path_glob="**/manifest.yaml",
            sensitive_fields=["scopes"],
            escalate_to="auto_risky",
        )
    ]

    ctx = MagicMock()
    ctx.git_tool.get_file_content.side_effect = lambda ref, path: {
        ("base", "plugin/manifest.yaml"): "scopes: [read]\n",
        ("upstream", "plugin/manifest.yaml"): "scopes: [read, admin]\n",
    }.get((ref, path))

    out = _apply_field_sensitivity([fd_match, fd_skip], rules, ctx, "base", "upstream")
    by_path = {fd.file_path: fd for fd in out}
    assert by_path["plugin/manifest.yaml"].risk_level == RiskLevel.AUTO_RISKY
    assert by_path["src/foo.py"].risk_level == RiskLevel.AUTO_SAFE


def test_apply_field_sensitivity_never_demotes() -> None:
    from unittest.mock import MagicMock

    from src.core.phases.initialize import _apply_field_sensitivity
    from src.models.diff import FileChangeCategory, FileDiff, FileStatus

    fd = FileDiff(
        file_path="plugin/manifest.yaml",
        file_status=FileStatus.MODIFIED,
        risk_level=RiskLevel.HUMAN_REQUIRED,
        risk_score=0.5,
        lines_added=1,
        lines_deleted=0,
        lines_changed=1,
        conflict_count=0,
        hunks=[],
        is_security_sensitive=True,
        change_category=FileChangeCategory.C,
    )
    rules = [
        FieldSensitivityRule(
            path_glob="**/manifest.yaml",
            sensitive_fields=["scopes"],
            escalate_to="auto_risky",
        )
    ]

    ctx = MagicMock()
    ctx.git_tool.get_file_content.side_effect = lambda ref, path: (
        "scopes: [read]\n" if ref == "base" else "scopes: [read, write]\n"
    )

    out = _apply_field_sensitivity([fd], rules, ctx, "base", "upstream")
    # auto_risky < human_required → must stay human_required.
    assert out[0].risk_level == RiskLevel.HUMAN_REQUIRED


def test_apply_field_sensitivity_skips_when_no_path_match() -> None:
    from unittest.mock import MagicMock

    from src.core.phases.initialize import _apply_field_sensitivity
    from src.models.diff import FileChangeCategory, FileDiff, FileStatus

    fd = FileDiff(
        file_path="src/main.py",
        file_status=FileStatus.MODIFIED,
        risk_level=RiskLevel.AUTO_SAFE,
        risk_score=0.1,
        lines_added=1,
        lines_deleted=0,
        lines_changed=1,
        conflict_count=0,
        hunks=[],
        is_security_sensitive=False,
        change_category=FileChangeCategory.C,
    )
    rules = [
        FieldSensitivityRule(
            path_glob="**/manifest.yaml",
            sensitive_fields=["scopes"],
            escalate_to="auto_risky",
        )
    ]
    ctx = MagicMock()

    out = _apply_field_sensitivity([fd], rules, ctx, "base", "upstream")
    # Path-glob miss: must NOT touch git, must NOT modify the diff.
    ctx.git_tool.get_file_content.assert_not_called()
    assert out[0] is fd
