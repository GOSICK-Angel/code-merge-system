"""Tests for the PR-1 pure setup functions.

Covers:
- ``apply_setup_payload``: writes ``.merge/config.yaml`` + ``.env``,
  overlays global defaults, honours explicit threshold overrides,
  sets the GitHub block only when the token is supplied.
- ``build_default_payload``: picks env-var keys + git-derived
  branches without prompting, suitable for ``merge --ci`` first-run.
- ``detect_setup_context``: returns ``has_existing_config=True`` once
  the file is on disk, masks already-stored API keys, and degrades
  cleanly when git isn't available.
- ``SetupPayload`` / ``ThresholdsPayload`` pydantic validation
  (out-of-range thresholds, missing required fields).
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from src.cli.commands.setup import (
    apply_setup_payload,
    build_default_payload,
    detect_setup_context,
)
from src.models.setup import SetupPayload, ThresholdsPayload


@pytest.fixture(autouse=True)
def _clean_api_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wipe API-key env vars before every test.

    ``apply_setup_payload`` calls ``os.environ.setdefault`` so a prior
    test that supplied a key leaks it into the process env and pollutes
    later ``detect_setup_context`` / ``build_default_payload`` assertions.
    """
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GITHUB_TOKEN"):
        monkeypatch.delenv(name, raising=False)


def _payload(**overrides: object) -> SetupPayload:
    defaults: dict[str, object] = {
        "target_branch": "upstream/main",
        "fork_ref": "feat/x",
        "project_context": "",
        "api_keys": {},
    }
    defaults.update(overrides)
    return SetupPayload.model_validate(defaults)


class TestSetupPayloadValidation:
    def test_missing_target_branch_rejected(self) -> None:
        with pytest.raises(Exception):
            SetupPayload.model_validate({"fork_ref": "feat/x", "project_context": ""})

    def test_threshold_out_of_range_rejected(self) -> None:
        with pytest.raises(Exception):
            ThresholdsPayload.model_validate({"auto_merge_confidence": 1.5})

    def test_threshold_none_accepted(self) -> None:
        # All-None payload means "use defaults" — never an error.
        t = ThresholdsPayload()
        assert t.auto_merge_confidence is None


class TestApplySetupPayload:
    def test_writes_config_and_env(self, tmp_path: Path) -> None:
        payload = _payload(
            project_context="dify fork",
            api_keys={
                "ANTHROPIC_API_KEY": "sk-ant-test",
                "OPENAI_API_KEY": "sk-oa-test",
            },
        )

        with patch.dict(os.environ, {}, clear=False):
            config = apply_setup_payload(payload, str(tmp_path))

        cfg_path = tmp_path / ".merge" / "config.yaml"
        env_path = tmp_path / ".merge" / ".env"
        assert cfg_path.exists()
        assert env_path.exists()

        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        assert raw["upstream_ref"] == "upstream/main"
        assert raw["fork_ref"] == "feat/x"
        assert raw["project_context"] == "dify fork"
        assert raw["thresholds"]["auto_merge_confidence"] == 0.85
        assert config.upstream_ref == "upstream/main"

        env_text = env_path.read_text(encoding="utf-8")
        assert "ANTHROPIC_API_KEY" in env_text
        assert "OPENAI_API_KEY" in env_text
        # GITHUB block only set when token supplied — not here.
        assert "github" not in raw

    def test_github_block_set_when_token_supplied(self, tmp_path: Path) -> None:
        payload = _payload(api_keys={"GITHUB_TOKEN": "ghp_xxx"})
        apply_setup_payload(payload, str(tmp_path))
        raw = yaml.safe_load(
            (tmp_path / ".merge" / "config.yaml").read_text(encoding="utf-8")
        )
        assert raw["github"] == {"enabled": True, "token_env": "GITHUB_TOKEN"}

    def test_explicit_thresholds_beat_defaults(self, tmp_path: Path) -> None:
        payload = _payload(
            thresholds=ThresholdsPayload(
                auto_merge_confidence=0.95, risk_score_high=0.5
            )
        )
        apply_setup_payload(payload, str(tmp_path))
        raw = yaml.safe_load(
            (tmp_path / ".merge" / "config.yaml").read_text(encoding="utf-8")
        )
        assert raw["thresholds"]["auto_merge_confidence"] == 0.95
        assert raw["thresholds"]["risk_score_high"] == 0.5
        # unchanged ones keep defaults
        assert raw["thresholds"]["risk_score_low"] == 0.30

    def test_no_api_keys_skips_env_file(self, tmp_path: Path) -> None:
        payload = _payload(api_keys={})
        apply_setup_payload(payload, str(tmp_path))
        assert not (tmp_path / ".merge" / ".env").exists()


class TestBuildDefaultPayload:
    def test_picks_env_keys_and_falls_back_branch(self, tmp_path: Path) -> None:
        with patch.dict(
            os.environ,
            {"ANTHROPIC_API_KEY": "sk-x", "OPENAI_API_KEY": "sk-y"},
            clear=False,
        ):
            with (
                patch(
                    "src.cli.commands.setup._auto_detect_fork_ref",
                    return_value="feat/ci-run",
                ),
                patch(
                    "src.cli.commands.setup._detect_upstream_default",
                    return_value="origin/main",
                ),
            ):
                payload = build_default_payload(str(tmp_path))

        assert payload.target_branch == "origin/main"
        assert payload.fork_ref == "feat/ci-run"
        assert "ANTHROPIC_API_KEY" in payload.api_keys
        assert "OPENAI_API_KEY" in payload.api_keys
        assert "GITHUB_TOKEN" not in payload.api_keys
        assert payload.thresholds is None
        assert payload.dry_run is False


class TestDetectSetupContext:
    def test_no_config_yet(self, tmp_path: Path) -> None:
        with (
            patch(
                "src.cli.commands.setup._auto_detect_fork_ref",
                return_value="feat/x",
            ),
            patch(
                "src.cli.commands.setup._detect_upstream_default",
                return_value="origin/main",
            ),
            patch(
                "src.cli.commands.setup._count_fork_deleted_files",
                return_value=42,
            ),
        ):
            ctx = detect_setup_context(str(tmp_path))

        assert ctx.has_existing_config is False
        assert ctx.existing_config_summary is None
        assert ctx.current_branch == "feat/x"
        assert ctx.suggested_target == "origin/main"
        assert ctx.fork_divergence_count == 42
        assert ctx.forks_profile_threshold == 30

    def test_summarises_existing_config(self, tmp_path: Path) -> None:
        payload = _payload(
            project_context="existing run",
            api_keys={"ANTHROPIC_API_KEY": "sk-a"},
        )
        apply_setup_payload(payload, str(tmp_path))

        with (
            patch(
                "src.cli.commands.setup._auto_detect_fork_ref",
                return_value="feat/x",
            ),
            patch(
                "src.cli.commands.setup._detect_upstream_default",
                return_value="origin/main",
            ),
            patch(
                "src.cli.commands.setup._count_fork_deleted_files",
                return_value=0,
            ),
        ):
            ctx = detect_setup_context(str(tmp_path))

        assert ctx.has_existing_config is True
        assert ctx.existing_config_summary is not None
        assert ctx.existing_config_summary["upstream_ref"] == "upstream/main"
        assert ctx.existing_config_summary["project_context"] == "existing run"

    def test_api_key_hint_priority_shell_beats_project_env(
        self, tmp_path: Path
    ) -> None:
        # Pre-create a project .env so apply leaves a file on disk.
        apply_setup_payload(
            _payload(api_keys={"OPENAI_API_KEY": "sk-from-file"}),
            str(tmp_path),
        )

        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "sk-from-shell"}, clear=False),
            patch(
                "src.cli.commands.setup._auto_detect_fork_ref",
                return_value="feat/x",
            ),
            patch(
                "src.cli.commands.setup._detect_upstream_default",
                return_value="origin/main",
            ),
            patch(
                "src.cli.commands.setup._count_fork_deleted_files",
                return_value=0,
            ),
        ):
            ctx = detect_setup_context(str(tmp_path))

        by_name = {h.name: h for h in ctx.api_key_hints}
        # shell wins over project_env
        assert by_name["OPENAI_API_KEY"].source == "shell"
        # masked is not the raw value
        assert by_name["OPENAI_API_KEY"].masked != "sk-from-shell"
        assert "sk-" in by_name["OPENAI_API_KEY"].masked

        # An env var not set anywhere has empty masked + source
        assert by_name["GITHUB_TOKEN"].masked == ""
        assert by_name["GITHUB_TOKEN"].source == ""
