"""Unit tests for the deterministic duplicate top-level symbol check.

This guards the chunked-semantic-merge boundary-duplication failure mode
observed in the zod merge test, where adjacent chunks each re-emitted the
same top-level declaration (``export const ZodNumberFormat`` etc.), producing
a file that no longer compiles (TS2451 "Cannot redeclare block-scoped
variable"). The check is language-aware, deterministic, and LLM-free.
"""

from __future__ import annotations

from src.tools.duplicate_symbol_check import find_duplicate_symbols


class TestTypeScript:
    def test_detects_duplicate_const_and_class(self):
        content = (
            "export const ZodNumberFormat = ctor();\n"
            "export class ZodNumber {}\n"
            "export const Other = 2;\n"
            "// duplicated chunk boundary below\n"
            "export const ZodNumberFormat = ctor();\n"
            "export class ZodNumber {}\n"
        )
        dups = find_duplicate_symbols(content, "schemas.ts")
        names = {d.name for d in dups}
        assert names == {"ZodNumberFormat", "ZodNumber"}
        by_name = {d.name: d for d in dups}
        assert by_name["ZodNumberFormat"].count == 2
        assert by_name["ZodNumberFormat"].kind == "const"
        assert by_name["ZodNumber"].kind == "class"
        # 1-based line numbers of both occurrences
        assert by_name["ZodNumberFormat"].lines == [1, 5]

    def test_function_overloads_are_not_flagged(self):
        # TS/JS allow overload signatures + an implementation, so repeated
        # top-level ``function foo`` lines are legal and must not be reported.
        content = (
            "export function string(p?: A): ZodString;\n"
            "export function string(c?: B): ZodString;\n"
            "export function string(x?: unknown): ZodString { return impl(x); }\n"
        )
        assert find_duplicate_symbols(content, "schemas.ts") == []

    def test_unique_declarations_have_no_duplicates(self):
        content = "export const a = 1;\nexport function b() {}\nexport class C {}\n"
        assert find_duplicate_symbols(content, "x.ts") == []

    def test_indented_inner_declarations_are_not_top_level(self):
        # A nested const sharing a name with a top-level one must not count:
        # only column-0 declarations are top-level.
        content = (
            "export const handler = () => {\n"
            "  const handler = innerThing();\n"
            "  return handler;\n"
            "};\n"
        )
        assert find_duplicate_symbols(content, "x.ts") == []

    def test_interface_merging_is_not_flagged(self):
        # TS allows declaration merging for interfaces, so a repeated
        # interface is legal and must not be reported as a duplicate.
        content = (
            "export interface Foo { a: number }\nexport interface Foo { b: number }\n"
        )
        assert find_duplicate_symbols(content, "x.ts") == []


class TestPython:
    def test_detects_duplicate_def_and_class(self):
        content = (
            "def handler():\n"
            "    return 1\n"
            "\n"
            "class Service:\n"
            "    pass\n"
            "\n"
            "def handler():\n"
            "    return 2\n"
        )
        dups = find_duplicate_symbols(content, "mod.py")
        names = {d.name for d in dups}
        assert names == {"handler"}
        assert dups[0].count == 2


class TestOtherLanguages:
    def test_go_duplicate_func(self):
        content = "func Foo() {}\nfunc Bar() {}\nfunc Foo() {}\n"
        dups = find_duplicate_symbols(content, "main.go")
        assert {d.name for d in dups} == {"Foo"}

    def test_unknown_extension_returns_empty(self):
        content = "Title\nTitle\nTitle\n"
        assert find_duplicate_symbols(content, "README.md") == []

    def test_empty_content_returns_empty(self):
        assert find_duplicate_symbols("", "x.ts") == []
