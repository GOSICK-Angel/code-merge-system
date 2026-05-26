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
    align_chunks,
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


def test_align_chunks_equal_counts_zip() -> None:
    a = ["a1\n", "a2\n"]
    b = ["b1\n", "b2\n"]
    assert align_chunks(a, b) == [("a1\n", "b1\n"), ("a2\n", "b2\n")]


def test_align_chunks_empty_side() -> None:
    assert align_chunks([], ["b\n"]) == []
    assert align_chunks(["a\n"], []) == []


def test_align_chunks_more_upstream_covers_every_b_once() -> None:
    # 2 fork chunks, 5 upstream chunks. Every upstream chunk must appear in
    # exactly one pair's target (no silent drop, no duplication).
    a = ["A1\n" * 10, "A2\n" * 10]
    b = [f"B{i}\n" for i in range(5)]
    pairs = align_chunks(a, b)

    assert [p[0] for p in pairs] == a  # one pair per fork chunk, in order
    joined_targets = "".join(p[1] for p in pairs)
    for i in range(5):
        assert joined_targets.count(f"B{i}\n") == 1  # each b exactly once


def test_align_chunks_more_fork_leaves_some_targets_empty() -> None:
    # 5 fork chunks, 2 upstream chunks. Every upstream chunk lands once; the
    # extra fork chunks get an empty target (fork-only regions).
    a = [f"A{i}\n" for i in range(5)]
    b = ["B0\n" * 10, "B1\n" * 10]
    pairs = align_chunks(a, b)

    assert [p[0] for p in pairs] == a
    joined_targets = "".join(p[1] for p in pairs)
    assert joined_targets.count("B0\n") == 10
    assert joined_targets.count("B1\n") == 10
    assert sum(1 for p in pairs if p[1] == "") >= 3  # extra fork chunks empty


def test_align_chunks_groups_are_contiguous() -> None:
    # Monotonic midpoints => each fork chunk receives a contiguous run of
    # upstream chunks, so the joined target is a real upstream slice.
    a = ["A1\n" * 30, "A2\n" * 30]
    b = [f"B{i}\n" for i in range(6)]
    pairs = align_chunks(a, b)

    full = "".join(b)
    reassembled = "".join(p[1] for p in pairs)
    assert reassembled == full  # order + completeness preserved


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
