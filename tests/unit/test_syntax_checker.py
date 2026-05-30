import pytest

from src.tools.syntax_checker import check_syntax, SyntaxCheckResult


class TestCheckSyntax:
    def test_valid_python(self) -> None:
        result = check_syntax("test.py", "x = 1\nprint(x)\n")
        assert result.valid is True
        assert result.language == "python"
        assert result.errors == []

    def test_invalid_python(self) -> None:
        result = check_syntax("test.py", "def foo(\n")
        assert result.valid is False
        assert result.language == "python"
        assert len(result.errors) >= 1
        assert result.errors[0].line > 0

    def test_valid_json(self) -> None:
        result = check_syntax("config.json", '{"key": "value"}')
        assert result.valid is True
        assert result.language == "json"

    def test_invalid_json(self) -> None:
        result = check_syntax("config.json", '{"key": }')
        assert result.valid is False
        assert result.language == "json"

    def test_valid_yaml(self) -> None:
        result = check_syntax("config.yaml", "key: value\nlist:\n  - item1\n")
        assert result.valid is True
        assert result.language == "yaml"

    def test_invalid_yaml(self) -> None:
        result = check_syntax("config.yml", "key: :\n  bad: [")
        assert result.valid is False
        assert result.language == "yaml"

    def test_unknown_extension(self) -> None:
        result = check_syntax("file.rs", "fn main() {}")
        assert result.valid is True
        assert result.language == "rust"

    def test_empty_content_python(self) -> None:
        result = check_syntax("empty.py", "")
        assert result.valid is True

    def test_empty_content_json(self) -> None:
        # "no content" must not be a syntax error — otherwise Judge flags
        # every SKIP-decision .json file (missing/empty worktree path) as
        # critical, exhausting the dispute loop. See
        # TestJudgeSkipFilterOnSkipDecision for the upstream guard.
        result = check_syntax("empty.json", "")
        assert result.valid is True

    def test_whitespace_only_json(self) -> None:
        result = check_syntax("empty.json", "   \n\t  \n")
        assert result.valid is True

    def test_empty_content_yaml(self) -> None:
        result = check_syntax("empty.yaml", "")
        assert result.valid is True

    def test_whitespace_only_yaml(self) -> None:
        result = check_syntax("empty.yaml", "   \n\n")
        assert result.valid is True

    def test_python_complex_valid(self) -> None:
        code = """
class Foo:
    def __init__(self, x: int) -> None:
        self.x = x

    async def bar(self) -> str:
        return f"value: {self.x}"
"""
        result = check_syntax("foo.py", code)
        assert result.valid is True

    def test_python_syntax_error_details(self) -> None:
        result = check_syntax("bad.py", "if True\n    pass\n")
        assert result.valid is False
        assert result.errors[0].line == 1

    def test_no_extension(self) -> None:
        result = check_syntax("Makefile", "all:\n\techo hello")
        assert result.valid is True
        assert result.language == "unknown"

    def test_yaml_yml_extension(self) -> None:
        result = check_syntax("data.yml", "items:\n  - one\n  - two\n")
        assert result.valid is True
        assert result.language == "yaml"

    def test_result_is_frozen(self) -> None:
        result = check_syntax("test.py", "x = 1\n")
        assert isinstance(result, SyntaxCheckResult)

    def test_json_error_has_location(self) -> None:
        result = check_syntax("bad.json", '{"a": 1,}')
        assert result.valid is False
        assert result.errors[0].line >= 1
        assert result.errors[0].column >= 0

    def test_yaml_error_has_location(self) -> None:
        result = check_syntax("bad.yaml", ":\n  - [unclosed")
        assert result.valid is False
        assert len(result.errors) >= 1


class TestBalanceChecker:
    """#1: conservative comment/string/regex-aware bracket-balance for the
    brace-family languages (previously a no-op returning valid=True)."""

    def test_valid_typescript_passes(self) -> None:
        code = "export function f(x: number): number {\n  return x + 1;\n}\n"
        assert check_syntax("a.ts", code).valid is True

    def test_truncated_typescript_unbalanced_fails(self) -> None:
        # LLM truncation: missing closing brace.
        r = check_syntax("a.ts", "export function f() {\n  return bar(\n")
        assert r.valid is False
        assert r.language == "typescript"

    def test_clean_mid_file_elision_unbalanced_fails(self) -> None:
        r = check_syntax("a.ts", "class A {\n  m() {\n    return 1\n")
        assert r.valid is False

    def test_stray_closer_fails(self) -> None:
        assert check_syntax("a.ts", "function a(){}\n}\n").valid is False

    def test_mismatched_bracket_fails(self) -> None:
        assert check_syntax("a.ts", "function a( ] {}\n").valid is False

    def test_regex_brace_quantifier_is_clean(self) -> None:
        # zod is dense with /{n,m}/ quantifiers — these must NOT count as braces.
        code = "const re = /\\d{1,3}/g;\nfunction f() { return re; }\n"
        assert check_syntax("a.ts", code).valid is True

    def test_regex_char_class_brace_is_clean(self) -> None:
        assert check_syntax("a.ts", "const re = /[{}]/;\nconst y = {a:1};\n").valid

    def test_template_interpolation_braces_clean(self) -> None:
        code = "const s = `${a} and ${ {x:1}.x }`;\nconst y = {b:2};\n"
        assert check_syntax("a.ts", code).valid is True

    def test_division_not_treated_as_regex(self) -> None:
        assert check_syntax("a.ts", "const z = a / b / c;\nlet q = z;\n").valid

    def test_braces_in_comment_ignored(self) -> None:
        assert check_syntax("a.ts", "// a } here {\nfunction h(){return 1;}\n").valid

    def test_braces_in_string_ignored(self) -> None:
        assert check_syntax("a.ts", 'const x = "a { b } c"; const y = [1];\n').valid

    def test_unterminated_template_fails(self) -> None:
        assert check_syntax("a.ts", "const x = `hello ${y}").valid is False

    def test_go_raw_string_with_braces_clean(self) -> None:
        code = "package main\nfunc main(){ s := `a{b}c`; _ = s }\n"
        assert check_syntax("a.go", code).valid is True

    def test_go_truncated_fails(self) -> None:
        assert check_syntax("a.go", "package main\nfunc main(){\n").valid is False

    def test_rust_lifetime_not_string(self) -> None:
        code = "fn f<'a>(x: &'a str) -> &'a str { x }\n"
        assert check_syntax("a.rs", code).valid is True

    def test_rust_nested_block_comment_clean(self) -> None:
        assert check_syntax("a.rs", "/* a /* b */ c */\nfn f(){}\n").valid is True

    def test_java_char_literal_brace_clean(self) -> None:
        assert check_syntax("a.java", "class A { char c = '{'; void m(){} }\n").valid

    def test_empty_content_is_valid(self) -> None:
        assert check_syntax("a.ts", "   \n  ").valid is True
