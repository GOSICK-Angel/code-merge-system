"""AST-aware code chunking and three-level rendering.

Splits source files into semantic chunks (functions, classes, imports, etc.)
using tree-sitter when available, with an indent-based fallback for unsupported
languages.  Each chunk can be rendered at three levels: FULL, SIGNATURE, or DROP.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Step 1: CodeChunk model + language detection
# ---------------------------------------------------------------------------


class ChunkKind(StrEnum):
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    IMPORT = "import"
    STATEMENT = "statement"
    COMMENT = "comment"
    UNKNOWN = "unknown"


class CodeChunk(BaseModel, frozen=True):
    name: str
    kind: ChunkKind
    start_line: int  # 1-based inclusive
    end_line: int  # 1-based inclusive
    content: str
    signature: str  # first meaningful line(s)
    children: tuple[str, ...] = ()
    byte_range: tuple[int, int] = (0, 0)


LANGUAGE_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".cs": "c_sharp",
    ".rb": "ruby",
}


def detect_language(file_path: str) -> str | None:
    suffix = Path(file_path).suffix.lower()
    return LANGUAGE_MAP.get(suffix)


# ---------------------------------------------------------------------------
# Step 2: AST Chunker (tree-sitter) — optional dependency
# ---------------------------------------------------------------------------

_HAS_TREE_SITTER = False
try:
    import tree_sitter

    _HAS_TREE_SITTER = True
except ImportError:
    tree_sitter = None  # type: ignore[assignment]


CHUNK_BOUNDARY_NODES: dict[str, dict[str, ChunkKind]] = {
    "python": {
        "function_definition": ChunkKind.FUNCTION,
        "class_definition": ChunkKind.CLASS,
        "import_statement": ChunkKind.IMPORT,
        "import_from_statement": ChunkKind.IMPORT,
        "decorated_definition": ChunkKind.FUNCTION,
    },
    "javascript": {
        "function_declaration": ChunkKind.FUNCTION,
        "class_declaration": ChunkKind.CLASS,
        "method_definition": ChunkKind.METHOD,
        "arrow_function": ChunkKind.FUNCTION,
        "import_statement": ChunkKind.IMPORT,
        "export_statement": ChunkKind.STATEMENT,
    },
    "typescript": {
        "function_declaration": ChunkKind.FUNCTION,
        "class_declaration": ChunkKind.CLASS,
        "method_definition": ChunkKind.METHOD,
        "arrow_function": ChunkKind.FUNCTION,
        "import_statement": ChunkKind.IMPORT,
        "export_statement": ChunkKind.STATEMENT,
    },
    "tsx": {
        "function_declaration": ChunkKind.FUNCTION,
        "class_declaration": ChunkKind.CLASS,
        "method_definition": ChunkKind.METHOD,
        "arrow_function": ChunkKind.FUNCTION,
        "import_statement": ChunkKind.IMPORT,
        "export_statement": ChunkKind.STATEMENT,
    },
    "go": {
        "function_declaration": ChunkKind.FUNCTION,
        "method_declaration": ChunkKind.METHOD,
        "type_declaration": ChunkKind.CLASS,
        "import_declaration": ChunkKind.IMPORT,
    },
    "rust": {
        "function_item": ChunkKind.FUNCTION,
        "impl_item": ChunkKind.CLASS,
        "struct_item": ChunkKind.CLASS,
        "enum_item": ChunkKind.CLASS,
        "use_declaration": ChunkKind.IMPORT,
    },
    "java": {
        "method_declaration": ChunkKind.METHOD,
        "constructor_declaration": ChunkKind.METHOD,
        "class_declaration": ChunkKind.CLASS,
        "interface_declaration": ChunkKind.CLASS,
        "import_declaration": ChunkKind.IMPORT,
    },
    "c": {
        "function_definition": ChunkKind.FUNCTION,
        "struct_specifier": ChunkKind.CLASS,
        "preproc_include": ChunkKind.IMPORT,
    },
    "cpp": {
        "function_definition": ChunkKind.FUNCTION,
        "class_specifier": ChunkKind.CLASS,
        "struct_specifier": ChunkKind.CLASS,
        "preproc_include": ChunkKind.IMPORT,
    },
}

_LANGUAGE_MODULE_MAP: dict[str, str] = {
    "python": "tree_sitter_python",
    "javascript": "tree_sitter_javascript",
    "typescript": "tree_sitter_typescript",
    "tsx": "tree_sitter_typescript",
    "go": "tree_sitter_go",
    "rust": "tree_sitter_rust",
    "java": "tree_sitter_java",
    "c": "tree_sitter_c",
    "cpp": "tree_sitter_cpp",
}

_PARSER_CACHE: dict[str, Any] = {}


def _get_parser(language: str) -> Any | None:
    if not _HAS_TREE_SITTER:
        return None
    if language in _PARSER_CACHE:
        return _PARSER_CACHE[language]

    module_name = _LANGUAGE_MODULE_MAP.get(language)
    if module_name is None:
        return None

    try:
        import importlib

        lang_mod = importlib.import_module(module_name)
        lang_fn = lang_mod.language

        if language == "tsx":
            lang_obj = tree_sitter.Language(lang_fn("tsx"))
        elif language == "typescript":
            lang_obj = tree_sitter.Language(lang_fn("typescript"))
        else:
            lang_obj = tree_sitter.Language(lang_fn())

        parser = tree_sitter.Parser(lang_obj)
        _PARSER_CACHE[language] = parser
        return parser
    except Exception:
        logger.debug("Failed to load tree-sitter parser for %s", language)
        return None


def _node_text(node: object, source: str) -> str:
    start = getattr(node, "start_byte", 0)
    end = getattr(node, "end_byte", 0)
    return source[start:end]


def _node_start_line(node: object) -> int:
    sp = getattr(node, "start_point", (0, 0))
    return sp[0] + 1


def _node_end_line(node: object) -> int:
    ep = getattr(node, "end_point", (0, 0))
    return ep[0] + 1


def _extract_child_method_names(node: object, source: str, language: str) -> list[str]:
    methods: list[str] = []
    boundary_types = CHUNK_BOUNDARY_NODES.get(language, {})
    func_kinds = {ChunkKind.FUNCTION, ChunkKind.METHOD}
    for child in getattr(node, "children", []):
        child_type = getattr(child, "type", "")
        kind = boundary_types.get(child_type)
        if kind in func_kinds:
            for sub in getattr(child, "children", []):
                if getattr(sub, "type", "") in ("identifier", "property_identifier"):
                    methods.append(_node_text(sub, source))
                    break
        if child_type == "decorated_definition":
            for sub in getattr(child, "children", []):
                sub_kind = boundary_types.get(getattr(sub, "type", ""))
                if sub_kind in func_kinds:
                    for sub2 in getattr(sub, "children", []):
                        if getattr(sub2, "type", "") == "identifier":
                            methods.append(_node_text(sub2, source))
                            break
        if child_type == "body" or child_type == "block":
            methods.extend(_extract_child_method_names(child, source, language))
    return methods


def _extract_signature(
    node: object, source: str, kind: ChunkKind, language: str
) -> str:
    text = _node_text(node, source)
    if kind in (ChunkKind.FUNCTION, ChunkKind.METHOD):
        for delimiter in (":", "{"):
            idx = text.find(delimiter)
            if idx != -1:
                return text[: idx + 1].strip()
        return text.split("\n")[0].strip()

    if kind == ChunkKind.CLASS:
        return text.split("\n")[0].strip()

    return text.split("\n")[0].strip()


def _extract_name(node: object, source: str, kind: ChunkKind, language: str) -> str:
    for child in getattr(node, "children", []):
        ctype = getattr(child, "type", "")
        if ctype in ("identifier", "property_identifier", "type_identifier"):
            return _node_text(child, source)
    first_line = _node_text(node, source).split("\n")[0].strip()
    return first_line[:60]


def _reclassify_decorated(
    node: object, source: str, language: str
) -> tuple[ChunkKind, str]:
    boundary = CHUNK_BOUNDARY_NODES.get(language, {})
    for child in getattr(node, "children", []):
        child_type = getattr(child, "type", "")
        if child_type in boundary:
            actual_kind = boundary[child_type]
            name = _extract_name(child, source, actual_kind, language)
            return actual_kind, name
    return ChunkKind.FUNCTION, _extract_name(node, source, ChunkKind.FUNCTION, language)


def _node_to_chunk(
    node: object, source: str, kind: ChunkKind, language: str
) -> CodeChunk:
    if (
        kind == ChunkKind.FUNCTION
        and getattr(node, "type", "") == "decorated_definition"
    ):
        kind, name = _reclassify_decorated(node, source, language)
    else:
        name = _extract_name(node, source, kind, language)

    content = _node_text(node, source)
    signature = _extract_signature(node, source, kind, language)
    children: tuple[str, ...] = ()
    if kind == ChunkKind.CLASS:
        children = tuple(_extract_child_method_names(node, source, language))

    return CodeChunk(
        name=name,
        kind=kind,
        start_line=_node_start_line(node),
        end_line=_node_end_line(node),
        content=content,
        signature=signature,
        children=children,
        byte_range=(
            getattr(node, "start_byte", 0),
            getattr(node, "end_byte", 0),
        ),
    )


def _merge_statement_nodes(nodes: list[object], source: str) -> CodeChunk:
    if not nodes:
        return CodeChunk(
            name="<empty>",
            kind=ChunkKind.STATEMENT,
            start_line=1,
            end_line=1,
            content="",
            signature="",
        )
    first = nodes[0]
    last = nodes[-1]
    start_byte = getattr(first, "start_byte", 0)
    end_byte = getattr(last, "end_byte", 0)
    content = source[start_byte:end_byte]
    first_line = content.split("\n")[0].strip()

    return CodeChunk(
        name=first_line[:60] if first_line else "<statements>",
        kind=ChunkKind.STATEMENT,
        start_line=_node_start_line(first),
        end_line=_node_end_line(last),
        content=content,
        signature=first_line[:80] if first_line else "",
        byte_range=(start_byte, end_byte),
    )


def _merge_adjacent_imports(chunks: list[CodeChunk]) -> list[CodeChunk]:
    if not chunks:
        return chunks
    merged: list[CodeChunk] = []
    acc: list[CodeChunk] = []

    def flush_imports() -> None:
        if not acc:
            return
        if len(acc) == 1:
            merged.append(acc[0])
        else:
            combined_content = "\n".join(c.content for c in acc)
            combined_sig = acc[0].signature
            merged.append(
                CodeChunk(
                    name="imports",
                    kind=ChunkKind.IMPORT,
                    start_line=acc[0].start_line,
                    end_line=acc[-1].end_line,
                    content=combined_content,
                    signature=combined_sig,
                    byte_range=(acc[0].byte_range[0], acc[-1].byte_range[1]),
                )
            )
        acc.clear()

    for chunk in chunks:
        if chunk.kind == ChunkKind.IMPORT:
            acc.append(chunk)
        else:
            flush_imports()
            merged.append(chunk)
    flush_imports()
    return merged


def _extract_chunks(root: object, source: str, language: str) -> list[CodeChunk]:
    chunks: list[CodeChunk] = []
    boundary_types = CHUNK_BOUNDARY_NODES.get(language, {})
    pending_statements: list[object] = []

    for child in getattr(root, "children", []):
        child_type = getattr(child, "type", "")
        if child_type in boundary_types:
            if pending_statements:
                chunks.append(_merge_statement_nodes(pending_statements, source))
                pending_statements = []

            kind = boundary_types[child_type]
            chunk = _node_to_chunk(child, source, kind, language)
            chunks.append(chunk)
        else:
            pending_statements.append(child)

    if pending_statements:
        chunks.append(_merge_statement_nodes(pending_statements, source))

    chunks = _merge_adjacent_imports(chunks)
    return chunks


class ASTChunker:
    @staticmethod
    def chunk(source: str, language: str | None) -> list[CodeChunk]:
        if not source.strip():
            return []
        if (
            not _HAS_TREE_SITTER
            or language is None
            or language not in CHUNK_BOUNDARY_NODES
        ):
            return IndentChunker.chunk(source, language)

        parser = _get_parser(language)
        if parser is None:
            return IndentChunker.chunk(source, language)

        tree = parser.parse(bytes(source, "utf-8"))
        return _extract_chunks(tree.root_node, source, language)


# ---------------------------------------------------------------------------
# Step 3: Indent-Based Fallback Chunker
# ---------------------------------------------------------------------------

_DEFINITION_PATTERNS = re.compile(
    r"^(def |class |function |async function |async def |export |public |private |protected |func |fn )"
)


def _is_chunk_boundary(line: str, current_lines: list[str]) -> bool:
    stripped = line.strip()
    if not stripped:
        return bool(current_lines) and current_lines[-1].strip() != ""
    if _DEFINITION_PATTERNS.match(stripped):
        return True
    if line[0:1] not in (" ", "\t", "") and current_lines:
        last_non_blank = next(
            (ln for ln in reversed(current_lines) if ln.strip()),
            None,
        )
        if last_non_blank and last_non_blank[0:1] in (" ", "\t"):
            return True
    return False


def _infer_chunk_kind(content: str) -> ChunkKind:
    first_line = content.strip().split("\n")[0].strip() if content.strip() else ""
    if re.match(r"^(def |async def )", first_line):
        return ChunkKind.FUNCTION
    if re.match(r"^class ", first_line):
        return ChunkKind.CLASS
    if re.match(r"^(import |from .+ import )", first_line):
        return ChunkKind.IMPORT
    if re.match(r"^(function |async function |export function )", first_line):
        return ChunkKind.FUNCTION
    if re.match(r"^(public |private |protected )", first_line):
        return ChunkKind.METHOD
    if re.match(r"^(func |fn )", first_line):
        return ChunkKind.FUNCTION
    return ChunkKind.UNKNOWN


def _extract_indent_signature(content: str, kind: ChunkKind) -> str:
    first_line = content.strip().split("\n")[0].strip() if content.strip() else ""
    if kind in (ChunkKind.FUNCTION, ChunkKind.METHOD):
        for delimiter in (":", "{"):
            idx = first_line.find(delimiter)
            if idx != -1:
                return first_line[: idx + 1]
        return first_line
    return first_line


def _extract_indent_name(content: str) -> str:
    first_line = content.strip().split("\n")[0].strip() if content.strip() else ""
    match = re.match(
        r"(?:def|class|function|async function|async def|func|fn)\s+(\w+)", first_line
    )
    if match:
        return match.group(1)
    return first_line[:60] if first_line else "<block>"


def _lines_to_chunk(lines: list[str], start_line: int) -> CodeChunk:
    content = "".join(lines)
    kind = _infer_chunk_kind(content)
    name = _extract_indent_name(content)
    signature = _extract_indent_signature(content, kind)
    end_line = start_line + len(lines) - 1
    return CodeChunk(
        name=name,
        kind=kind,
        start_line=start_line,
        end_line=end_line,
        content=content.rstrip("\n"),
        signature=signature,
    )


class IndentChunker:
    @staticmethod
    def chunk(source: str, language: str | None = None) -> list[CodeChunk]:
        if not source.strip():
            return []

        lines = source.splitlines(keepends=True)
        chunks: list[CodeChunk] = []
        current_lines: list[str] = []
        current_start = 1

        for i, line in enumerate(lines, 1):
            if _is_chunk_boundary(line, current_lines):
                if current_lines:
                    chunks.append(_lines_to_chunk(current_lines, current_start))
                current_lines = [line]
                current_start = i
            else:
                current_lines.append(line)

        if current_lines:
            chunks.append(_lines_to_chunk(current_lines, current_start))

        return chunks


# ---------------------------------------------------------------------------
# Step 5: Three-Level Renderer
# ---------------------------------------------------------------------------


def render_signature(chunk: CodeChunk) -> str:
    if chunk.kind == ChunkKind.CLASS:
        if chunk.children:
            return f"{chunk.signature}  # methods: {', '.join(chunk.children)}"
        return chunk.signature
    if chunk.kind in (ChunkKind.FUNCTION, ChunkKind.METHOD):
        return f"{chunk.signature}  ..."
    return chunk.signature


def render_chunk(chunk: CodeChunk, level: str) -> str:
    if level == "full":
        return chunk.content
    if level == "signature":
        return render_signature(chunk)
    return ""


def render_file_staged(
    chunks: list[CodeChunk],
    levels: dict[str, str] | Mapping[str, str],
) -> str:
    parts: list[str] = []
    consecutive_drops = 0

    for chunk in sorted(chunks, key=lambda c: c.start_line):
        level = levels.get(chunk.name, "drop")
        rendered = render_chunk(chunk, level)

        if not rendered:
            consecutive_drops += 1
            continue

        if consecutive_drops > 0:
            parts.append(f"# ... ({consecutive_drops} sections omitted)")
            consecutive_drops = 0

        parts.append(rendered)

    if consecutive_drops > 0:
        parts.append(f"# ... ({consecutive_drops} sections omitted)")

    return "\n\n".join(parts)
