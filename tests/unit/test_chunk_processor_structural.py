"""#10 chunked-merge structural-alignment regression tests.

Covers: forced mid-body split detection, post-merge seam balance gate,
symbol-sequence-guarded equal-count alignment (b-covering preserved), and
conservative JS/TS function-implementation duplicate detection.
"""

from __future__ import annotations

from src.tools.chunk_processor import (
    align_chunks,
    seam_balanced,
    split_with_forced_flag,
)
from src.tools.duplicate_symbol_check import find_duplicate_function_impls


class TestForcedMidBodySplit:
    def test_giant_unsplittable_unit_flags_forced(self) -> None:
        # One function with no internal boundary/blank line, far over 2x chunk.
        body = "    x = " + "y + " * 2000 + "0\n"
        content = "def huge():\n" + body
        chunk_size = 200
        chunks, forced = split_with_forced_flag(content, "f.py", chunk_size)
        assert forced is True
        assert len(chunks) >= 2

    def test_clean_boundaries_not_forced(self) -> None:
        content = "".join(f"def fn{i}():\n    return {i}\n" for i in range(40))
        chunk_size = len(content) // 5
        chunks, forced = split_with_forced_flag(content, "f.py", chunk_size)
        assert forced is False
        assert len(chunks) >= 2

    def test_small_file_never_forced(self) -> None:
        chunks, forced = split_with_forced_flag(
            "def a():\n    return 1\n", "f.py", 9999
        )
        assert forced is False
        assert chunks == ["def a():\n    return 1\n"]


class TestSeamBalanced:
    def test_balanced_ts_passes(self) -> None:
        assert seam_balanced("export function f() { return 1; }\n", "a.ts") is True

    def test_imbalanced_ts_fails(self) -> None:
        # Missing closing brace — the shape a mispaired/partial chunk produces.
        assert seam_balanced("export function f() { return 1;\n", "a.ts") is False

    def test_unsupported_language_passes(self) -> None:
        assert seam_balanced("anything at all {{{", "notes.md") is True


class TestSymbolGuardedAlignment:
    def test_equal_count_matching_symbols_zips(self) -> None:
        a = ["def alpha():\n    return 1\n", "def beta():\n    return 2\n"]
        b = ["def alpha():\n    return 10\n", "def beta():\n    return 20\n"]
        pairs = align_chunks(a, b)
        assert pairs == list(zip(a, b))

    def test_equal_count_diverged_symbols_falls_back_but_covers_b(self) -> None:
        # Same count, but the symbol sequences differ (an inserted/renamed unit).
        # Must NOT blindly zip; whichever path is taken must still cover every b.
        a = ["def alpha():\n    return 1\n", "def beta():\n    return 2\n"]
        b = ["def gamma():\n    return 9\n", "def beta():\n    return 2\n"]
        pairs = align_chunks(a, b)
        assert [p[0] for p in pairs] == a  # one pair per fork chunk, in order
        reassembled = "".join(p[1] for p in pairs)
        assert reassembled == "".join(b)  # every upstream chunk covered once


class TestDuplicateFunctionImpls:
    def test_detects_single_line_impl_duplicate(self) -> None:
        content = (
            "export function foo(x) { return x; }\n"
            "const z = 1;\n"
            "export function foo(x) { return x + 1; }\n"
        )
        assert find_duplicate_function_impls(content, "a.ts") == ["foo"]

    def test_overload_signatures_not_flagged(self) -> None:
        # Legal TS: two overload signatures (no body) + one implementation.
        content = (
            "function foo(x: number): number;\n"
            "function foo(x: string): string;\n"
            "function foo(x: any): any { return x; }\n"
        )
        assert find_duplicate_function_impls(content, "a.ts") == []

    def test_single_impl_not_flagged(self) -> None:
        content = "export function foo(x) { return x; }\n"
        assert find_duplicate_function_impls(content, "a.ts") == []

    def test_non_js_returns_empty(self) -> None:
        content = "def foo(): pass\ndef foo(): pass\n"
        assert find_duplicate_function_impls(content, "a.py") == []
