"""Unit tests for additive fork-export preservation.

Guards the false-PASS observed in the zod merge test: the fork added
``export const cidrv6Mapped`` to ``regexes.ts``; the conflict resolution took
the upstream side and dropped it, yet the scar feature inventory reported the
file PASS because it only checked ``file_exists``. This module deterministically
extracts the symbols a fork *added* over the merge base and checks they survive
in the merged tree, so a dropped additive export can be flagged FAIL.
"""

from __future__ import annotations

from src.tools.feature_preservation import (
    added_exported_symbols,
    extract_exported_symbols,
    missing_symbols,
)


class TestExtractExportedSymbols:
    def test_typescript_exports(self):
        content = (
            "export const cidrv6 = /re/;\n"
            "export const cidrv6Mapped = /re2/;\n"
            "export function ipv6Util() {}\n"
            "export class ZodFoo {}\n"
            "const internal = 1;\n"  # not exported
        )
        syms = extract_exported_symbols(content, "regexes.ts")
        assert syms == {"cidrv6", "cidrv6Mapped", "ipv6Util", "ZodFoo"}

    def test_python_top_level_defs(self):
        content = "def public_fn():\n    pass\n\nclass Service:\n    pass\n"
        assert extract_exported_symbols(content, "mod.py") == {
            "public_fn",
            "Service",
        }

    def test_unknown_extension_empty(self):
        assert extract_exported_symbols("x\ny\n", "README.md") == set()


class TestAddedExportedSymbols:
    def test_fork_added_const_export(self):
        base = "export const cidrv6 = /re/;\n"
        fork = "export const cidrv6 = /re/;\nexport const cidrv6Mapped = /re2/;\n"
        assert added_exported_symbols(base, fork, "regexes.ts") == {"cidrv6Mapped"}

    def test_no_additions_returns_empty(self):
        base = "export const a = 1;\n"
        fork = "export const a = 2;\n"  # modified value, same symbol
        assert added_exported_symbols(base, fork, "x.ts") == set()


class TestMissingSymbols:
    def test_dropped_additive_export_is_missing(self):
        expected = {"cidrv6Mapped"}
        merged = "export const cidrv6 = /upstream-re/;\n"  # mapped dropped
        assert missing_symbols(merged, expected, "regexes.ts") == {"cidrv6Mapped"}

    def test_preserved_export_not_missing(self):
        expected = {"cidrv6Mapped"}
        merged = (
            "export const cidrv6 = /upstream-re/;\nexport const cidrv6Mapped = /re2/;\n"
        )
        assert missing_symbols(merged, expected, "regexes.ts") == set()
