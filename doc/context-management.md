# LLM Context Management Design

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [Design Goals](#2-design-goals)
3. [Architecture Overview](#3-architecture-overview)
4. [Token Estimation](#4-token-estimation)
5. [Token Budget](#5-token-budget)
6. [Context Priority System](#6-context-priority-system)
7. [Context Assembler](#7-context-assembler)
8. [Staged Content Processing](#8-staged-content-processing)
9. [AST Chunker](#9-ast-chunker)
10. [Relevance Scorer](#10-relevance-scorer)
11. [Three-Level Rendering](#11-three-level-rendering)
12. [Prompt Builders](#12-prompt-builders)
13. [Agent Integration](#13-agent-integration)
14. [Model Registry](#14-model-registry)

---

## 1. Problem Statement

The system makes LLM calls across 5 agents with varying prompt sizes. Before this design, context management was ad-hoc:

| Issue | Location | Impact |
|-------|----------|--------|
| Hardcoded truncation `[:5000]` | `judge_prompts.py:51` | Wastes 195K tokens on Claude Opus; too aggressive for GPT-4 |
| No token counting | `base_agent.py` | Character count logged but no token estimation or budget check |
| No prompt size warning | All agents | Silently exceeds context window, causing API errors |
| File content passed verbatim | `executor_prompts.py`, `analyst_prompts.py` | Three-way diffs on large files can exceed any model's context |
| Blind truncation loses semantics | `_truncate_text()` | Cutting code mid-function destroys merge analysis quality |

### Why Truncation Is Insufficient

Truncation (tail/head/middle) treats code as flat text. A 2000-line file truncated at line 800 may:

- Cut a critical class definition in half
- Lose the only function modified by the upstream diff
- Discard return types and error handling at the file's end
- Keep 500 lines of imports while dropping all business logic

The core insight from industry research (Aider, Continue.dev, Moatless-tools, Sweep): **code has structure, and that structure should drive what to keep, compress, or drop**.

---

## 2. Design Goals

| Goal | Mechanism |
|------|-----------|
| Model-aware budgeting | Map model name to context window size |
| Token estimation without dependencies | `chars / 3.5` approximation, no tiktoken required |
| Priority-based allocation | Critical sections never dropped; optional sections dropped first |
| **AST-aware chunking** | Split code by function/class/statement boundaries, not character count |
| **Selective rendering** | Three levels: full content, signature-only, drop — no uniform compression |
| **Diff-driven relevance** | Chunks overlapping with diff changes ranked higher |
| Dynamic truncation | Adapt content size to actual model capacity |
| Safety margin | Reserve 5% of context window as buffer |
| Non-breaking integration | All new parameters have defaults; existing behavior preserved |

---

## 3. Architecture Overview

```
                        File Content (raw)
                              |
                    +---------+---------+
                    |   AST Chunker     |  tree-sitter parse + .scm queries
                    |   (chunker.py)    |  -> CodeChunk[]
                    +---------+---------+
                              |
                    +---------+---------+
                    | Relevance Scorer  |  diff overlap + security + conflict analysis
                    | (relevance.py)    |  -> RenderLevel per chunk (FULL/SIGNATURE/DROP)
                    +---------+---------+
                              |
                    +---------+---------+
                    | Three-Level       |  FULL: verbatim
                    | Renderer          |  SIGNATURE: def foo(x) -> int: ...
                    | (chunker.py)      |  DROP: omitted
                    +---------+---------+
                              |
              +---------------+---------------+
              |                               |
    +---------+---------+           +---------+---------+
    | ContextAssembler  |           | AgentPromptBuilder|
    | (context.py)      |           | (prompt_builders) |
    | budget + priority |           | memory + budget   |
    +---------+---------+           +---------+---------+
              |                               |
              +---------------+---------------+
                              |
                        Final Prompt
```

### File Layout

```
src/llm/
  context.py          # TokenBudget, ContextAssembler, ContextSection, ContextPriority
  chunker.py          # ASTChunker, CodeChunk, render_chunk(), fallback chunker
  relevance.py        # RelevanceScorer, RenderLevel, scoring factors
  prompt_builders.py  # AgentPromptBuilder (integrates memory + budget + staged processing)
```

---

## 4. Token Estimation

Location: `src/llm/context.py`

```python
_CHARS_PER_TOKEN = 3.5

def estimate_tokens(text: str) -> int:
    return int(len(text) / _CHARS_PER_TOKEN)
```

This is a conservative estimate:
- English text averages ~4 chars/token
- Code averages ~3-4 chars/token
- Using 3.5 slightly overestimates, providing a safety buffer
- No external dependency (tiktoken not required)

---

## 5. Token Budget

Location: `src/llm/context.py`

```python
class TokenBudget(BaseModel, frozen=True):
    model: str
    context_window: int          # total context window (e.g., 200_000)
    reserved_for_output: int     # max_tokens from AgentLLMConfig (e.g., 8_192)
    used: int = 0                # tokens consumed so far
```

### Properties and Methods

| Member | Type | Description |
|--------|------|-------------|
| `available` | `property` | `context_window - output - used - 5% margin` |
| `consume(tokens)` | `method` | Returns new budget with `used += tokens` (immutable) |
| `can_fit(tokens)` | `method` | Returns `tokens <= available` |

### Budget Calculation

```
available = context_window
          - reserved_for_output
          - used
          - (context_window * 0.05)  // 5% safety margin

Example (Claude Opus, default config):
  200,000 - 8,192 - 0 - 10,000 = 181,808 available tokens
```

---

## 6. Context Priority System

```python
class ContextPriority(IntEnum):
    CRITICAL = 0    # System prompt, output schema -- never dropped
    HIGH = 1        # File content being processed -- last to truncate
    MEDIUM = 2      # Memory context, phase summaries -- truncated if needed
    LOW = 3         # Full diffs, supplementary examples -- truncated early
    OPTIONAL = 4    # Nice-to-have context -- dropped first
```

### Typical Priority Assignment by Agent

| Agent | CRITICAL | HIGH | MEDIUM | LOW |
|-------|----------|------|--------|-----|
| Judge | System prompt | Merged file content | Memory context | Original diff stats |
| Executor | System prompt | Current + target content | Memory context | Conflict analysis details |
| Analyst | System prompt | Three-way diff (current/target) | Memory context | Base version content |

---

## 7. Context Assembler

Location: `src/llm/context.py`

### ContextSection

```python
class ContextSection(BaseModel):
    name: str                    # identifier for logging
    content: str                 # the text content
    priority: ContextPriority    # determines drop/truncation order
    min_tokens: int = 0          # minimum to keep if truncated (0 = droppable)
    can_truncate: bool = True    # False = drop entirely or keep entirely
    truncation_strategy: Literal["tail", "head", "middle"] = "tail"
```

### Truncation Strategies

| Strategy | Behavior | Use Case |
|----------|----------|----------|
| `tail` | Keep first N chars, truncate end | System prompts, instructions |
| `head` | Keep last N chars, truncate beginning | Log output, stack traces |
| `middle` | Keep first N/2 + last N/2 | File content (keep imports + tail) |

### Assembly Algorithm

```python
assembler = ContextAssembler(budget)
assembler.add_section(ContextSection(name="system", content=..., priority=CRITICAL))
assembler.add_section(ContextSection(name="file", content=..., priority=HIGH))
assembler.add_section(ContextSection(name="memory", content=..., priority=MEDIUM))
result_text, final_budget = assembler.build()
```

`build()` algorithm:

1. Sort all sections by priority (CRITICAL first)
2. Calculate total tokens for all sections
3. If total <= budget.available: return all sections joined
4. Otherwise, iterate from OPTIONAL to LOW to MEDIUM:
   - For each section at current priority level:
     - If `can_truncate=True`: truncate to `min_tokens` or to fit budget
     - If `can_truncate=False`: drop entirely (set tokens to 0)
   - Stop when excess <= 0
5. CRITICAL sections are never touched
6. Join remaining sections with `\n\n` separator
7. Return (joined text, consumed budget)

---

## 8. Staged Content Processing

### 8.1 Problem

When a single file (e.g., 3000 lines of Python) is passed to an agent, the `ContextAssembler` treats it as one opaque blob. It can only truncate at character boundaries, losing semantic structure. The file may contain 50 functions, but only 3 overlap with the actual diff.

### 8.2 Solution: Chunk -> Score -> Render Pipeline

Inspired by industry best practices:

| Framework | Technique | Key Insight |
|-----------|-----------|-------------|
| **Aider** | tree-sitter `.scm` queries + PageRank | Code graph ranking; definitions vs references |
| **Continue.dev** | AST node boundaries + progressive collapse | Signature + folded body `{ ... }` as middle ground |
| **Moatless-tools** | Span visibility + dependency graph | Show related spans, hide others with summary comments |
| **Sweep** | Recursive AST descent + term extraction | Hierarchical preview: small nodes full, large nodes collapsed |

Our design combines the strongest elements:

```
Stage 1: CHUNK    -- tree-sitter AST -> CodeChunk[] per file
Stage 2: SCORE    -- diff-driven relevance -> RenderLevel per chunk
Stage 3: RENDER   -- three-level output -> assembled text within budget
```

### 8.3 When Staged Processing Activates

Staged processing is **not applied universally**. It activates only when:

1. File content exceeds `staged_processing_threshold` (default: 500 lines or 15,000 chars)
2. The file's language is supported by tree-sitter (Python, JS, TS, Go, Rust, Java, C, C++)
3. The agent's budget cannot fit the full content

Small files and unsupported languages continue to use the existing `ContextAssembler` truncation path.

### 8.4 Data Flow Example

```
Input: utils.py (2500 lines, ~180 functions)
Diff:  lines 120-135 changed, lines 890-920 added

Stage 1 - AST Chunker:
  chunk[0]: imports (lines 1-45)           kind=IMPORT
  chunk[1]: class Config (lines 47-120)    kind=CLASS
  chunk[2]: def parse_args (lines 122-180) kind=FUNCTION  <- overlaps diff
  chunk[3]: def validate (lines 182-250)   kind=FUNCTION
  ...
  chunk[28]: class Router (lines 870-950)  kind=CLASS     <- overlaps diff
  ...
  chunk[45]: def main (lines 2480-2500)    kind=FUNCTION

Stage 2 - Relevance Scorer:
  chunk[0]:  SIGNATURE  (imports: keep list, drop bodies)
  chunk[2]:  FULL       (overlaps diff change range)
  chunk[3]:  DROP       (no overlap, no references from changed code)
  chunk[28]: FULL       (overlaps diff addition range)
  chunk[45]: SIGNATURE  (entry point, keep signature)
  ... (remaining 40 chunks: mostly DROP or SIGNATURE)

Stage 3 - Renderer:
  Total FULL chunks:      ~800 tokens (2 chunks)
  Total SIGNATURE chunks: ~200 tokens (8 chunks)
  Total DROP chunks:      0 tokens (35 chunks)
  Final rendered content:  ~1000 tokens (vs. ~70,000 tokens for full file)
```

---

## 9. AST Chunker

Location: `src/llm/chunker.py`

### 9.1 CodeChunk Model

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
    name: str                    # e.g., "class Router", "def parse_args"
    kind: ChunkKind
    start_line: int
    end_line: int
    content: str                 # full verbatim text of the chunk
    signature: str               # first meaningful line(s): "def foo(x: int) -> str:"
    children: tuple[str, ...]    # names of child definitions (for classes)
    byte_range: tuple[int, int]  # (start_byte, end_byte) from tree-sitter
```

### 9.2 tree-sitter Integration

**Supported languages and chunk boundary node types:**

| Language | Function nodes | Class nodes | Import nodes |
|----------|---------------|-------------|--------------|
| Python | `function_definition` | `class_definition` | `import_statement`, `import_from_statement` |
| JavaScript/TS | `function_declaration`, `arrow_function`, `method_definition` | `class_declaration` | `import_statement` |
| Go | `function_declaration`, `method_declaration` | `type_declaration` (struct) | `import_declaration` |
| Rust | `function_item`, `impl_item` | `struct_item`, `enum_item` | `use_declaration` |
| Java | `method_declaration`, `constructor_declaration` | `class_declaration`, `interface_declaration` | `import_declaration` |
| C/C++ | `function_definition` | `struct_specifier`, `class_specifier` | `preproc_include` |

**Query pattern (Python example):**

```scheme
;; src/llm/queries/python-tags.scm
(function_definition name: (identifier) @name.definition.function)
(class_definition name: (identifier) @name.definition.class)
(import_statement) @name.definition.import
(import_from_statement) @name.definition.import
```

### 9.3 Chunking Algorithm

```
parse(source_code, language) -> tree
for each top-level child in tree.root_node:
    if node.type in CHUNK_BOUNDARY_TYPES[language]:
        extract CodeChunk(name, kind, lines, content, signature)
    elif node.type is class-like:
        extract class chunk with children = [method names]
    else:
        accumulate into current "statement" chunk

merge adjacent import nodes into one IMPORT chunk
merge adjacent small statements (< 3 lines) into one STATEMENT chunk
```

### 9.4 Signature Extraction

Each chunk stores a `signature` -- the minimal text that identifies its purpose:

| Kind | Signature rule | Example |
|------|---------------|---------|
| FUNCTION | First line up to `:` or `{` | `def parse_args(config: Config) -> Args:` |
| CLASS | Class line + first-level method names | `class Router:\n  get(), post(), delete()` |
| METHOD | First line up to `:` or `{` | `async def handle_request(self, req):` |
| IMPORT | Full import text (usually short) | `from pathlib import Path` |
| STATEMENT | First line only | `MAX_RETRIES = 3` |

### 9.5 Fallback: Indent-Based Chunking

For unsupported languages (no tree-sitter grammar available), a heuristic chunker:

```
scan lines:
  if line is blank after non-blank block -> chunk boundary
  if dedent to column 0 after indented block -> chunk boundary
  if line matches /^(def |class |function |public |private )/ -> chunk boundary
```

This produces lower-quality chunks but avoids failing on unknown languages.

---

## 10. Relevance Scorer

Location: `src/llm/relevance.py`

### 10.1 RenderLevel

```python
class RenderLevel(StrEnum):
    FULL = "full"            # keep entire chunk content verbatim
    SIGNATURE = "signature"  # keep only the signature line(s)
    DROP = "drop"            # omit chunk entirely from output
```

### 10.2 Scoring Algorithm

Each chunk receives a relevance score (0.0 to 1.0), then mapped to a `RenderLevel`:

```python
score = base_score(chunk.kind)
      + diff_overlap_bonus(chunk, diff_ranges)
      + conflict_bonus(chunk, conflict_ranges)
      + security_bonus(chunk, security_patterns)
      + reference_bonus(chunk, referenced_names)

if score >= FULL_THRESHOLD (0.6):     -> FULL
elif score >= SIGNATURE_THRESHOLD (0.2): -> SIGNATURE
else:                                    -> DROP
```

### 10.3 Scoring Factors

| Factor | Weight | Description |
|--------|--------|-------------|
| `base_score` | varies | FUNCTION=0.15, CLASS=0.2, IMPORT=0.1, STATEMENT=0.05, COMMENT=0.0 |
| `diff_overlap` | +0.6 | Chunk's line range overlaps with diff changed/added lines |
| `diff_adjacent` | +0.2 | Chunk is within 10 lines of a diff range (context) |
| `conflict_region` | +0.5 | Chunk contains or is contained by a conflict marker region |
| `security_sensitive` | +0.3 | Chunk matches security-sensitive patterns (auth, crypto, etc.) |
| `referenced_by_diff` | +0.3 | Chunk defines a name that appears in a diff-overlapping chunk |
| `entry_point` | +0.2 | Chunk is `main`, `__init__`, constructor, or module-level executable |

### 10.4 Reference Discovery

To support the `referenced_by_diff` factor, the scorer performs a lightweight cross-reference:

```
1. Collect all names defined in FULL-scored chunks
2. For each SIGNATURE/DROP chunk, check if its name appears in any FULL chunk's content
3. If referenced, boost score by +0.3
```

This captures cases like: a helper function `_validate_input()` that isn't in the diff range but is called by a function that is.

### 10.5 Budget-Aware Demotion

After initial scoring, if total rendered tokens exceed the budget:

```
while total_tokens > budget:
    find the FULL chunk with lowest score
    demote it to SIGNATURE
    recalculate total_tokens

    if still over budget:
        find the SIGNATURE chunk with lowest score
        demote it to DROP
        recalculate total_tokens
```

This greedy demotion ensures the budget is respected while keeping the most relevant content.

---

## 11. Three-Level Rendering

Location: `src/llm/chunker.py` (render functions)

### 11.1 Render Rules

```python
def render_chunk(chunk: CodeChunk, level: RenderLevel) -> str:
    if level == RenderLevel.FULL:
        return chunk.content
    if level == RenderLevel.SIGNATURE:
        return render_signature(chunk)
    return ""  # DROP
```

### 11.2 Signature Rendering Format

```python
def render_signature(chunk: CodeChunk) -> str:
    if chunk.kind == ChunkKind.CLASS:
        # class name + child method list
        return f"{chunk.signature}  # methods: {', '.join(chunk.children)}"
    if chunk.kind in (ChunkKind.FUNCTION, ChunkKind.METHOD):
        return f"{chunk.signature}  ..."
    return chunk.signature
```

**Output examples:**

FULL (verbatim):
```python
def parse_args(config: Config) -> Args:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    return Args(verbose=args.verbose, config=args.config)
```

SIGNATURE:
```python
def parse_args(config: Config) -> Args:  ...
```

CLASS with children:
```python
class Router:  # methods: get, post, delete, _match_route
```

### 11.3 Assembly Output

Chunks are assembled in source order (by `start_line`), preserving the file's logical structure:

```python
def render_file_staged(
    chunks: list[CodeChunk],
    levels: dict[str, RenderLevel],
) -> str:
    parts: list[str] = []
    consecutive_drops = 0

    for chunk in sorted(chunks, key=lambda c: c.start_line):
        level = levels.get(chunk.name, RenderLevel.DROP)
        rendered = render_chunk(chunk, level)

        if not rendered:
            consecutive_drops += 1
            continue

        if consecutive_drops > 0:
            parts.append(f"# ... ({consecutive_drops} sections omitted)")
            consecutive_drops = 0

        parts.append(rendered)

    return "\n\n".join(parts)
```

---

## 12. Prompt Builders

Location: `src/llm/prompt_builders.py`

### AgentPromptBuilder

```python
class AgentPromptBuilder:
    def __init__(self, llm_config: AgentLLMConfig, memory_store: MemoryStore | None = None)
```

| Method | Description |
|--------|-------------|
| `compute_content_budget(fixed_prompt_text)` | Returns max chars available for variable content after accounting for fixed prompt overhead |
| `build_memory_context_text(file_paths)` | Queries memory store, formats relevant entries + phase insights as text |
| `build_staged_content(content, file_path, diff_ranges, budget_tokens)` | **NEW**: Runs chunk->score->render pipeline, returns rendered text within budget |

### Content Budget Calculation

```python
def compute_content_budget(self, fixed_prompt_text: str) -> int:
    fixed_tokens = estimate_tokens(fixed_prompt_text)
    available = self.budget.available - fixed_tokens
    return max(0, int(available * 3.5))  # convert back to chars
```

### Staged Content Flow

```python
def build_staged_content(
    self,
    content: str,
    file_path: str,
    diff_ranges: list[tuple[int, int]],
    budget_tokens: int,
    conflict_ranges: list[tuple[int, int]] | None = None,
) -> str:
    # Skip staged processing for small files
    if len(content.splitlines()) < STAGED_THRESHOLD_LINES:
        return content[:int(budget_tokens * _CHARS_PER_TOKEN)]

    # Stage 1: Chunk
    language = detect_language(file_path)
    chunks = ASTChunker.chunk(content, language)

    # Stage 2: Score
    scorer = RelevanceScorer(diff_ranges, conflict_ranges)
    levels = scorer.score_and_assign(chunks, budget_tokens)

    # Stage 3: Render
    return render_file_staged(chunks, levels)
```

---

## 13. Agent Integration

### BaseAgent Token Warning

Location: `src/agents/base_agent.py`

Every LLM call now estimates tokens and logs a warning if the prompt exceeds the budget:

```python
estimated_tokens = estimate_tokens("".join(m.get("content", "") for m in messages))
budget = self._get_token_budget()
if not budget.can_fit(estimated_tokens):
    self.logger.warning(
        "Prompt (%d est. tokens) exceeds budget (%d available) for %s",
        estimated_tokens, budget.available, self.llm_config.model
    )
```

### ConflictAnalystAgent: Three-Way Diff Staging

The analyst processes base/current/target versions. For large files:

```python
# Each version processed through staged pipeline independently
# diff_ranges derived from FileDiff.lines_added / lines_deleted ranges
staged_current = builder.build_staged_content(
    current_content, file_path, diff_ranges, content_budget // 2
)
staged_target = builder.build_staged_content(
    target_content, file_path, diff_ranges, content_budget // 2
)
# base_content gets lower budget (MEDIUM priority)
staged_base = builder.build_staged_content(
    base_content, file_path, diff_ranges, content_budget // 4
)
```

### JudgeAgent: Merged Content Staging

The judge reviews the merged file with context from the original diff:

```python
max_content_chars = builder.compute_content_budget(JUDGE_SYSTEM + memory_context)
staged_merged = builder.build_staged_content(
    merged_content, file_path, diff_ranges, max_content_chars
)
```

### ExecutorAgent: Semantic Merge Staging

The executor receives full content for the diff-affected regions but signatures for the rest:

```python
staged_current = builder.build_staged_content(
    current_content, file_path, diff_ranges, content_budget // 2
)
staged_target = builder.build_staged_content(
    target_content, file_path, diff_ranges, content_budget // 2
)
```

---

## 14. Model Registry

Location: `src/llm/context.py`

```python
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "claude-opus-4-6":             200_000,
    "claude-sonnet-4-6":           200_000,
    "claude-haiku-4-5-20251001":   200_000,
    "claude-3-5-sonnet-20241022":  200_000,
    "claude-3-5-haiku-20241022":   200_000,
    "gpt-4o":                      128_000,
    "gpt-4o-mini":                 128_000,
    "gpt-4-turbo":                 128_000,
    "gpt-4":                         8_192,
    "gpt-3.5-turbo":                16_385,
}
```

Lookup strategy:
1. Exact match on model name
2. Prefix match (e.g., `claude-opus-4-6-latest` matches `claude-opus-4-6`)
3. Fallback: `8,000` tokens (safe default for unknown models)

To add a new model, add an entry to `MODEL_CONTEXT_WINDOWS`. No other code changes needed.
