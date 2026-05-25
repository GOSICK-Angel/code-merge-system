"""Regression: chunked semantic merge must not glue chunk seams.

Root cause of the forgejo `models/user/user.go` corruption: Go chunks split
*before* a ``func`` line, so a chunk ends on the function's doc comment and the
next chunk starts with ``func ...``. ``parse_merge_result`` ``.strip()``s each
LLM-rewritten chunk, dropping its trailing newline, so the old naive
``"".join`` produced ``// ...reservedfunc IsUsableUsername(...)`` — commenting
out the declaration and breaking compilation. ``merge_chunks`` must re-insert
the seam newline.
"""

from __future__ import annotations

from src.tools.chunk_processor import (
    merge_chunks,
    split_by_semantic_boundary,
)


def test_merge_chunks_reinserts_seam_newline_when_chunk_lost_trailing_nl() -> None:
    # Simulates LLM round-trip: parse_merge_result strips each chunk, so the
    # first chunk arrives without its trailing newline.
    chunks = [
        "// IsUsableUsername returns an error when a username is reserved",
        "func IsUsableUsername(name string) error {\n\treturn nil\n}\n",
    ]
    merged = merge_chunks(chunks)
    assert "reservedfunc" not in merged
    assert (
        "// IsUsableUsername returns an error when a username is reserved\n"
        "func IsUsableUsername(name string) error {" in merged
    )


def test_merge_chunks_does_not_add_blank_lines_when_newline_present() -> None:
    chunks = ["package a\n\n", "func B() {}\n"]
    assert merge_chunks(chunks) == "package a\n\nfunc B() {}\n"


def test_merge_chunks_preserves_missing_final_newline() -> None:
    # A file whose last line has no trailing newline must stay that way.
    chunks = ["func A() {}\n", "func B() {}"]
    assert merge_chunks(chunks) == "func A() {}\nfunc B() {}"


def test_merge_chunks_empty() -> None:
    assert merge_chunks([]) == ""


def test_split_then_stripped_roundtrip_is_compilable_go() -> None:
    # Build a Go file large enough to force >=2 chunks at func boundaries,
    # then emulate the strip-per-chunk LLM round-trip and reassemble.
    body = "\treturn nil\n}\n"
    funcs = "".join(
        f"// Doc for Fn{i} explaining the reserved behaviour\n"
        f"func Fn{i}(name string) error {{\n{body}\n"
        for i in range(40)
    )
    content = "package user\n\n" + funcs
    chunk_size = len(content) // 4
    chunks = split_by_semantic_boundary(content, "models/user/user.go", chunk_size)
    assert len(chunks) >= 2
    # Emulate parse_merge_result: strip() drops each chunk's trailing newline.
    stripped = [c.strip() for c in chunks]
    merged = merge_chunks(stripped)
    # No doc comment may be glued onto its declaration.
    assert "behaviourfunc" not in merged
    for i in range(40):
        assert f"\nfunc Fn{i}(name string) error {{" in merged
