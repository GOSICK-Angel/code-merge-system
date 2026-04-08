# Staged Context Processing Implementation Plan

> **前置条件**: `src/llm/context.py` + `src/llm/prompt_builders.py` 已实现 TokenBudget/ContextAssembler。
> **权威设计文档**: `doc/context-management.md` 8-13 节。
> **目标**: 用 AST 分块 + 相关性评分 + 三级渲染 替代暴力截断，保留语义完整性。

---

## 目录

1. [不可违背原则](#1-不可违背原则)
2. [实施顺序与依赖关系](#2-实施顺序与依赖关系)
3. [Step 1: CodeChunk 模型 + 语言检测](#3-step-1-codechunk-模型--语言检测)
4. [Step 2: AST Chunker (tree-sitter)](#4-step-2-ast-chunker-tree-sitter)
5. [Step 3: Indent-Based Fallback Chunker](#5-step-3-indent-based-fallback-chunker)
6. [Step 4: Relevance Scorer](#6-step-4-relevance-scorer)
7. [Step 5: Three-Level Renderer](#7-step-5-three-level-renderer)
8. [Step 6: Prompt Builder Integration](#8-step-6-prompt-builder-integration)
9. [Step 7: Agent Integration](#9-step-7-agent-integration)
10. [Step 8: Prompt Template Updates](#10-step-8-prompt-template-updates)
11. [Testing Strategy](#11-testing-strategy)
12. [File Checklist](#12-file-checklist)
13. [Rollout & Fallback](#13-rollout--fallback)

---

## 1. Not-Violable Principles

| Principle | Reason |
|-----------|--------|
| Existing `ContextAssembler.build()` signature unchanged | All current callers remain valid |
| Small files bypass staged processing | Files < 500 lines go through existing truncation path |
| CRITICAL priority sections never processed by chunker | System prompts, output schemas are opaque text |
| tree-sitter is optional dependency | If not installed, fallback to indent-based chunker |
| No LLM calls in the chunking/scoring pipeline | Chunking and scoring are pure computation, deterministic |
| Immutable models | `CodeChunk`, `RenderLevel` are frozen Pydantic / StrEnum |
| Agent prompt format backward compatible | `max_content_chars` parameter remains, staged processing is additive |

---

## 2. Implementation Order & Dependencies

```
Step 1: CodeChunk model + language detection    (no deps)
    |
Step 2: AST Chunker (tree-sitter)              (depends on Step 1)
    |
Step 3: Indent-based fallback chunker           (depends on Step 1)
    |
Step 4: Relevance Scorer                        (depends on Step 1)
    |
Step 5: Three-level renderer                    (depends on Step 1)
    |
Step 6: Prompt builder integration              (depends on Step 2-5)
    |
Step 7: Agent integration                       (depends on Step 6)
    |
Step 8: Prompt template updates                 (depends on Step 7)
```

Steps 2, 3, 4, 5 are independent of each other and can be implemented in parallel.

---

## 3. Step 1: CodeChunk Model + Language Detection

**File**: `src/llm/chunker.py` (create)

### 3.1 Models

```python
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
    start_line: int              # 1-based inclusive
    end_line: int                # 1-based inclusive
    content: str
    signature: str               # first meaningful line(s)
    children: tuple[str, ...] = ()
    byte_range: tuple[int, int] = (0, 0)
```

### 3.2 Language Detection

```python
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
```

### 3.3 Tests: `tests/unit/test_chunker.py`

- `test_chunk_kind_values` - StrEnum members
- `test_code_chunk_frozen` - immutability
- `test_detect_language_python` / `_javascript` / `_unknown`
- `test_code_chunk_signature_stored`

---

## 4. Step 2: AST Chunker (tree-sitter)

**File**: `src/llm/chunker.py` (append)

### 4.1 Dependency

```toml
# pyproject.toml [project.optional-dependencies]
ast = ["tree-sitter>=0.24", "tree-sitter-python", "tree-sitter-javascript", "tree-sitter-typescript", "tree-sitter-go", "tree-sitter-rust", "tree-sitter-java", "tree-sitter-c"]
```

Make tree-sitter an **optional** dependency group. Import guarded:

```python
_HAS_TREE_SITTER = False
try:
    import tree_sitter
    _HAS_TREE_SITTER = True
except ImportError:
    pass
```

### 4.2 Chunk Boundary Node Types

```python
CHUNK_BOUNDARY_NODES: dict[str, dict[str, ChunkKind]] = {
    "python": {
        "function_definition": ChunkKind.FUNCTION,
        "class_definition": ChunkKind.CLASS,
        "import_statement": ChunkKind.IMPORT,
        "import_from_statement": ChunkKind.IMPORT,
        "decorated_definition": ChunkKind.FUNCTION,  # re-classify by child
    },
    "javascript": {
        "function_declaration": ChunkKind.FUNCTION,
        "class_declaration": ChunkKind.CLASS,
        "method_definition": ChunkKind.METHOD,
        "arrow_function": ChunkKind.FUNCTION,
        "import_statement": ChunkKind.IMPORT,
        "export_statement": ChunkKind.STATEMENT,
    },
    # ... other languages
}
```

### 4.3 ASTChunker Class

```python
class ASTChunker:
    @staticmethod
    def chunk(source: str, language: str) -> list[CodeChunk]:
        if not _HAS_TREE_SITTER or language not in CHUNK_BOUNDARY_NODES:
            return IndentChunker.chunk(source, language)

        parser = _get_parser(language)
        tree = parser.parse(bytes(source, "utf-8"))
        return _extract_chunks(tree.root_node, source, language)
```

### 4.4 `_extract_chunks` Algorithm

```
def _extract_chunks(root, source, language):
    chunks = []
    boundary_types = CHUNK_BOUNDARY_NODES[language]
    pending_statements: list[tree_sitter.Node] = []

    for child in root.children:
        if child.type in boundary_types:
            # flush pending statements as one STATEMENT chunk
            if pending_statements:
                chunks.append(_merge_statement_nodes(pending_statements, source))
                pending_statements = []

            kind = boundary_types[child.type]
            chunk = _node_to_chunk(child, source, kind, language)
            chunks.append(chunk)
        else:
            pending_statements.append(child)

    if pending_statements:
        chunks.append(_merge_statement_nodes(pending_statements, source))

    # post-process: merge adjacent IMPORT chunks
    chunks = _merge_adjacent_imports(chunks)
    return chunks
```

### 4.5 Signature Extraction from AST Node

```python
def _extract_signature(node, source: str, kind: ChunkKind, language: str) -> str:
    if kind in (ChunkKind.FUNCTION, ChunkKind.METHOD):
        # take text from node start to first ":" (Python) or "{" (C-like)
        text = _node_text(node, source)
        for delimiter in (":", "{"):
            idx = text.find(delimiter)
            if idx != -1:
                return text[:idx + 1].strip()
        return text.split("\n")[0].strip()

    if kind == ChunkKind.CLASS:
        first_line = _node_text(node, source).split("\n")[0].strip()
        # extract child method names
        method_names = _extract_child_method_names(node, source, language)
        return first_line

    # IMPORT, STATEMENT: full text (usually short)
    return _node_text(node, source).split("\n")[0].strip()
```

### 4.6 Parser Caching

```python
_PARSER_CACHE: dict[str, tree_sitter.Parser] = {}

def _get_parser(language: str) -> tree_sitter.Parser:
    if language not in _PARSER_CACHE:
        lang_module = _load_language_module(language)
        parser = tree_sitter.Parser(tree_sitter.Language(lang_module.language()))
        _PARSER_CACHE[language] = parser
    return _PARSER_CACHE[language]
```

### 4.7 Tests: `tests/unit/test_chunker.py` (extend)

- `test_ast_chunk_python_function` - single function extracted
- `test_ast_chunk_python_class_with_methods` - class + children list
- `test_ast_chunk_imports_merged` - adjacent imports become one chunk
- `test_ast_chunk_mixed_file` - real-world Python file with imports, classes, functions
- `test_ast_chunk_preserves_line_numbers` - start_line/end_line accurate
- `test_ast_chunk_decorated_function` - decorator included in chunk
- `test_ast_chunk_fallback_when_no_treesitter` - mock import failure -> IndentChunker

---

## 5. Step 3: Indent-Based Fallback Chunker

**File**: `src/llm/chunker.py` (append)

### 5.1 IndentChunker

```python
class IndentChunker:
    @staticmethod
    def chunk(source: str, language: str | None = None) -> list[CodeChunk]:
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
```

### 5.2 Boundary Heuristics

```python
_DEFINITION_PATTERNS = re.compile(
    r"^(def |class |function |async function |export |public |private |protected |func |fn )"
)

def _is_chunk_boundary(line: str, current_lines: list[str]) -> bool:
    stripped = line.strip()
    if not stripped:
        # blank line after non-blank block = potential boundary
        return bool(current_lines) and current_lines[-1].strip() != ""
    if _DEFINITION_PATTERNS.match(stripped):
        return True
    # dedent to column 0 after indented block
    if line[0:1] not in (" ", "\t", "") and current_lines:
        last_non_blank = next(
            (l for l in reversed(current_lines) if l.strip()), None
        )
        if last_non_blank and last_non_blank[0:1] in (" ", "\t"):
            return True
    return False
```

### 5.3 Tests: `tests/unit/test_chunker.py` (extend)

- `test_indent_chunker_python` - functions separated correctly
- `test_indent_chunker_blank_line_boundary` - blank line triggers split
- `test_indent_chunker_no_false_split_in_function` - indented block stays together
- `test_indent_chunker_empty_file` - returns empty list
- `test_indent_chunker_single_line` - returns one chunk

---

## 6. Step 4: Relevance Scorer

**File**: `src/llm/relevance.py` (create)

### 6.1 Models

```python
class RenderLevel(StrEnum):
    FULL = "full"
    SIGNATURE = "signature"
    DROP = "drop"

FULL_THRESHOLD = 0.6
SIGNATURE_THRESHOLD = 0.2

@dataclass(frozen=True)
class ScoringContext:
    diff_ranges: list[tuple[int, int]]          # (start_line, end_line) of changed regions
    conflict_ranges: list[tuple[int, int]] = field(default_factory=list)
    security_patterns: list[str] = field(default_factory=list)
    referenced_names: frozenset[str] = field(default_factory=frozenset)
```

### 6.2 RelevanceScorer Class

```python
class RelevanceScorer:
    def __init__(self, context: ScoringContext) -> None:
        self._context = context

    def score_chunk(self, chunk: CodeChunk) -> float:
        score = _BASE_SCORES.get(chunk.kind, 0.05)
        score += self._diff_overlap_score(chunk)
        score += self._conflict_score(chunk)
        score += self._security_score(chunk)
        score += self._reference_score(chunk)
        score += self._entry_point_score(chunk)
        return min(1.0, score)

    def score_and_assign(
        self,
        chunks: list[CodeChunk],
        budget_tokens: int,
    ) -> dict[str, RenderLevel]:
        # Phase 1: initial scoring
        scored = [(c, self.score_chunk(c)) for c in chunks]

        # Phase 2: cross-reference boost
        full_names = {c.name for c, s in scored if s >= FULL_THRESHOLD}
        full_contents = " ".join(
            c.content for c, s in scored if s >= FULL_THRESHOLD
        )
        boosted = []
        for chunk, score in scored:
            if score < FULL_THRESHOLD and chunk.name in full_contents:
                score = min(1.0, score + 0.3)
            boosted.append((chunk, score))

        # Phase 3: assign levels
        levels = {}
        for chunk, score in boosted:
            if score >= FULL_THRESHOLD:
                levels[chunk.name] = RenderLevel.FULL
            elif score >= SIGNATURE_THRESHOLD:
                levels[chunk.name] = RenderLevel.SIGNATURE
            else:
                levels[chunk.name] = RenderLevel.DROP

        # Phase 4: budget-aware demotion
        levels = self._demote_to_fit(boosted, levels, budget_tokens)
        return levels
```

### 6.3 Scoring Factor Implementations

```python
_BASE_SCORES: dict[ChunkKind, float] = {
    ChunkKind.FUNCTION: 0.15,
    ChunkKind.METHOD: 0.15,
    ChunkKind.CLASS: 0.20,
    ChunkKind.IMPORT: 0.10,
    ChunkKind.STATEMENT: 0.05,
    ChunkKind.COMMENT: 0.00,
    ChunkKind.MODULE: 0.10,
    ChunkKind.UNKNOWN: 0.05,
}

def _diff_overlap_score(self, chunk: CodeChunk) -> float:
    chunk_range = range(chunk.start_line, chunk.end_line + 1)
    for start, end in self._context.diff_ranges:
        diff_range = range(start, end + 1)
        if _ranges_overlap(chunk_range, diff_range):
            return 0.6
        if _ranges_adjacent(chunk_range, diff_range, margin=10):
            return 0.2
    return 0.0

def _conflict_score(self, chunk: CodeChunk) -> float:
    chunk_range = range(chunk.start_line, chunk.end_line + 1)
    for start, end in self._context.conflict_ranges:
        if _ranges_overlap(chunk_range, range(start, end + 1)):
            return 0.5
    return 0.0

def _security_score(self, chunk: CodeChunk) -> float:
    content_lower = chunk.content.lower()
    for pattern in self._context.security_patterns:
        if pattern.lower() in content_lower:
            return 0.3
    return 0.0

def _reference_score(self, chunk: CodeChunk) -> float:
    if chunk.name in self._context.referenced_names:
        return 0.3
    return 0.0

def _entry_point_score(self, chunk: CodeChunk) -> float:
    entry_names = {"main", "__init__", "__main__", "constructor", "setup", "teardown"}
    if chunk.name.lower().strip("_") in entry_names:
        return 0.2
    return 0.0
```

### 6.4 Budget-Aware Demotion

```python
def _demote_to_fit(
    self,
    scored: list[tuple[CodeChunk, float]],
    levels: dict[str, RenderLevel],
    budget_tokens: int,
) -> dict[str, RenderLevel]:
    def _total_tokens() -> int:
        total = 0
        for chunk, _ in scored:
            level = levels[chunk.name]
            if level == RenderLevel.FULL:
                total += estimate_tokens(chunk.content)
            elif level == RenderLevel.SIGNATURE:
                total += estimate_tokens(chunk.signature)
        return total

    # demote FULL -> SIGNATURE (lowest score first)
    full_by_score = sorted(
        [(c, s) for c, s in scored if levels[c.name] == RenderLevel.FULL],
        key=lambda x: x[1],
    )
    for chunk, score in full_by_score:
        if _total_tokens() <= budget_tokens:
            break
        levels[chunk.name] = RenderLevel.SIGNATURE

    # demote SIGNATURE -> DROP (lowest score first)
    sig_by_score = sorted(
        [(c, s) for c, s in scored if levels[c.name] == RenderLevel.SIGNATURE],
        key=lambda x: x[1],
    )
    for chunk, score in sig_by_score:
        if _total_tokens() <= budget_tokens:
            break
        levels[chunk.name] = RenderLevel.DROP

    return levels
```

### 6.5 Tests: `tests/unit/test_relevance.py` (create)

- `test_base_score_function_vs_comment` - function > comment
- `test_diff_overlap_full` - chunk overlapping diff -> FULL
- `test_diff_adjacent_signature` - chunk near diff -> SIGNATURE
- `test_no_overlap_drop` - distant chunk -> DROP
- `test_conflict_boost` - conflict range boosts score
- `test_security_pattern_boost` - "password" in content boosts score
- `test_reference_boost` - name appearing in FULL chunk boosts
- `test_entry_point_boost` - "main" function gets bonus
- `test_budget_demotion_full_to_signature` - over budget demotes FULL
- `test_budget_demotion_signature_to_drop` - severely over budget demotes SIGNATURE
- `test_empty_chunks_returns_empty` - edge case
- `test_all_chunks_fit` - under budget, no demotion

---

## 7. Step 5: Three-Level Renderer

**File**: `src/llm/chunker.py` (append)

### 7.1 Functions

```python
def render_chunk(chunk: CodeChunk, level: RenderLevel) -> str
def render_signature(chunk: CodeChunk) -> str
def render_file_staged(
    chunks: list[CodeChunk],
    levels: dict[str, RenderLevel],
) -> str
```

See `doc/context-management.md` Section 11 for exact specification.

### 7.2 Tests: `tests/unit/test_chunker.py` (extend)

- `test_render_chunk_full` - returns content verbatim
- `test_render_chunk_signature_function` - returns `def foo(): ...`
- `test_render_chunk_signature_class` - returns class line + methods list
- `test_render_chunk_drop` - returns empty string
- `test_render_file_staged_preserves_order` - chunks in source line order
- `test_render_file_staged_drop_marker` - consecutive drops show `# ... (N sections omitted)`
- `test_render_file_staged_all_full` - no markers, complete content

---

## 8. Step 6: Prompt Builder Integration

**File**: `src/llm/prompt_builders.py` (modify)

### 8.1 Changes

Add `build_staged_content` method to `AgentPromptBuilder`:

```python
STAGED_THRESHOLD_LINES = 500
STAGED_THRESHOLD_CHARS = 15_000

def build_staged_content(
    self,
    content: str,
    file_path: str,
    diff_ranges: list[tuple[int, int]],
    budget_tokens: int,
    conflict_ranges: list[tuple[int, int]] | None = None,
    security_patterns: list[str] | None = None,
) -> str:
    line_count = content.count("\n") + 1
    if line_count < STAGED_THRESHOLD_LINES and len(content) < STAGED_THRESHOLD_CHARS:
        # small file: use existing truncation
        max_chars = int(budget_tokens * _CHARS_PER_TOKEN)
        return content[:max_chars]

    language = detect_language(file_path)
    chunks = ASTChunker.chunk(content, language)

    context = ScoringContext(
        diff_ranges=diff_ranges,
        conflict_ranges=conflict_ranges or [],
        security_patterns=security_patterns or [],
    )
    scorer = RelevanceScorer(context)
    levels = scorer.score_and_assign(chunks, budget_tokens)

    return render_file_staged(chunks, levels)
```

### 8.2 Tests: `tests/unit/test_context.py` (extend)

- `test_build_staged_content_small_file_passthrough` - < threshold, returns truncated
- `test_build_staged_content_large_file_uses_staging` - > threshold, returns rendered

---

## 9. Step 7: Agent Integration

### 9.1 ConflictAnalystAgent (`src/agents/conflict_analyst_agent.py`)

Modify `analyze_file` to call `build_staged_content` for `base_content`, `current_content`, `target_content` before passing to `build_conflict_analysis_prompt`.

Key change in `analyze_file`:
```python
if builder and content_budget:
    diff_ranges = _extract_diff_ranges(file_diff)
    current_content = builder.build_staged_content(
        current_content, file_diff.file_path, diff_ranges, content_budget // 2
    )
    target_content = builder.build_staged_content(
        target_content, file_diff.file_path, diff_ranges, content_budget // 2
    )
    if base_content:
        base_content = builder.build_staged_content(
            base_content, file_diff.file_path, diff_ranges, content_budget // 4
        )
```

### 9.2 JudgeAgent (`src/agents/judge_agent.py`)

Modify `review_file` to stage `merged_content` before building prompt.

### 9.3 ExecutorAgent (`src/agents/executor_agent.py`)

Modify `execute_semantic_merge` to stage `current_content` and `target_content`.

### 9.4 Helper: Extract Diff Ranges

```python
def _extract_diff_ranges(file_diff: FileDiff) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    if hasattr(file_diff, "hunks") and file_diff.hunks:
        for hunk in file_diff.hunks:
            ranges.append((hunk.start_line, hunk.end_line))
    elif file_diff.lines_added > 0 or file_diff.lines_deleted > 0:
        # rough estimate: whole file is relevant
        ranges.append((1, file_diff.lines_added + file_diff.lines_deleted + 100))
    return ranges
```

### 9.5 Tests

- Existing agent tests should continue passing (backward compatible)
- Add integration tests verifying staged content is shorter than raw content for large files

---

## 10. Step 8: Prompt Template Updates

### 10.1 `analyst_prompts.py`

`build_conflict_analysis_prompt` unchanged in signature. Content arrives pre-staged.

Add a note in the prompt template when staged content is used:

```python
# If content was staged, add a note
if "sections omitted" in current_section:
    current_section += "\n(Note: some unrelated code sections are summarized or omitted for context efficiency)"
```

### 10.2 `judge_prompts.py`

`build_file_review_prompt` unchanged. The `_truncate_content` call remains as final safety net but should rarely activate since content arrives pre-staged.

### 10.3 `executor_prompts.py`

`build_semantic_merge_prompt` unchanged in signature. Content arrives pre-staged.

---

## 11. Testing Strategy

### 11.1 Unit Tests (new files)

| File | Coverage Target | Key Tests |
|------|----------------|-----------|
| `tests/unit/test_chunker.py` | AST chunking, indent fallback, rendering | 20+ tests |
| `tests/unit/test_relevance.py` | Scoring factors, budget demotion, edge cases | 15+ tests |

### 11.2 Unit Tests (extend existing)

| File | New Tests |
|------|-----------|
| `tests/unit/test_context.py` | `build_staged_content` integration |

### 11.3 Test Fixtures

Create `tests/fixtures/` with sample code files:

```
tests/fixtures/
  sample_large_python.py    # ~100 lines, 5 functions, 2 classes
  sample_large_js.js        # ~80 lines, mixed exports
  sample_unsupported.txt    # plain text, tests fallback
```

### 11.4 TDD Order

For each step:
1. Write test first (RED)
2. Run test - confirm FAIL
3. Implement (GREEN)
4. Run test - confirm PASS
5. Refactor if needed

---

## 12. File Checklist

### New Files

| File | Step | Description |
|------|------|-------------|
| `src/llm/chunker.py` | 1-3, 5 | CodeChunk model, ASTChunker, IndentChunker, render functions |
| `src/llm/relevance.py` | 4 | RelevanceScorer, RenderLevel, ScoringContext |
| `src/llm/queries/python-tags.scm` | 2 | tree-sitter Python query (optional, can use node types directly) |
| `tests/unit/test_chunker.py` | 1-3, 5 | Chunker and renderer tests |
| `tests/unit/test_relevance.py` | 4 | Scorer tests |
| `tests/fixtures/sample_large_python.py` | 11 | Test fixture |
| `tests/fixtures/sample_large_js.js` | 11 | Test fixture |

### Modified Files

| File | Step | Change |
|------|------|--------|
| `src/llm/prompt_builders.py` | 6 | Add `build_staged_content()` |
| `src/agents/conflict_analyst_agent.py` | 7 | Stage three-way diff content |
| `src/agents/judge_agent.py` | 7 | Stage merged content |
| `src/agents/executor_agent.py` | 7 | Stage current/target content |
| `pyproject.toml` | 2 | Add `tree-sitter` optional dependency group |
| `tests/unit/test_context.py` | 6 | Add staged content tests |

### Unchanged Files

| File | Reason |
|------|--------|
| `src/llm/context.py` | `ContextAssembler` not modified; staged processing is a layer above |
| `src/llm/prompts/analyst_prompts.py` | Receives pre-staged content, no signature change |
| `src/llm/prompts/executor_prompts.py` | Receives pre-staged content, no signature change |
| `src/llm/prompts/judge_prompts.py` | `_truncate_content` remains as safety net |
| `src/agents/base_agent.py` | No change needed |

---

## 13. Rollout & Fallback

### 13.1 Feature Gate

Staged processing activates only when ALL conditions are met:
1. File exceeds `STAGED_THRESHOLD_LINES` (500) or `STAGED_THRESHOLD_CHARS` (15,000)
2. Language is detectable from file extension
3. tree-sitter is installed (otherwise: indent fallback)

### 13.2 Graceful Degradation

```
tree-sitter installed + supported language  -> AST chunking (best quality)
tree-sitter NOT installed                   -> indent-based chunking (acceptable)
indent chunking produces 0 chunks           -> existing truncation (fallback)
```

### 13.3 Observability

Add logging at key stages:
```python
logger.info(
    "Staged processing: file=%s, chunks=%d, full=%d, signature=%d, drop=%d, tokens=%d/%d",
    file_path, len(chunks), full_count, sig_count, drop_count, used_tokens, budget_tokens,
)
```

### 13.4 Configuration (Future)

If needed, add to `MergeConfig`:
```python
class StagedProcessingConfig(BaseModel):
    enabled: bool = True
    threshold_lines: int = 500
    threshold_chars: int = 15_000
    full_threshold: float = 0.6
    signature_threshold: float = 0.2
```

This is **not** implemented in this phase. The thresholds are constants in code. Configuration is a future enhancement if tuning is needed.
