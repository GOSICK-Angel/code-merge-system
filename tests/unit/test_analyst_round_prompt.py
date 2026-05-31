from src.llm.prompts.analyst_prompts import (
    _ROUND_DIFF_MAX_CHARS_PER_SIDE,
    build_commit_round_prompt,
)


def _commits() -> list[dict]:
    return [{"sha": "abc12345", "message": "feat: enhance oauth", "files": ["auth.go"]}]


def test_changed_region_beyond_head_appears_as_diff():
    # The changed line sits far past the first 1000 chars; the old truncated
    # head-only prompt would never have shown it. The diff-centric prompt must.
    head = "package auth\n" + ("// filler comment line\n" * 200)
    base = head + "func validate() bool { return true }\n"
    fork = head + "func validate() bool { return checkFork() }\n"
    upstream = head + "func validate() bool { return checkUpstream() }\n"

    prompt = build_commit_round_prompt(
        _commits(),
        {"auth.go": (base, fork, upstream)},
        {"auth.go": "go"},
        project_context="ctx",
    )

    assert "checkFork()" in prompt
    assert "checkUpstream()" in prompt
    assert "Fork changes (merge-base → fork)" in prompt
    assert "Upstream changes (merge-base → upstream)" in prompt
    # Filler that is identical on all three sides must not be echoed wholesale —
    # only the changed hunk plus a few context lines should appear.
    assert prompt.count("// filler comment line") < 20


def test_unchanged_side_emits_explicit_no_changes_note():
    base = "package auth\nfunc f() {}\n"
    fork = base  # fork did not touch this file
    upstream = "package auth\nfunc f() { log() }\n"

    prompt = build_commit_round_prompt(
        _commits(),
        {"auth.go": (base, fork, upstream)},
        {"auth.go": "go"},
    )

    assert "fork made no changes vs merge-base" in prompt
    assert "log()" in prompt


def test_per_side_diff_is_char_bounded():
    base = "".join(f"line {i}\n" for i in range(5000))
    fork = "".join(f"FORK {i}\n" for i in range(5000))
    upstream = base + "tail\n"

    prompt = build_commit_round_prompt(
        _commits(),
        {"auth.go": (base, fork, upstream)},
        {"auth.go": "go"},
    )

    assert "more diff lines)" in prompt
    # Each side stays within budget (+ small fence/marker overhead).
    assert len(prompt) < _ROUND_DIFF_MAX_CHARS_PER_SIDE * 2 + 2000


def test_added_file_with_no_base_shows_both_versions():
    fork = "package auth\nfunc fork() {}\n"
    upstream = "package auth\nfunc upstream() {}\n"

    prompt = build_commit_round_prompt(
        _commits(),
        {"auth.go": (None, fork, upstream)},
        {"auth.go": "go"},
    )

    assert "func fork()" in prompt
    assert "func upstream()" in prompt
