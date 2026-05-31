"""Unit tests for target-agnostic build_check command auto-detection.

``_detect_build_check_command`` inspects a repo root for a recognised
toolchain and returns a shell command for the post-judge compile gate
(``build_check.command``). ``_default_config_data`` embeds it (enabled) when
detected, so a freshly generated ``.merge/config.yaml`` catches uncompilable
merge artifacts — the failure mode the zod test surfaced (30 TS errors → green
COMPLETED).
"""

from __future__ import annotations

import json
from pathlib import Path

from src.cli.commands.setup import (
    _default_config_data,
    _detect_build_check_command,
)
from src.models.setup import ProviderConfig, SetupPayload


def _payload(**overrides: object) -> SetupPayload:
    defaults: dict[str, object] = {
        "target_branch": "upstream/main",
        "fork_ref": "feature/x",
        "project_context": "",
        "anthropic": ProviderConfig(enabled=True, api_key="sk-ant", models=["m"]),
    }
    defaults.update(overrides)
    return SetupPayload.model_validate(defaults)


def _write_package_json(repo: Path, scripts: dict[str, str]) -> None:
    (repo / "package.json").write_text(
        json.dumps({"scripts": scripts}), encoding="utf-8"
    )


class TestDetectBuildCheckCommand:
    def test_npm_typecheck_script(self, tmp_path: Path) -> None:
        _write_package_json(tmp_path, {"typecheck": "tsc --noEmit"})
        assert _detect_build_check_command(str(tmp_path)) == "npm run typecheck"

    def test_type_check_hyphen_variant(self, tmp_path: Path) -> None:
        _write_package_json(tmp_path, {"type-check": "tsc"})
        assert _detect_build_check_command(str(tmp_path)) == "npm run type-check"

    def test_pnpm_when_lockfile_present(self, tmp_path: Path) -> None:
        _write_package_json(tmp_path, {"build": "tsc -b"})
        (tmp_path / "pnpm-lock.yaml").write_text("", encoding="utf-8")
        assert _detect_build_check_command(str(tmp_path)) == "pnpm run build"

    def test_yarn_when_lockfile_present(self, tmp_path: Path) -> None:
        _write_package_json(tmp_path, {"build": "tsc -b"})
        (tmp_path / "yarn.lock").write_text("", encoding="utf-8")
        assert _detect_build_check_command(str(tmp_path)) == "yarn run build"

    def test_typecheck_preferred_over_build(self, tmp_path: Path) -> None:
        _write_package_json(tmp_path, {"build": "vite build", "typecheck": "tsc"})
        assert _detect_build_check_command(str(tmp_path)) == "npm run typecheck"

    def test_tsconfig_fallback_when_no_useful_script(self, tmp_path: Path) -> None:
        _write_package_json(tmp_path, {"start": "node ."})
        (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")
        assert _detect_build_check_command(str(tmp_path)) == "npx tsc --noEmit"

    def test_tsconfig_without_package_json(self, tmp_path: Path) -> None:
        (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")
        assert _detect_build_check_command(str(tmp_path)) == "npx tsc --noEmit"

    def test_go_module(self, tmp_path: Path) -> None:
        (tmp_path / "go.mod").write_text("module x\n", encoding="utf-8")
        assert _detect_build_check_command(str(tmp_path)) == "go build ./..."

    def test_cargo(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
        assert _detect_build_check_command(str(tmp_path)) == "cargo check"

    def test_python_not_auto_detected(self, tmp_path: Path) -> None:
        # mypy/pytest fail on un-clean trees → would block every merge; skip.
        (tmp_path / "pyproject.toml").write_text("[tool.mypy]\n", encoding="utf-8")
        assert _detect_build_check_command(str(tmp_path)) == ""

    def test_no_toolchain_returns_empty(self, tmp_path: Path) -> None:
        assert _detect_build_check_command(str(tmp_path)) == ""

    def test_malformed_package_json_falls_through(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text("{not json", encoding="utf-8")
        (tmp_path / "go.mod").write_text("module x\n", encoding="utf-8")
        assert _detect_build_check_command(str(tmp_path)) == "go build ./..."


class TestDefaultConfigDataBuildCheck:
    def test_build_check_block_added_when_detected(self, tmp_path: Path) -> None:
        _write_package_json(tmp_path, {"typecheck": "tsc --noEmit"})
        data = _default_config_data(_payload(), str(tmp_path))
        assert data["build_check"] == {
            "enabled": True,
            "command": "npm run typecheck",
            "working_dir": ".",
            "timeout_seconds": 600,
        }

    def test_no_build_check_block_when_not_detected(self, tmp_path: Path) -> None:
        data = _default_config_data(_payload(), str(tmp_path))
        assert "build_check" not in data
