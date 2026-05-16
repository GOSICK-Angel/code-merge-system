"""Tests for ``scripts.eval._ast_equiv`` — Verifier T4-A1..T4-A7."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.eval import _ast_equiv as ast_mod
from scripts.eval._ast_equiv import (
    UnsupportedFileType,
    is_equivalent,
    is_equivalent_files,
)


# ---------------------------------------------------------------------------
# T4-A1 — fallback covers BOM + CRLF
# ---------------------------------------------------------------------------


class TestFallbackBytes:
    def test_bom_and_crlf_normalised(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ast_mod, "_has_tree_sitter", lambda: False)
        a = b"\xef\xbb\xbfdef greet():\r\n    return 1\r\n"
        b = b"def greet():\n    return 1\n"
        equal, engine = is_equivalent(a, b, suffix=".py")
        assert equal is True
        assert engine == "fallback-bytes"

    def test_trailing_whitespace_trimmed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ast_mod, "_has_tree_sitter", lambda: False)
        a = b"line1   \nline2\t\n"
        b = b"line1\nline2\n"
        equal, engine = is_equivalent(a, b, suffix=".py")
        assert equal is True
        assert engine == "fallback-bytes"


# ---------------------------------------------------------------------------
# T4-A2 — fallback does NOT strip comments
# ---------------------------------------------------------------------------


class TestFallbackNoCommentStripping:
    def test_comment_difference_preserved(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ast_mod, "_has_tree_sitter", lambda: False)
        a = b"# comment\nx = 1\n"
        b = b"x = 1\n"
        equal, _ = is_equivalent(a, b, suffix=".py")
        assert equal is False

    def test_url_inside_string_not_truncated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ast_mod, "_has_tree_sitter", lambda: False)
        a = b'URL = "https://example.com/path"\n'
        b = b'URL = "https://example.com/path"\n'
        equal, _ = is_equivalent(a, b, suffix=".py")
        assert equal is True


# ---------------------------------------------------------------------------
# T4-A3 — JSON / YAML canonical
# ---------------------------------------------------------------------------


class TestCanonicalSerialisers:
    def test_json_key_order_does_not_matter(self) -> None:
        a = b'{"b":2,"a":1}'
        b = b'{"a":1,"b":2}'
        equal, engine = is_equivalent(a, b, suffix=".json")
        assert equal is True
        assert engine == "json-canonical"

    def test_json_value_difference_detected(self) -> None:
        equal, engine = is_equivalent(b'{"a":1}', b'{"a":2}', suffix=".json")
        assert equal is False
        assert engine == "json-canonical"

    def test_invalid_json_falls_back_to_bytes(self) -> None:
        # Two non-JSON inputs that are byte-identical only after the
        # text normaliser (CRLF → LF) — the EXACT short-circuit must
        # NOT fire, forcing the fallback path.
        equal, engine = is_equivalent(b"not json\r\n", b"not json\n", suffix=".json")
        assert equal is True
        assert engine == "fallback-bytes"

    def test_yaml_key_order_does_not_matter(self) -> None:
        a = b"b: 2\na: 1\n"
        b = b"a: 1\nb: 2\n"
        equal, engine = is_equivalent(a, b, suffix=".yaml")
        assert equal is True
        assert engine == "yaml-canonical"


# ---------------------------------------------------------------------------
# T4-A4 — binary strict bytes
# ---------------------------------------------------------------------------


class TestBinarySuffix:
    def test_one_byte_difference_detected(self) -> None:
        equal, engine = is_equivalent(b"\x00\x01\x02", b"\x00\x01\x03", suffix=".png")
        assert equal is False
        assert engine == "exact-bytes"

    def test_identical_binary_is_exact(self) -> None:
        equal, engine = is_equivalent(b"\x00\x01\x02", b"\x00\x01\x02", suffix=".png")
        assert equal is True
        assert engine == "exact-bytes"


# ---------------------------------------------------------------------------
# T4-A5 — tree-sitter engine name when available
# ---------------------------------------------------------------------------


class TestTreeSitterEngine:
    def test_engine_reports_tree_sitter_when_available(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ast_mod, "_has_tree_sitter", lambda: True)
        a = b"x = 1\n"
        b = b"x = 1\n"
        # With tree-sitter "available" the engine name is reported even
        # though the current shim still uses byte normalisation under
        # the hood (plan decision 4: extras not installed in CI).
        equal, engine = is_equivalent(a, b, suffix=".py")
        # Both inputs are byte-equal, so the EXACT short-circuit fires
        # and engine is exact-bytes — that is the right answer because
        # we never need the AST when bytes already agree.
        assert equal is True
        assert engine == "exact-bytes"

    def test_engine_reports_tree_sitter_when_normalisation_kicks_in(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ast_mod, "_has_tree_sitter", lambda: True)
        a = b"x = 1\r\n"
        b = b"x = 1\n"
        equal, engine = is_equivalent(a, b, suffix=".py")
        assert equal is True
        assert engine == "tree-sitter"


# ---------------------------------------------------------------------------
# T4-A6 — unsupported suffix raises
# ---------------------------------------------------------------------------


class TestUnsupportedSuffix:
    def test_unknown_suffix_raises(self) -> None:
        with pytest.raises(UnsupportedFileType) as exc:
            is_equivalent(b"x", b"y", suffix=".xyzunknown")
        assert ".xyzunknown" in str(exc.value)


# ---------------------------------------------------------------------------
# T4-A7 — file IO wrapper raises FileNotFoundError
# ---------------------------------------------------------------------------


class TestFileIOWrapper:
    def test_missing_b_raises(self, tmp_path: Path) -> None:
        a = tmp_path / "a.py"
        a.write_text("x = 1\n", encoding="utf-8")
        with pytest.raises(FileNotFoundError):
            is_equivalent_files(a, tmp_path / "absent.py")

    def test_round_trip_via_disk(self, tmp_path: Path) -> None:
        a = tmp_path / "a.py"
        b = tmp_path / "b.py"
        a.write_bytes(b"x = 1\r\n")
        b.write_bytes(b"x = 1\n")
        equal, engine = is_equivalent_files(a, b)
        assert equal is True
        assert engine in {"tree-sitter", "fallback-bytes"}
