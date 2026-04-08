"""Tests for AST chunking, indent fallback, and three-level rendering."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.llm.chunker import (
    ASTChunker,
    ChunkKind,
    CodeChunk,
    IndentChunker,
    detect_language,
    render_chunk,
    render_file_staged,
    render_signature,
)
from src.llm.relevance import RenderLevel


# ---------------------------------------------------------------------------
# Step 1: CodeChunk model + language detection
# ---------------------------------------------------------------------------


class TestChunkKind:
    def test_chunk_kind_values(self) -> None:
        assert ChunkKind.MODULE == "module"
        assert ChunkKind.CLASS == "class"
        assert ChunkKind.FUNCTION == "function"
        assert ChunkKind.METHOD == "method"
        assert ChunkKind.IMPORT == "import"
        assert ChunkKind.STATEMENT == "statement"
        assert ChunkKind.COMMENT == "comment"
        assert ChunkKind.UNKNOWN == "unknown"


class TestCodeChunkModel:
    def test_code_chunk_frozen(self) -> None:
        chunk = CodeChunk(
            name="foo",
            kind=ChunkKind.FUNCTION,
            start_line=1,
            end_line=5,
            content="def foo(): pass",
            signature="def foo():",
        )
        with pytest.raises(Exception):
            chunk.name = "bar"  # type: ignore[misc]

    def test_code_chunk_signature_stored(self) -> None:
        chunk = CodeChunk(
            name="bar",
            kind=ChunkKind.FUNCTION,
            start_line=10,
            end_line=20,
            content="def bar(x: int) -> int:\n    return x * 2",
            signature="def bar(x: int) -> int:",
        )
        assert chunk.signature == "def bar(x: int) -> int:"
        assert chunk.children == ()
        assert chunk.byte_range == (0, 0)


class TestDetectLanguage:
    def test_detect_language_python(self) -> None:
        assert detect_language("src/main.py") == "python"

    def test_detect_language_javascript(self) -> None:
        assert detect_language("app/index.js") == "javascript"

    def test_detect_language_typescript(self) -> None:
        assert detect_language("src/utils.ts") == "typescript"

    def test_detect_language_tsx(self) -> None:
        assert detect_language("component.tsx") == "tsx"

    def test_detect_language_unknown(self) -> None:
        assert detect_language("readme.md") is None

    def test_detect_language_case_insensitive(self) -> None:
        assert detect_language("Main.PY") == "python"


# ---------------------------------------------------------------------------
# Step 2: AST Chunker (tree-sitter or fallback)
# ---------------------------------------------------------------------------


class TestASTChunker:
    def test_ast_chunk_python_function(self) -> None:
        source = "def hello():\n    print('hello')\n"
        chunks = ASTChunker.chunk(source, "python")
        func_chunks = [c for c in chunks if c.kind == ChunkKind.FUNCTION]
        assert len(func_chunks) >= 1
        assert "hello" in func_chunks[0].name

    def test_ast_chunk_python_class_with_methods(self) -> None:
        source = (
            "class Foo:\n"
            "    def bar(self):\n"
            "        pass\n"
            "    def baz(self):\n"
            "        pass\n"
        )
        chunks = ASTChunker.chunk(source, "python")
        class_chunks = [c for c in chunks if c.kind == ChunkKind.CLASS]
        assert len(class_chunks) == 1
        assert "Foo" in class_chunks[0].name
        assert len(class_chunks[0].children) >= 2

    def test_ast_chunk_imports_merged(self) -> None:
        source = "import os\nimport sys\nfrom pathlib import Path\n"
        chunks = ASTChunker.chunk(source, "python")
        import_chunks = [c for c in chunks if c.kind == ChunkKind.IMPORT]
        assert len(import_chunks) == 1

    def test_ast_chunk_mixed_file(self) -> None:
        source = (
            "import os\n\n"
            "MAX = 10\n\n"
            "def foo():\n    pass\n\n"
            "class Bar:\n"
            "    def baz(self):\n"
            "        pass\n"
        )
        chunks = ASTChunker.chunk(source, "python")
        assert len(chunks) >= 3
        kinds = {c.kind for c in chunks}
        assert ChunkKind.FUNCTION in kinds or ChunkKind.CLASS in kinds

    def test_ast_chunk_preserves_line_numbers(self) -> None:
        source = "import os\n\ndef foo():\n    pass\n"
        chunks = ASTChunker.chunk(source, "python")
        for chunk in chunks:
            assert chunk.start_line >= 1
            assert chunk.end_line >= chunk.start_line

    def test_ast_chunk_decorated_function(self) -> None:
        source = "@staticmethod\ndef decorated():\n    pass\n"
        chunks = ASTChunker.chunk(source, "python")
        func_chunks = [
            c for c in chunks if c.kind in (ChunkKind.FUNCTION, ChunkKind.METHOD)
        ]
        assert len(func_chunks) >= 1

    def test_ast_chunk_empty_source(self) -> None:
        assert ASTChunker.chunk("", "python") == []
        assert ASTChunker.chunk("   \n  \n", "python") == []

    def test_ast_chunk_fallback_when_no_treesitter(self) -> None:
        with patch("src.llm.chunker._HAS_TREE_SITTER", False):
            source = "def foo():\n    pass\n"
            chunks = ASTChunker.chunk(source, "python")
            assert len(chunks) >= 1

    def test_ast_chunk_unknown_language_uses_indent(self) -> None:
        source = "function test() {\n    return 1;\n}\n"
        chunks = ASTChunker.chunk(source, "unknown_lang_xyz")
        assert len(chunks) >= 1


# ---------------------------------------------------------------------------
# Step 3: Indent-Based Fallback Chunker
# ---------------------------------------------------------------------------


class TestIndentChunker:
    def test_indent_chunker_python(self) -> None:
        source = "def foo():\n    return 1\n\ndef bar():\n    return 2\n"
        chunks = IndentChunker.chunk(source, "python")
        assert len(chunks) >= 2

    def test_indent_chunker_blank_line_boundary(self) -> None:
        source = "x = 1\ny = 2\n\nz = 3\n"
        chunks = IndentChunker.chunk(source, "python")
        assert len(chunks) >= 2

    def test_indent_chunker_no_false_split_in_function(self) -> None:
        source = "def foo():\n    x = 1\n    y = 2\n    return x + y\n"
        chunks = IndentChunker.chunk(source, "python")
        func_chunks = [c for c in chunks if c.kind == ChunkKind.FUNCTION]
        assert len(func_chunks) == 1

    def test_indent_chunker_empty_file(self) -> None:
        assert IndentChunker.chunk("") == []
        assert IndentChunker.chunk("   \n  \n") == []

    def test_indent_chunker_single_line(self) -> None:
        chunks = IndentChunker.chunk("x = 1")
        assert len(chunks) == 1

    def test_indent_chunker_class_detection(self) -> None:
        source = "class Foo:\n    pass\n"
        chunks = IndentChunker.chunk(source, "python")
        class_chunks = [c for c in chunks if c.kind == ChunkKind.CLASS]
        assert len(class_chunks) == 1


# ---------------------------------------------------------------------------
# Step 5: Three-Level Renderer
# ---------------------------------------------------------------------------


class TestRenderChunk:
    def test_render_chunk_full(self) -> None:
        chunk = CodeChunk(
            name="foo",
            kind=ChunkKind.FUNCTION,
            start_line=1,
            end_line=3,
            content="def foo():\n    return 42",
            signature="def foo():",
        )
        result = render_chunk(chunk, RenderLevel.FULL)
        assert result == "def foo():\n    return 42"

    def test_render_chunk_signature_function(self) -> None:
        chunk = CodeChunk(
            name="foo",
            kind=ChunkKind.FUNCTION,
            start_line=1,
            end_line=3,
            content="def foo(x: int) -> int:\n    return x * 2",
            signature="def foo(x: int) -> int:",
        )
        result = render_chunk(chunk, RenderLevel.SIGNATURE)
        assert "def foo(x: int) -> int:" in result
        assert "..." in result

    def test_render_chunk_signature_class(self) -> None:
        chunk = CodeChunk(
            name="Router",
            kind=ChunkKind.CLASS,
            start_line=1,
            end_line=20,
            content="class Router:\n    def get(self): pass\n    def post(self): pass",
            signature="class Router:",
            children=("get", "post"),
        )
        result = render_chunk(chunk, RenderLevel.SIGNATURE)
        assert "class Router:" in result
        assert "get" in result
        assert "post" in result

    def test_render_chunk_drop(self) -> None:
        chunk = CodeChunk(
            name="unused",
            kind=ChunkKind.STATEMENT,
            start_line=1,
            end_line=1,
            content="x = 1",
            signature="x = 1",
        )
        result = render_chunk(chunk, RenderLevel.DROP)
        assert result == ""


class TestRenderSignature:
    def test_function_signature(self) -> None:
        chunk = CodeChunk(
            name="foo",
            kind=ChunkKind.FUNCTION,
            start_line=1,
            end_line=5,
            content="def foo(): pass",
            signature="def foo():",
        )
        assert "..." in render_signature(chunk)

    def test_class_signature_with_children(self) -> None:
        chunk = CodeChunk(
            name="Cls",
            kind=ChunkKind.CLASS,
            start_line=1,
            end_line=10,
            content="class Cls: pass",
            signature="class Cls:",
            children=("method_a", "method_b"),
        )
        result = render_signature(chunk)
        assert "methods:" in result
        assert "method_a" in result

    def test_class_signature_no_children(self) -> None:
        chunk = CodeChunk(
            name="Empty",
            kind=ChunkKind.CLASS,
            start_line=1,
            end_line=2,
            content="class Empty: pass",
            signature="class Empty:",
        )
        result = render_signature(chunk)
        assert result == "class Empty:"

    def test_import_signature(self) -> None:
        chunk = CodeChunk(
            name="imports",
            kind=ChunkKind.IMPORT,
            start_line=1,
            end_line=1,
            content="import os",
            signature="import os",
        )
        assert render_signature(chunk) == "import os"


class TestRenderFileStaged:
    def test_render_file_staged_preserves_order(self) -> None:
        chunks = [
            CodeChunk(
                name="b",
                kind=ChunkKind.FUNCTION,
                start_line=10,
                end_line=15,
                content="def b(): pass",
                signature="def b():",
            ),
            CodeChunk(
                name="a",
                kind=ChunkKind.FUNCTION,
                start_line=1,
                end_line=5,
                content="def a(): pass",
                signature="def a():",
            ),
        ]
        levels = {"a": RenderLevel.FULL, "b": RenderLevel.FULL}
        result = render_file_staged(chunks, levels)
        assert result.index("def a()") < result.index("def b()")

    def test_render_file_staged_drop_marker(self) -> None:
        chunks = [
            CodeChunk(
                name="keep",
                kind=ChunkKind.FUNCTION,
                start_line=1,
                end_line=5,
                content="def keep(): pass",
                signature="def keep():",
            ),
            CodeChunk(
                name="drop1",
                kind=ChunkKind.STATEMENT,
                start_line=6,
                end_line=8,
                content="x = 1",
                signature="x = 1",
            ),
            CodeChunk(
                name="drop2",
                kind=ChunkKind.STATEMENT,
                start_line=9,
                end_line=11,
                content="y = 2",
                signature="y = 2",
            ),
            CodeChunk(
                name="keep2",
                kind=ChunkKind.FUNCTION,
                start_line=12,
                end_line=15,
                content="def keep2(): pass",
                signature="def keep2():",
            ),
        ]
        levels = {
            "keep": RenderLevel.FULL,
            "drop1": RenderLevel.DROP,
            "drop2": RenderLevel.DROP,
            "keep2": RenderLevel.FULL,
        }
        result = render_file_staged(chunks, levels)
        assert "2 sections omitted" in result

    def test_render_file_staged_all_full(self) -> None:
        chunks = [
            CodeChunk(
                name="a",
                kind=ChunkKind.FUNCTION,
                start_line=1,
                end_line=3,
                content="def a(): pass",
                signature="def a():",
            ),
            CodeChunk(
                name="b",
                kind=ChunkKind.FUNCTION,
                start_line=5,
                end_line=8,
                content="def b(): pass",
                signature="def b():",
            ),
        ]
        levels = {"a": RenderLevel.FULL, "b": RenderLevel.FULL}
        result = render_file_staged(chunks, levels)
        assert "omitted" not in result
        assert "def a()" in result
        assert "def b()" in result

    def test_render_file_staged_all_dropped(self) -> None:
        chunks = [
            CodeChunk(
                name="x",
                kind=ChunkKind.STATEMENT,
                start_line=1,
                end_line=1,
                content="x = 1",
                signature="x = 1",
            ),
        ]
        levels = {"x": RenderLevel.DROP}
        result = render_file_staged(chunks, levels)
        assert "1 sections omitted" in result
