"""Tests for ``scripts.eval._common``."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts.eval._common import (
    DUMMY_LLM_KEY,
    LLM_API_KEY_ENV_VARS,
    atomic_write_text,
    eval_subprocess_env,
    read_json,
    resolve_workdir,
    write_json,
)


class TestEvalSubprocessEnv:
    def test_strips_merge_dev_when_present_in_base_env(self) -> None:
        base = {"MERGE_DEV": "1", "PATH": "/usr/bin"}
        env = eval_subprocess_env(base_env=base)
        assert "MERGE_DEV" not in env
        assert env["PATH"] == "/usr/bin"

    def test_strips_merge_dev_even_when_real_os_environ_has_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MERGE_DEV", "1")
        env = eval_subprocess_env()
        assert "MERGE_DEV" not in env

    def test_strips_merge_dev_absent_is_noop(self) -> None:
        env = eval_subprocess_env(base_env={"PATH": "/usr/bin"})
        assert "MERGE_DEV" not in env

    def test_injects_dummy_keys_by_default(self) -> None:
        env = eval_subprocess_env(base_env={})
        for key in LLM_API_KEY_ENV_VARS:
            assert env[key] == DUMMY_LLM_KEY

    def test_injects_dummy_keys_overriding_base_env(self) -> None:
        env = eval_subprocess_env(
            base_env={"ANTHROPIC_API_KEY": "sk-real", "OPENAI_API_KEY": "sk-real"}
        )
        for key in LLM_API_KEY_ENV_VARS:
            assert env[key] == DUMMY_LLM_KEY

    def test_use_real_keys_preserves_existing_keys(self) -> None:
        env = eval_subprocess_env(
            base_env={"ANTHROPIC_API_KEY": "sk-real", "OPENAI_API_KEY": "sk-also-real"},
            use_real_keys=True,
        )
        assert env["ANTHROPIC_API_KEY"] == "sk-real"
        assert env["OPENAI_API_KEY"] == "sk-also-real"

    def test_use_real_keys_does_not_inject_when_absent(self) -> None:
        env = eval_subprocess_env(base_env={"PATH": "/x"}, use_real_keys=True)
        for key in LLM_API_KEY_ENV_VARS:
            assert key not in env

    def test_returns_new_dict_does_not_mutate_base_env(self) -> None:
        base = {"MERGE_DEV": "1", "PATH": "/usr/bin"}
        original = dict(base)
        env = eval_subprocess_env(base_env=base)
        assert base == original  # immutable: source untouched
        assert env is not base

    def test_returns_new_dict_does_not_mutate_os_environ(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MERGE_DEV", "1")
        snapshot = dict(os.environ)
        eval_subprocess_env()
        assert dict(os.environ) == snapshot


class TestResolveWorkdir:
    def test_creates_directory_by_default(self, tmp_path: Path) -> None:
        target = tmp_path / "newdir"
        resolved = resolve_workdir(target)
        assert resolved.is_dir()
        assert resolved == target.resolve()

    def test_create_false_does_not_create(self, tmp_path: Path) -> None:
        target = tmp_path / "notyet"
        resolved = resolve_workdir(target, create=False)
        assert not resolved.exists()

    def test_existing_dir_is_idempotent(self, tmp_path: Path) -> None:
        existing = tmp_path / "exists"
        existing.mkdir()
        resolved = resolve_workdir(existing)
        assert resolved == existing.resolve()
        assert resolved.is_dir()

    def test_existing_file_path_raises(self, tmp_path: Path) -> None:
        clash = tmp_path / "regular.txt"
        clash.write_text("hello", encoding="utf-8")
        with pytest.raises(FileExistsError):
            resolve_workdir(clash)


class TestJsonIO:
    def test_round_trip(self, tmp_path: Path) -> None:
        target = tmp_path / "out.json"
        payload = {"b": 2, "a": 1, "nested": [1, 2, 3]}
        write_json(target, payload)
        assert read_json(target) == payload

    def test_write_json_sorts_keys_by_default(self, tmp_path: Path) -> None:
        target = tmp_path / "sorted.json"
        write_json(target, {"b": 1, "a": 2})
        body = target.read_text(encoding="utf-8")
        assert body.index('"a"') < body.index('"b"')

    def test_write_json_creates_parent_dirs(self, tmp_path: Path) -> None:
        target = tmp_path / "deep" / "nested" / "x.json"
        write_json(target, {"k": "v"})
        assert json.loads(target.read_text(encoding="utf-8")) == {"k": "v"}

    def test_atomic_write_text_round_trip(self, tmp_path: Path) -> None:
        target = tmp_path / "msg.txt"
        atomic_write_text(target, "hello\n")
        assert target.read_text(encoding="utf-8") == "hello\n"

    def test_atomic_write_text_overwrites(self, tmp_path: Path) -> None:
        target = tmp_path / "msg.txt"
        atomic_write_text(target, "first")
        atomic_write_text(target, "second")
        assert target.read_text(encoding="utf-8") == "second"

    def test_atomic_write_text_no_temp_files_left(self, tmp_path: Path) -> None:
        target = tmp_path / "msg.txt"
        atomic_write_text(target, "ok")
        leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
        assert leftovers == []

    def test_read_json_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            read_json(tmp_path / "missing.json")
