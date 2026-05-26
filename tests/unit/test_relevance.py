"""Tests for relevance scoring and budget-aware render level assignment."""

from __future__ import annotations

from src.llm.chunker import ChunkKind, CodeChunk
from src.llm.relevance import (
    FULL_THRESHOLD,
    SIGNATURE_THRESHOLD,
    RenderLevel,
    RelevanceScorer,
    ScoringContext,
)


def _make_chunk(
    name: str = "test",
    kind: ChunkKind = ChunkKind.FUNCTION,
    start_line: int = 1,
    end_line: int = 10,
    content: str = "def test(): pass",
    signature: str = "def test():",
) -> CodeChunk:
    return CodeChunk(
        name=name,
        kind=kind,
        start_line=start_line,
        end_line=end_line,
        content=content,
        signature=signature,
    )


class TestBaseScoring:
    def test_base_score_function_vs_comment(self) -> None:
        ctx = ScoringContext(diff_ranges=[])
        scorer = RelevanceScorer(ctx)

        func_chunk = _make_chunk(kind=ChunkKind.FUNCTION)
        comment_chunk = _make_chunk(kind=ChunkKind.COMMENT, name="comment")

        assert scorer.score_chunk(func_chunk) > scorer.score_chunk(comment_chunk)

    def test_class_higher_base_than_statement(self) -> None:
        ctx = ScoringContext(diff_ranges=[])
        scorer = RelevanceScorer(ctx)

        class_chunk = _make_chunk(kind=ChunkKind.CLASS, name="MyClass")
        stmt_chunk = _make_chunk(kind=ChunkKind.STATEMENT, name="x = 1")

        assert scorer.score_chunk(class_chunk) > scorer.score_chunk(stmt_chunk)


class TestDiffOverlap:
    def test_diff_overlap_full(self) -> None:
        ctx = ScoringContext(diff_ranges=[(5, 15)])
        scorer = RelevanceScorer(ctx)
        chunk = _make_chunk(start_line=1, end_line=10)
        score = scorer.score_chunk(chunk)
        assert score >= FULL_THRESHOLD

    def test_diff_adjacent_signature(self) -> None:
        ctx = ScoringContext(diff_ranges=[(20, 30)])
        scorer = RelevanceScorer(ctx)
        chunk = _make_chunk(start_line=10, end_line=15)
        score = scorer.score_chunk(chunk)
        assert score >= SIGNATURE_THRESHOLD

    def test_no_overlap_low_score(self) -> None:
        ctx = ScoringContext(diff_ranges=[(100, 200)])
        scorer = RelevanceScorer(ctx)
        chunk = _make_chunk(
            kind=ChunkKind.STATEMENT,
            name="far_away",
            start_line=1,
            end_line=5,
            content="x = 1",
            signature="x = 1",
        )
        score = scorer.score_chunk(chunk)
        assert score < FULL_THRESHOLD


class TestConflictBoost:
    def test_conflict_boost(self) -> None:
        ctx = ScoringContext(
            diff_ranges=[],
            conflict_ranges=[(1, 10)],
        )
        scorer = RelevanceScorer(ctx)
        chunk = _make_chunk(start_line=3, end_line=8)
        score = scorer.score_chunk(chunk)
        assert score >= FULL_THRESHOLD


class TestSecurityPatternBoost:
    def test_security_pattern_boost(self) -> None:
        ctx = ScoringContext(
            diff_ranges=[],
            security_patterns=["password"],
        )
        scorer = RelevanceScorer(ctx)
        chunk = _make_chunk(content="def check_password(pwd): pass")
        score = scorer.score_chunk(chunk)
        assert score > 0.3

    def test_no_security_match(self) -> None:
        ctx = ScoringContext(
            diff_ranges=[],
            security_patterns=["password"],
        )
        scorer = RelevanceScorer(ctx)
        chunk = _make_chunk(content="def hello(): pass")
        base_score = scorer.score_chunk(chunk)
        assert base_score < 0.3


class TestReferenceBoost:
    def test_reference_boost(self) -> None:
        ctx = ScoringContext(
            diff_ranges=[],
            referenced_names=frozenset(["helper"]),
        )
        scorer = RelevanceScorer(ctx)
        chunk = _make_chunk(name="helper")
        score = scorer.score_chunk(chunk)
        assert score >= 0.3


class TestEntryPointBoost:
    def test_entry_point_boost(self) -> None:
        ctx = ScoringContext(diff_ranges=[])
        scorer = RelevanceScorer(ctx)
        chunk = _make_chunk(name="main")
        score = scorer.score_chunk(chunk)
        assert score >= 0.3

    def test_init_entry_point(self) -> None:
        ctx = ScoringContext(diff_ranges=[])
        scorer = RelevanceScorer(ctx)
        chunk = _make_chunk(name="__init__")
        score = scorer.score_chunk(chunk)
        assert score >= 0.3


class TestScoreAndAssign:
    def test_empty_chunks_returns_empty(self) -> None:
        ctx = ScoringContext(diff_ranges=[(1, 10)])
        scorer = RelevanceScorer(ctx)
        result = scorer.score_and_assign([], budget_tokens=10_000)
        assert result == {}

    def test_all_chunks_fit(self) -> None:
        ctx = ScoringContext(diff_ranges=[(1, 10)])
        scorer = RelevanceScorer(ctx)
        chunks = [
            _make_chunk(name="a", start_line=1, end_line=5, content="short"),
            _make_chunk(name="b", start_line=6, end_line=10, content="also short"),
        ]
        levels = scorer.score_and_assign(chunks, budget_tokens=100_000)
        assert all(v == RenderLevel.FULL for v in levels.values())

    def test_same_named_chunks_do_not_collide(self) -> None:
        # Two chunks share a name (overloaded def); only the second overlaps
        # the diff. Keying by name would collapse them onto one level and
        # mis-render the irrelevant one. Keying by (start_line, end_line)
        # keeps an independent decision per chunk.
        ctx = ScoringContext(diff_ranges=[(40, 45)])
        scorer = RelevanceScorer(ctx)
        chunks = [
            _make_chunk(
                name="handle",
                start_line=1,
                end_line=5,
                content="def handle(): legacy()",
            ),
            _make_chunk(
                name="handle",
                start_line=40,
                end_line=45,
                content="def handle(): current()",
            ),
        ]
        levels = scorer.score_and_assign(chunks, budget_tokens=100_000)
        # One key per chunk — no collapse.
        assert len(levels) == 2
        assert (1, 5) in levels and (40, 45) in levels
        # The diff-overlapping chunk is FULL; the unrelated one is not promoted.
        assert levels[(40, 45)] == RenderLevel.FULL
        assert levels[(1, 5)] != RenderLevel.FULL

    def test_budget_demotion_full_to_signature(self) -> None:
        ctx = ScoringContext(diff_ranges=[(1, 5)])
        scorer = RelevanceScorer(ctx)
        chunks = [
            _make_chunk(
                name="critical",
                start_line=1,
                end_line=5,
                content="x" * 5000,
                signature="def critical():",
            ),
            _make_chunk(
                name="less_important",
                start_line=50,
                end_line=60,
                content="y" * 5000,
                signature="def less_important():",
            ),
        ]
        levels = scorer.score_and_assign(chunks, budget_tokens=500)
        demoted = [k for k, v in levels.items() if v != RenderLevel.FULL]
        assert len(demoted) >= 1

    def test_budget_demotion_signature_to_drop(self) -> None:
        ctx = ScoringContext(diff_ranges=[(1, 5)])
        scorer = RelevanceScorer(ctx)
        chunks = [
            _make_chunk(
                name=f"chunk_{i}",
                start_line=i * 10,
                end_line=i * 10 + 9,
                content="x" * 3000,
                signature=f"def chunk_{i}():" + "x" * 500,
            )
            for i in range(20)
        ]
        levels = scorer.score_and_assign(chunks, budget_tokens=50)
        drop_count = sum(1 for v in levels.values() if v == RenderLevel.DROP)
        assert drop_count > 0

    def test_cross_reference_boost(self) -> None:
        ctx = ScoringContext(diff_ranges=[(1, 10)])
        scorer = RelevanceScorer(ctx)
        chunks = [
            _make_chunk(
                name="caller",
                start_line=1,
                end_line=5,
                content="def caller(): helper()",
            ),
            _make_chunk(
                name="helper",
                kind=ChunkKind.FUNCTION,
                start_line=50,
                end_line=55,
                content="def helper(): pass",
                signature="def helper():",
            ),
        ]
        levels = scorer.score_and_assign(chunks, budget_tokens=100_000)
        assert levels[(50, 55)] in (RenderLevel.FULL, RenderLevel.SIGNATURE)

    def test_name_boost_requires_whole_identifier(self) -> None:
        # The FULL chunk references get_user (whole identifier) and contains
        # "width" (which embeds the substring "id"). A chunk named "id" must
        # not be boosted off that substring; "get_user" must be.
        ctx = ScoringContext(diff_ranges=[(1, 10)])
        scorer = RelevanceScorer(ctx)
        chunks = [
            _make_chunk(
                name="render",
                kind=ChunkKind.FUNCTION,
                start_line=1,
                end_line=10,
                content="def render(): return self.width + get_user()",
            ),
            _make_chunk(
                name="get_user",
                kind=ChunkKind.STATEMENT,
                start_line=50,
                end_line=52,
                content="unrelated",
                signature="get_user",
            ),
            _make_chunk(
                name="id",
                kind=ChunkKind.STATEMENT,
                start_line=60,
                end_line=62,
                content="unrelated",
                signature="id",
            ),
        ]
        levels = scorer.score_and_assign(chunks, budget_tokens=100_000)
        assert levels[(1, 10)] == RenderLevel.FULL
        assert levels[(50, 52)] == RenderLevel.SIGNATURE  # boosted by get_user
        assert levels[(60, 62)] == RenderLevel.DROP  # "id" only as substring


def test_identifier_tokens_whole_word_only() -> None:
    from src.llm.relevance import _identifier_tokens

    tokens = _identifier_tokens(["self.width + get_user()"])
    assert "width" in tokens
    assert "get_user" in tokens
    assert "id" not in tokens
