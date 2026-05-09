"""Tests for global config defaults consumed by the first-run wizard.

Covers:
- ``_load_global_defaults``: missing file / malformed yaml / whitelist filter
- ``_deep_merge_dicts``: nested dict merge, scalar replace, new keys
- ``_interactive_setup``: global defaults seed new project yaml
- ``_interactive_setup``: explicit wizard threshold answers beat global
- ``_repeat_run_flow``: existing project yaml is NOT touched by global
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import yaml

from src.cli.commands.setup import (
    _ask,
    _confirm,
    _deep_merge_dicts,
    _load_global_defaults,
)


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


def _wizard_mocks(
    global_path: Path,
    *,
    use_defaults: bool,
    custom_thresholds: list[float] | None = None,
):
    """Stack of patches that turns ``_interactive_setup`` into a no-prompt flow."""
    return [
        patch(
            "src.cli.commands.setup.get_global_config_path",
            return_value=global_path,
        ),
        patch(
            "src.cli.commands.setup._auto_detect_fork_ref",
            return_value="feature/x",
        ),
        patch(
            "src.cli.commands.setup._resolve_api_keys",
            return_value={"ANTHROPIC_API_KEY": "ak", "OPENAI_API_KEY": "ok"},
        ),
        patch(
            "src.cli.commands.setup._prompt_api_key",
            side_effect=lambda name, existing, required: existing,
        ),
        patch(
            "src.cli.commands.setup._offer_forks_profile_draft",
            return_value=None,
        ),
        patch("src.cli.commands.setup._ask", return_value=""),
        patch("src.cli.commands.setup._confirm", return_value=use_defaults),
        patch(
            "src.cli.commands.setup._prompt_float",
            side_effect=custom_thresholds or [],
        ),
    ]


class TestInteractiveSetupAppliesGlobal:
    def test_no_global_yaml_keeps_factory_defaults(self, tmp_path: Path) -> None:
        from src.cli.commands.setup import _interactive_setup

        global_path = tmp_path / "missing.yaml"
        with (
            _wizard_mocks(global_path, use_defaults=True)[0],
            _wizard_mocks(global_path, use_defaults=True)[1],
            _wizard_mocks(global_path, use_defaults=True)[2],
            _wizard_mocks(global_path, use_defaults=True)[3],
            _wizard_mocks(global_path, use_defaults=True)[4],
            _wizard_mocks(global_path, use_defaults=True)[5],
            _wizard_mocks(global_path, use_defaults=True)[6],
        ):
            cfg = _interactive_setup("upstream/main", str(tmp_path))

        assert cfg.thresholds.auto_merge_confidence == 0.85
        written = yaml.safe_load(
            (tmp_path / ".merge" / "config.yaml").read_text(encoding="utf-8")
        )
        assert written["agents"]["planner_judge"]["model"] == "gpt-5.4"

    def test_global_yaml_overrides_hardcoded_model(self, tmp_path: Path) -> None:
        from src.cli.commands.setup import _interactive_setup

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
        mocks = _wizard_mocks(global_path, use_defaults=True)
        with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4], mocks[5], mocks[6]:
            cfg = _interactive_setup("upstream/main", str(tmp_path))

        written = yaml.safe_load(
            (tmp_path / ".merge" / "config.yaml").read_text(encoding="utf-8")
        )
        assert written["agents"]["planner_judge"]["model"] == "gpt-5.4-custom"
        assert cfg.agents.planner_judge.model == "gpt-5.4-custom"
        assert written["thresholds"]["auto_merge_confidence"] == 0.92
        assert cfg.thresholds.auto_merge_confidence == 0.92

    def test_explicit_threshold_beats_global(self, tmp_path: Path) -> None:
        from src.cli.commands.setup import _interactive_setup

        global_path = tmp_path / "global_config.yaml"
        global_path.write_text(
            yaml.dump({"thresholds": {"auto_merge_confidence": 0.92}}),
            encoding="utf-8",
        )
        mocks = _wizard_mocks(
            global_path, use_defaults=False, custom_thresholds=[0.99, 0.10, 0.50]
        )
        with (
            mocks[0],
            mocks[1],
            mocks[2],
            mocks[3],
            mocks[4],
            mocks[5],
            mocks[6],
            mocks[7],
        ):
            cfg = _interactive_setup("upstream/main", str(tmp_path))

        assert cfg.thresholds.auto_merge_confidence == 0.99
        assert cfg.thresholds.risk_score_low == 0.10
        assert cfg.thresholds.risk_score_high == 0.50

    def test_global_disallowed_keys_dropped(self, tmp_path: Path) -> None:
        from src.cli.commands.setup import _interactive_setup

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
        mocks = _wizard_mocks(global_path, use_defaults=True)
        with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4], mocks[5], mocks[6]:
            cfg = _interactive_setup("upstream/main", str(tmp_path))

        assert cfg.fork_ref == "feature/x"
        assert cfg.repo_path == str(tmp_path)
        written = yaml.safe_load(
            (tmp_path / ".merge" / "config.yaml").read_text(encoding="utf-8")
        )
        assert written["fork_ref"] == "feature/x"
        assert written["repo_path"] == str(tmp_path)
        assert written["agents"]["planner_judge"]["model"] == "gpt-5.4-custom"


class TestRepeatRunIgnoresGlobal:
    def test_existing_project_yaml_takes_priority(self, tmp_path: Path) -> None:
        from src.cli.commands.setup import detect_or_setup

        merge_dir = tmp_path / ".merge"
        merge_dir.mkdir()
        project_yaml = merge_dir / "config.yaml"
        project_yaml.write_text(
            yaml.dump(
                {
                    "upstream_ref": "upstream/main",
                    "fork_ref": "feature/locked",
                    "repo_path": str(tmp_path),
                    "agents": {
                        "planner_judge": {
                            "provider": "openai",
                            "model": "project-pinned-model",
                            "api_key_env": "OPENAI_API_KEY",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

        global_path = tmp_path / "global_config.yaml"
        global_path.write_text(
            yaml.dump(
                {"agents": {"planner_judge": {"model": "global-should-not-win"}}}
            ),
            encoding="utf-8",
        )

        with (
            patch(
                "src.cli.commands.setup.get_global_config_path",
                return_value=global_path,
            ),
            patch("src.cli.commands.setup._ask", return_value=""),
        ):
            cfg = detect_or_setup("upstream/main", str(tmp_path), reconfigure=False)

        assert cfg.agents.planner_judge.model == "project-pinned-model"


class TestAskAndConfirmReadlineSafe:
    """Regression: readline must see the rendered prompt as the input() arg.

    If the prompt is pre-printed and ``input("")`` is called (Rich's
    behaviour), Ctrl+U erases the prompt characters from the screen.
    These tests pin the contract that ``_ask``/``_confirm`` always pass
    the rendered prompt string as the *first positional argument* to
    builtin ``input``.
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
