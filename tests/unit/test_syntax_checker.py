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
