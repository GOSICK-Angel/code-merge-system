"""Tests for global config defaults applied during setup.

Covers:
- ``_load_global_defaults``: missing file / malformed yaml / whitelist filter
- ``_deep_merge_dicts``: nested dict merge, scalar replace, new keys
- ``apply_setup_payload``: global defaults seed a new project yaml
- ``apply_setup_payload``: explicit payload thresholds beat global defaults
- ``_ask`` / ``_confirm``: readline-safe prompt rendering (still used by
  ``init_context.py`` even after the terminal setup wizard moved into
  the browser).
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from src.cli.commands.setup import (
    _ask,
    _confirm,
    _deep_merge_dicts,
    _load_global_defaults,
    apply_setup_payload,
)
from src.models.setup import ProviderConfig, SetupPayload, ThresholdsPayload


@pytest.fixture(autouse=True)
def _clean_api_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wipe API-key env vars so ``apply_setup_payload``'s ``setdefault``
    doesn't leak between tests."""
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GITHUB_TOKEN"):
        monkeypatch.delenv(name, raising=False)


_TEST_MODELS = ["claude-opus-4-7", "claude-haiku-4-5-20251001"]


def _payload(**overrides: object) -> SetupPayload:
    defaults: dict[str, object] = {
        "target_branch": "upstream/main",
        "fork_ref": "feature/x",
        "project_context": "",
        "anthropic": ProviderConfig(
            enabled=True, api_key="sk-ant", models=list(_TEST_MODELS)
        ),
    }
    defaults.update(overrides)
    return SetupPayload.model_validate(defaults)


class TestLoadGlobalDefaults:
    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        with patch(
            "src.cli.commands.setup.get_global_config_path",
            return_value=tmp_path / "nope.yaml",
        ):
            assert _load_global_defaults() == {}

    def test_malformed_yaml_returns_empty(self, tmp_path: Path) -> None:
        bad = tmp_path / "config.yaml"
        bad.write_text("agents: [unbalanced\n", encoding="utf-8")
        with patch("src.cli.commands.setup.get_global_config_path", return_value=bad):
            assert _load_global_defaults() == {}

    def test_non_dict_root_returns_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        path.write_text("- just-a-list\n", encoding="utf-8")
        with patch("src.cli.commands.setup.get_global_config_path", return_value=path):
            assert _load_global_defaults() == {}

    def test_whitelist_keeps_allowed(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        path.write_text(
            yaml.dump(
                {
                    "agents": {"planner_judge": {"model": "gpt-5.4"}},
                    "thresholds": {"auto_merge_confidence": 0.92},
                    "max_files_per_run": 100,
                    "max_plan_revision_rounds": 3,
                    "llm": {"provider": "anthropic"},
                    "output": {"directory": "./custom-out"},
                }
            ),
            encoding="utf-8",
        )
        with patch("src.cli.commands.setup.get_global_config_path", return_value=path):
            result = _load_global_defaults()
        assert set(result) == {
            "agents",
            "thresholds",
            "max_files_per_run",
            "max_plan_revision_rounds",
            "llm",
            "output",
        }
        assert result["agents"]["planner_judge"]["model"] == "gpt-5.4"
        assert result["max_files_per_run"] == 100

    def test_whitelist_drops_disallowed(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        path.write_text(
            yaml.dump(
                {
                    "agents": {"planner_judge": {"model": "gpt-5.4"}},
                    "fork_ref": "evil/override",
                    "repo_path": "/tmp/no",
                    "github": {"enabled": True},
                }
            ),
            encoding="utf-8",
        )
        with patch("src.cli.commands.setup.get_global_config_path", return_value=path):
            result = _load_global_defaults()
        assert set(result) == {"agents"}
        assert "fork_ref" not in result
        assert "repo_path" not in result
        assert "github" not in result


class TestDeepMergeDicts:
    def test_overlay_replaces_scalar(self) -> None:
        merged = _deep_merge_dicts({"a": 1}, {"a": 2})
        assert merged == {"a": 2}

    def test_overlay_adds_new_keys(self) -> None:
        merged = _deep_merge_dicts({"a": 1}, {"b": 2})
        assert merged == {"a": 1, "b": 2}

    def test_nested_dicts_are_merged(self) -> None:
        merged = _deep_merge_dicts(
            {"agents": {"planner": {"model": "claude"}, "judge": {"model": "j1"}}},
            {"agents": {"planner": {"model": "opus"}}},
        )
        assert merged == {
            "agents": {
                "planner": {"model": "opus"},
                "judge": {"model": "j1"},
            }
        }

    def test_lists_are_replaced_not_merged(self) -> None:
        merged = _deep_merge_dicts(
            {"output": {"formats": ["json", "md"]}},
            {"output": {"formats": ["yaml"]}},
        )
        assert merged == {"output": {"formats": ["yaml"]}}

    def test_input_not_mutated(self) -> None:
        base = {"a": {"b": 1}}
        overlay = {"a": {"c": 2}}
        _deep_merge_dicts(base, overlay)
        assert base == {"a": {"b": 1}}
        assert overlay == {"a": {"c": 2}}


class TestApplySetupPayloadGlobals:
    """Global defaults overlay applies on top of ``_default_config_data``.

    ``apply_setup_payload`` is the post-PR-3 single entry point both
    the Web UI and ``merge --ci`` first-run go through, so these tests
    have replaced the legacy ``_interactive_setup`` coverage with
    payload-driven equivalents — no monkeypatched input() needed.
    """

    def test_no_global_yaml_keeps_factory_defaults(self, tmp_path: Path) -> None:
        with patch(
            "src.cli.commands.setup.get_global_config_path",
            return_value=tmp_path / "missing.yaml",
        ):
            cfg = apply_setup_payload(_payload(), str(tmp_path))

        assert cfg.thresholds.auto_merge_confidence == 0.85
        written = yaml.safe_load(
            (tmp_path / ".merge" / "config.yaml").read_text(encoding="utf-8")
        )
        # Single-provider payload (anthropic only) — every agent
        # without an override lands on default_provider.models[0],
        # which is the first entry in the textarea-derived list.
        assert written["agents"]["planner_judge"]["provider"] == "anthropic"
        assert written["agents"]["planner_judge"]["model"] == _TEST_MODELS[0]
        assert written["agents"]["human_interface"]["model"] == _TEST_MODELS[0]

    def test_global_yaml_overrides_hardcoded_model(self, tmp_path: Path) -> None:
        global_path = tmp_path / "global_config.yaml"
        global_path.write_text(
            yaml.dump(
                {
                    "agents": {"planner_judge": {"model": "gpt-5.4-custom"}},
                    "thresholds": {"auto_merge_confidence": 0.92},
                }
            ),
            encoding="utf-8",
        )
        with patch(
            "src.cli.commands.setup.get_global_config_path",
            return_value=global_path,
        ):
            cfg = apply_setup_payload(_payload(), str(tmp_path))

        written = yaml.safe_load(
            (tmp_path / ".merge" / "config.yaml").read_text(encoding="utf-8")
        )
        assert written["agents"]["planner_judge"]["model"] == "gpt-5.4-custom"
        assert cfg.agents.planner_judge.model == "gpt-5.4-custom"
        assert written["thresholds"]["auto_merge_confidence"] == 0.92
        assert cfg.thresholds.auto_merge_confidence == 0.92

    def test_explicit_payload_threshold_beats_global(self, tmp_path: Path) -> None:
        global_path = tmp_path / "global_config.yaml"
        global_path.write_text(
            yaml.dump({"thresholds": {"auto_merge_confidence": 0.92}}),
            encoding="utf-8",
        )
        payload = _payload(
            thresholds=ThresholdsPayload(
                auto_merge_confidence=0.99,
                risk_score_low=0.10,
                risk_score_high=0.50,
            )
        )
        with patch(
            "src.cli.commands.setup.get_global_config_path",
            return_value=global_path,
        ):
            cfg = apply_setup_payload(payload, str(tmp_path))

        assert cfg.thresholds.auto_merge_confidence == 0.99
        assert cfg.thresholds.risk_score_low == 0.10
        assert cfg.thresholds.risk_score_high == 0.50

    def test_global_disallowed_keys_dropped(self, tmp_path: Path) -> None:
        global_path = tmp_path / "global_config.yaml"
        global_path.write_text(
            yaml.dump(
                {
                    "fork_ref": "global/should/not/win",
                    "repo_path": "/tmp/should-not-win",
                    "agents": {"planner_judge": {"model": "gpt-5.4-custom"}},
                }
            ),
            encoding="utf-8",
        )
        with patch(
            "src.cli.commands.setup.get_global_config_path",
            return_value=global_path,
        ):
            cfg = apply_setup_payload(_payload(), str(tmp_path))

        # payload's fork_ref + repo_path win — global non-whitelisted
        # keys were filtered out before the deep-merge.
        assert cfg.fork_ref == "feature/x"
        assert cfg.repo_path == str(tmp_path)
        written = yaml.safe_load(
            (tmp_path / ".merge" / "config.yaml").read_text(encoding="utf-8")
        )
        assert written["fork_ref"] == "feature/x"
        assert written["repo_path"] == str(tmp_path)
        assert written["agents"]["planner_judge"]["model"] == "gpt-5.4-custom"


class TestAskAndConfirmReadlineSafe:
    """Regression: readline must see the rendered prompt as the input() arg.

    These helpers are still used by ``init_context.py`` post-PR-3.
    """

    def test_ask_passes_rendered_prompt_to_input(self) -> None:
        with patch("builtins.input", return_value="hello") as mock_input:
            result = _ask("Project description", default="", show_default=False)
        mock_input.assert_called_once_with("Project description: ")
        assert result == "hello"

    def test_ask_renders_default_when_show_default(self) -> None:
        with patch("builtins.input", return_value="") as mock_input:
            result = _ask("auto_merge_confidence", default="0.85")
        mock_input.assert_called_once_with("auto_merge_confidence (0.85): ")
        assert result == "0.85"

    def test_ask_returns_default_on_eof(self) -> None:
        with patch("builtins.input", side_effect=EOFError):
            assert _ask("X", default="fallback") == "fallback"

    def test_confirm_renders_yes_default(self) -> None:
        with patch("builtins.input", return_value="") as mock_input:
            result = _confirm("Use defaults?", default=True)
        mock_input.assert_called_once_with("Use defaults? [Y/n]: ")
        assert result is True

    def test_confirm_renders_no_default(self) -> None:
        with patch("builtins.input", return_value="") as mock_input:
            result = _confirm("Overwrite?", default=False)
        mock_input.assert_called_once_with("Overwrite? [y/N]: ")
        assert result is False

    def test_confirm_parses_y_and_n(self) -> None:
        with patch("builtins.input", return_value="y"):
            assert _confirm("X", default=False) is True
        with patch("builtins.input", return_value="N"):
            assert _confirm("X", default=True) is False

    def test_confirm_reprompts_on_invalid(self) -> None:
        with patch("builtins.input", side_effect=["?", "wat", "y"]) as mock_input:
            assert _confirm("X", default=True) is True
        assert mock_input.call_count == 3


# Silence "unused import" if os import lands on test order shuffle.
_ = os
