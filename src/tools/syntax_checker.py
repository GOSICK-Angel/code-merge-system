import ast
import json

import yaml as yaml_lib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class SyntaxError_:
    line: int
    column: int
    message: str


@dataclass(frozen=True)
class SyntaxCheckResult:
    valid: bool
    errors: list[SyntaxError_] = field(default_factory=list)
    language: str = "unknown"


def check_syntax(file_path: str, content: str) -> SyntaxCheckResult:
    """Check syntax of file content based on file extension.

    Python/JSON/YAML get a real parser. Brace-language files (TS/JS/Go/Java/
    Rust/C-family) get a conservative, comment/string/regex-aware bracket-balance
    + unterminated-string check (``_check_balanced``) — this is the only
    always-on per-file gate, and historically it returned ``valid=True`` for
    every non-Python/JSON/YAML extension, letting truncated / elided / brace-
    imbalanced LLM merge output reach COMPLETED. It is deliberately NOT a full
    parser: a real type/parse check is delegated to the configurable
    ``build_check`` toolchain gate (``tsc --noEmit`` / ``go build`` / ...).
    Unknown extensions still degrade to ``valid=True``.
    """
    ext = Path(file_path).suffix.lower()

    if ext == ".py":
        return _check_python(content)
    if ext == ".json":
        return _check_json(content)
    if ext in (".yaml", ".yml"):
        return _check_yaml(content)

    spec = _BALANCE_SPECS.get(ext)
    if spec is not None:
        return _check_balanced(content, spec, language=_ext_to_language(ext))

    return SyntaxCheckResult(valid=True, errors=[], language=_ext_to_language(ext))


def _check_python(content: str) -> SyntaxCheckResult:
    try:
        ast.parse(content)
        return SyntaxCheckResult(valid=True, errors=[], language="python")
    except SyntaxError as e:
        err = SyntaxError_(
            line=e.lineno or 0,
            column=e.offset or 0,
            message=str(e.msg) if hasattr(e, "msg") else str(e),
        )
        return SyntaxCheckResult(valid=False, errors=[err], language="python")


def _check_json(content: str) -> SyntaxCheckResult:
    # Empty / whitespace-only input is "no content to check", not a
    # syntax error. JSON's strict spec rejects empty strings, but in
    # this codebase Judge feeds in worktree files (including SKIP'd
    # ones that may be missing/empty) — treating them as critical
    # syntax errors leaks placeholder HumanDecisionRequest entries.
    if not content.strip():
        return SyntaxCheckResult(valid=True, errors=[], language="json")
    try:
        json.loads(content)
        return SyntaxCheckResult(valid=True, errors=[], language="json")
    except json.JSONDecodeError as e:
        err = SyntaxError_(line=e.lineno, column=e.colno, message=e.msg)
        return SyntaxCheckResult(valid=False, errors=[err], language="json")


def _check_yaml(content: str) -> SyntaxCheckResult:
    if not content.strip():
        return SyntaxCheckResult(valid=True, errors=[], language="yaml")
    try:
        yaml_lib.safe_load(content)
        return SyntaxCheckResult(valid=True, errors=[], language="yaml")
    except yaml_lib.YAMLError as e:
        line = 0
        col = 0
        msg = str(e)
        if hasattr(e, "problem_mark") and e.problem_mark is not None:
            line = e.problem_mark.line + 1
            col = e.problem_mark.column + 1
        err = SyntaxError_(line=line, column=col, message=msg)
        return SyntaxCheckResult(valid=False, errors=[err], language="yaml")


# ---------------------------------------------------------------------------
# Conservative bracket-balance checker for brace-family languages.
#
# Goal: catch the GROSS structural breakage an LLM merge produces when it
# truncates or elides — missing closing brace(s) at EOF, a stray closer, an
# unterminated string / block comment. NOT a parser: it must never false-
# positive on valid source, so anything genuinely ambiguous (a regex it
# cannot disambiguate) is resolved in favour of "valid". The acceptance bar
# is "zero false positives on the real target tree" (validated against the
# full zod TS tree, which is dense with `/{n,m}/` regex quantifiers and
# template literals).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _BalanceSpec:
    line_comments: tuple[str, ...]
    block_comment: tuple[str, str] | None  # (start, end)
    block_comment_nestable: bool
    string_delims: tuple[str, ...]  # treated as escape-aware "…" strings
    raw_backtick: bool  # ` ` JS/TS template OR Go raw string (no \\ escapes)
    single_quote_string: bool  # '…' is a string/char literal (False for Rust)
    regex: bool  # JS/TS-style /…/ regex literals in expression position


_TS_SPEC = _BalanceSpec(
    line_comments=("//",),
    block_comment=("/*", "*/"),
    block_comment_nestable=False,
    string_delims=('"',),
    raw_backtick=True,
    single_quote_string=True,
    regex=True,
)
_GO_SPEC = _BalanceSpec(
    line_comments=("//",),
    block_comment=("/*", "*/"),
    block_comment_nestable=False,
    string_delims=('"',),
    raw_backtick=True,  # Go raw string literal (no escapes inside)
    single_quote_string=True,  # rune literal '…'
    regex=False,
)
_RUST_SPEC = _BalanceSpec(
    line_comments=("//",),
    block_comment=("/*", "*/"),
    block_comment_nestable=True,
    string_delims=('"',),
    raw_backtick=False,
    single_quote_string=False,  # '… is a lifetime, NOT a string — ambiguous
    regex=False,
)
_JAVA_SPEC = _BalanceSpec(
    line_comments=("//",),
    block_comment=("/*", "*/"),
    block_comment_nestable=False,
    string_delims=('"',),
    raw_backtick=False,
    single_quote_string=True,  # char literal '…'
    regex=False,
)

_BALANCE_SPECS: dict[str, _BalanceSpec] = {
    ".ts": _TS_SPEC,
    ".tsx": _TS_SPEC,
    ".js": _TS_SPEC,
    ".jsx": _TS_SPEC,
    ".mjs": _TS_SPEC,
    ".cjs": _TS_SPEC,
    ".go": _GO_SPEC,
    ".rs": _RUST_SPEC,
    ".java": _JAVA_SPEC,
    ".kt": _JAVA_SPEC,
}

_OPENERS = {"{": "}", "(": ")", "[": "]"}
_CLOSERS = {"}": "{", ")": "(", "]": "["}

# Characters whose presence as the last non-space token before a `/` means the
# `/` begins a regex literal (expression position) rather than division. Kept
# permissive: when in doubt we DON'T enter regex mode, but a `/` that is not a
# regex is just division and harmless to bracket counting either way.
_REGEX_PRECEDERS = set("(,=:[!&|?{};~+-*%<>^")


def _check_balanced(
    content: str, spec: _BalanceSpec, language: str
) -> SyntaxCheckResult:
    if not content.strip():
        return SyntaxCheckResult(valid=True, errors=[], language=language)

    stack: list[tuple[str, int]] = []  # (opener_char, line_no)
    line = 1
    i = 0
    n = len(content)
    prev_significant = ""  # last non-space, non-comment char seen (for regex)

    def _fail(msg: str, at_line: int) -> SyntaxCheckResult:
        return SyntaxCheckResult(
            valid=False,
            errors=[SyntaxError_(line=at_line, column=0, message=msg)],
            language=language,
        )

    while i < n:
        c = content[i]

        if c == "\n":
            line += 1
            i += 1
            continue

        # Line comment.
        if any(content.startswith(lc, i) for lc in spec.line_comments):
            nl = content.find("\n", i)
            if nl == -1:
                break
            line += content.count("\n", i, nl)
            i = nl
            continue

        # Block comment (optionally nestable, e.g. Rust).
        if spec.block_comment and content.startswith(spec.block_comment[0], i):
            start_line = line
            start, end = spec.block_comment
            depth = 1
            i += len(start)
            while i < n and depth > 0:
                if spec.block_comment_nestable and content.startswith(start, i):
                    depth += 1
                    i += len(start)
                elif content.startswith(end, i):
                    depth -= 1
                    i += len(end)
                else:
                    if content[i] == "\n":
                        line += 1
                    i += 1
            if depth > 0:
                return _fail("Unterminated block comment", start_line)
            continue

        # Double-quoted (and other escape-aware) strings.
        if c in spec.string_delims:
            start_line = line
            i += 1
            closed = False
            while i < n:
                ch = content[i]
                if ch == "\\":
                    i += 2
                    continue
                if ch == "\n":
                    # An unescaped newline inside a "…" string is itself a
                    # syntax error in these languages, but be conservative:
                    # treat it as a terminated string to avoid runaway scans.
                    line += 1
                    i += 1
                    closed = True
                    break
                if ch == c:
                    closed = True
                    i += 1
                    break
                i += 1
            if not closed:
                return _fail("Unterminated string literal", start_line)
            prev_significant = c
            continue

        # Single-quoted char / string literal.
        if c == "'" and spec.single_quote_string:
            start_line = line
            i += 1
            closed = False
            while i < n:
                ch = content[i]
                if ch == "\\":
                    i += 2
                    continue
                if ch == "\n":
                    line += 1
                    i += 1
                    closed = True
                    break
                if ch == "'":
                    closed = True
                    i += 1
                    break
                i += 1
            if not closed:
                return _fail("Unterminated character literal", start_line)
            prev_significant = "'"
            continue

        # Backtick: JS/TS template literal (with ${…} interpolation that DOES
        # contain bracket-significant code) OR Go raw string (opaque).
        if c == "`" and spec.raw_backtick:
            start_line = line
            if spec.regex:
                # Template literal: scan to the matching backtick but recurse
                # into ${ … } so its braces still balance. We track template
                # nesting with a small local depth so a `}` that closes ${} is
                # not mistaken for a code brace.
                i += 1
                interp_depth = 0
                closed = False
                while i < n:
                    ch = content[i]
                    if ch == "\\":
                        i += 2
                        continue
                    if ch == "\n":
                        line += 1
                        i += 1
                        continue
                    if interp_depth == 0 and ch == "`":
                        closed = True
                        i += 1
                        break
                    if ch == "$" and i + 1 < n and content[i + 1] == "{":
                        interp_depth += 1
                        i += 2
                        continue
                    if interp_depth > 0 and ch == "}":
                        interp_depth -= 1
                        i += 1
                        continue
                    i += 1
                if not closed:
                    return _fail("Unterminated template literal", start_line)
            else:
                # Go raw string: opaque, no escapes, ends at next backtick.
                close_idx = content.find("`", i + 1)
                if close_idx == -1:
                    return _fail("Unterminated raw string literal", start_line)
                line += content.count("\n", i, close_idx)
                i = close_idx + 1
            prev_significant = "`"
            continue

        # Regex literal (JS/TS) in expression position.
        if (
            c == "/"
            and spec.regex
            and (prev_significant == "" or prev_significant in _REGEX_PRECEDERS)
        ):
            consumed = _try_consume_regex(content, i)
            if consumed is not None:
                i = consumed
                prev_significant = "/"
                continue
            # Not a regex (or ambiguous) — fall through and treat `/` as code.

        if c in _OPENERS:
            stack.append((c, line))
            prev_significant = c
            i += 1
            continue

        if c in _CLOSERS:
            if not stack:
                return _fail(f"Unmatched closing '{c}'", line)
            opener, _ = stack.pop()
            if _OPENERS[opener] != c:
                return _fail(f"Mismatched bracket: '{opener}' closed by '{c}'", line)
            prev_significant = c
            i += 1
            continue

        if not c.isspace():
            prev_significant = c
        i += 1

    if stack:
        opener, opener_line = stack[0]
        return _fail(
            f"Unbalanced bracket: '{opener}' opened at line {opener_line} "
            f"is never closed ({len(stack)} unclosed)",
            opener_line,
        )
    return SyntaxCheckResult(valid=True, errors=[], language=language)


def _try_consume_regex(content: str, start: int) -> int | None:
    """If ``content[start] == '/'`` begins a single-line regex literal, return
    the index just past the closing ``/`` (and any flags). Return ``None`` when
    it is not safely a regex (newline before close, looks like a comment, or
    unterminated) so the caller treats ``/`` as ordinary code — conservative:
    never claim a regex we are unsure about.
    """
    n = len(content)
    i = start + 1
    in_class = False  # inside a [...] character class, where / is literal
    while i < n:
        ch = content[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "\n":
            return None  # regex literals don't span lines — bail, treat as code
        if ch == "[":
            in_class = True
        elif ch == "]":
            in_class = False
        elif ch == "/" and not in_class:
            i += 1
            # consume trailing flags (a-z)
            while i < n and content[i].isalpha():
                i += 1
            return i
        i += 1
    return None


_LANG_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".jsx": "javascript",
    ".tsx": "typescript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".java": "java",
    ".kt": "kotlin",
    ".go": "go",
    ".rs": "rust",
}


def _ext_to_language(ext: str) -> str:
    return _LANG_MAP.get(ext, "unknown")


_REAL_CHECKER_EXTS = frozenset({".py", ".json", ".yaml", ".yml"}) | frozenset(
    _BALANCE_SPECS
)


def has_real_checker(file_path: str) -> bool:
    """True when ``check_syntax`` actually validates this extension (vs. the
    degrade-to-``valid=True`` path). Used to gate the Judge's O-J1
    high-confidence skip: a file whose language is NOT genuinely checkable must
    not be skipped on a vacuous "syntax OK", it must get full LLM review.
    """
    return Path(file_path).suffix.lower() in _REAL_CHECKER_EXTS


def balance_only_language_suffixes() -> frozenset[str]:
    """The compiled-language extensions whose only always-on gate is the
    balance check (no real type/parse check) — they depend on an
    operator-configured build/compile gate for semantic correctness. Single
    source of truth (mirrors ``_BALANCE_SPECS``) for the P3/P4 "no compile gate"
    advisory, so the two never drift from the actual checker coverage.
    """
    return frozenset(_BALANCE_SPECS)
