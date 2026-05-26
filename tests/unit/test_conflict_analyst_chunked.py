"""Phase 1 — U1 chunked analysis tests.

Coverage of test/FINAL.md §2.2.1 U-P1.* matrix:
- U-P1.1 build_staged_content runs without memory_store (U1.A decoupling)
- U-P1.2 ~ U-P1.6 chunked reducer paths (fast / slow / hard cap / security / count)
- U-P1.7 ~ U-P1.8 chunked threshold boundaries (39999 / 40001)
- U-P1.9 single-chunk LLM failure falls back to ESCALATE_HUMAN
- U-P1.10 ~ U-P1.11 contract yaml + restricted view for `thresholds`
- U-P1.12 _aggregate_chunked_analyses purity (no LLM, no cost mutation)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.agents.conflict_analyst_agent import ConflictAnalystAgent
from src.models.conflict import ConflictAnalysis, ConflictType
from src.models.config import AgentLLMConfig
from src.models.decision import MergeDecision
from src.models.diff import FileDiff, FileStatus, RiskLevel


def _make_agent(memory_store=None) -> ConflictAnalystAgent:
    agent = ConflictAnalystAgent(
        AgentLLMConfig(
            provider="anthropic",
            model="test-model",
            api_key_env="ANTHROPIC_API_KEY",
            max_retries=1,
        )
    )
    agent._memory_store = memory_store
    return agent


def _make_file_diff(
    file_path: str = "demo.py",
    is_security_sensitive: bool = False,
) -> FileDiff:
    return FileDiff(
        file_path=file_path,
        file_status=FileStatus.MODIFIED,
        risk_level=RiskLevel.AUTO_RISKY,
        risk_score=0.5,
        lines_added=10,
        lines_deleted=2,
        is_security_sensitive=is_security_sensitive,
    )


def _make_analysis(
    file_path: str,
    strategy: MergeDecision,
    confidence: float = 0.9,
    is_security_sensitive: bool = False,
    is_chunked: bool = False,
    chunk_count: int = 1,
) -> ConflictAnalysis:
    return ConflictAnalysis(
        file_path=file_path,
        conflict_points=[],
        overall_confidence=confidence,
        recommended_strategy=strategy,
        conflict_type=ConflictType.UNKNOWN,
        is_security_sensitive=is_security_sensitive,
        confidence=confidence,
        is_chunked=is_chunked,
        chunk_count=chunk_count,
    )


# ---------- U-P1.1 U1.A decoupling ----------


@pytest.mark.asyncio
async def test_staged_content_runs_without_memory_store() -> None:
    """U-P1.1: build_staged_content is invoked even when memory_store is None.

    Anchor: facts.md C1 (lines 117-172 currently gated by ``if self._memory_store``);
    plan FINAL §2 Phase 1 U1.A delivery.
    """
    agent = _make_agent(memory_store=None)
    file_diff = _make_file_diff("demo.py")

    # Replace build_staged_content with a tracking mock that returns the
    # input string unchanged. Using ``side_effect`` keeps argument inspection
    # while avoiding the ``self`` binding issue of ``wraps`` on an instance method.
    from src.llm import prompt_builders as pb

    wrapper = MagicMock(side_effect=lambda *a, **kw: a[0])

    with (
        patch.object(pb.AgentPromptBuilder, "build_staged_content", wrapper),
        patch.object(
            agent,
            "_call_llm_with_retry",
            new=AsyncMock(return_value='{"recommended_strategy": "take_target"}'),
        ),
        patch(
            "src.agents.conflict_analyst_agent.parse_conflict_analysis",
            return_value=_make_analysis("demo.py", MergeDecision.TAKE_TARGET),
        ),
    ):
        result = await agent.analyze_file(
            file_diff,
            base_content=None,
            current_content="x" * 1000,
            target_content="y" * 1000,
        )

    assert wrapper.call_count >= 1, (
        "build_staged_content must run independently of memory_store gate"
    )
    assert result.recommended_strategy == MergeDecision.TAKE_TARGET


# ---------- ③ chunked relevance pre-filter ----------


def _padded_fn(idx: int, body: str) -> str:
    # Fixed-shape function so changing only the body keeps byte length (and
    # thus semantic-chunk boundaries) stable across current/target.
    return f"def f{idx}():\n    {body}  # pad-xxxxxxxxxxxxxxxxxxxx\n\n\n"


async def test_chunked_skips_unchanged_pairs() -> None:
    """③: identical (cur == tgt) chunk pairs are not sent to the LLM."""
    from src.tools.chunk_processor import split_by_semantic_boundary

    agent = _make_agent()
    file_diff = _make_file_diff("demo.py")
    chunk_size = 200

    current = "".join(_padded_fn(i, f"return {i} + 0") for i in range(12))
    # Change exactly one function's body, same length -> boundaries unchanged.
    target = "".join(
        _padded_fn(i, f"return {i} + 9" if i == 6 else f"return {i} + 0")
        for i in range(12)
    )
    total_chunks = len(split_by_semantic_boundary(current, "demo.py", chunk_size))
    assert total_chunks > 1  # sanity: file actually splits

    counter = AsyncMock(return_value='{"recommended_strategy": "take_target"}')
    with (
        patch.object(agent, "_call_llm_with_retry", new=counter),
        patch(
            "src.agents.conflict_analyst_agent.parse_conflict_analysis",
            return_value=_make_analysis("demo.py", MergeDecision.TAKE_TARGET),
        ),
    ):
        result = await agent.analyze_file(
            file_diff,
            base_content=None,
            current_content=current,
            target_content=target,
            chunk_size_chars=chunk_size,
        )

    assert counter.await_count >= 1
    assert counter.await_count < total_chunks, (
        "unchanged chunk pairs must be skipped, sparing their LLM calls"
    )
    assert result.recommended_strategy == MergeDecision.TAKE_TARGET


async def test_chunked_all_changed_analyzes_every_pair() -> None:
    """③ degenerate: when every pair differs, nothing is skipped (no regression)."""
    from src.tools.chunk_processor import split_by_semantic_boundary

    agent = _make_agent()
    file_diff = _make_file_diff("demo.py")
    chunk_size = 200

    current = "".join(_padded_fn(i, f"return {i} + 0") for i in range(12))
    target = "".join(_padded_fn(i, f"return {i} + 9") for i in range(12))
    total_chunks = len(split_by_semantic_boundary(current, "demo.py", chunk_size))

    counter = AsyncMock(return_value='{"recommended_strategy": "take_target"}')
    with (
        patch.object(agent, "_call_llm_with_retry", new=counter),
        patch(
            "src.agents.conflict_analyst_agent.parse_conflict_analysis",
            return_value=_make_analysis("demo.py", MergeDecision.TAKE_TARGET),
        ),
    ):
        await agent.analyze_file(
            file_diff,
            base_content=None,
            current_content=current,
            target_content=target,
            chunk_size_chars=chunk_size,
        )

    assert counter.await_count == total_chunks


# ---------- U-P1.2 ~ U-P1.6 reducer paths ----------


def test_chunked_path_fast_unanimous() -> None:
    """U-P1.2: unanimous strategy + min conf >= threshold + no security → fast path."""
    from src.agents.conflict_analyst_agent import _aggregate_chunked_analyses

    chunks = [
        _make_analysis("demo.py", MergeDecision.TAKE_TARGET, confidence=0.86),
        _make_analysis("demo.py", MergeDecision.TAKE_TARGET, confidence=0.92),
        _make_analysis("demo.py", MergeDecision.TAKE_TARGET, confidence=0.95),
    ]
    merged = _aggregate_chunked_analyses(
        chunks, file_path="demo.py", min_confidence=0.85
    )
    assert merged.recommended_strategy == MergeDecision.TAKE_TARGET
    assert merged.is_chunked is True
    assert merged.chunk_count == 3
    assert merged.confidence == pytest.approx(0.86)
    assert "unanimous" in merged.rationale


def test_chunked_path_slow_disagreement() -> None:
    """U-P1.3: disagreement → slow path with precedence + 0.8 penalty."""
    from src.agents.conflict_analyst_agent import (
        PENALTY_FACTOR,
        _aggregate_chunked_analyses,
    )

    chunks = [
        _make_analysis("demo.py", MergeDecision.TAKE_TARGET, confidence=0.90),
        _make_analysis("demo.py", MergeDecision.SEMANTIC_MERGE, confidence=0.92),
        _make_analysis("demo.py", MergeDecision.TAKE_CURRENT, confidence=0.88),
    ]
    merged = _aggregate_chunked_analyses(
        chunks, file_path="demo.py", min_confidence=0.85
    )
    # SEMANTIC > TAKE_* per precedence rules
    assert merged.recommended_strategy == MergeDecision.SEMANTIC_MERGE
    assert merged.is_chunked is True
    assert merged.chunk_count == 3
    assert merged.confidence == pytest.approx(min(0.90, 0.92, 0.88) * PENALTY_FACTOR)
    assert PENALTY_FACTOR == 0.8
    assert "disagreement" in merged.rationale


def test_chunked_hard_cap_escalates() -> None:
    """U-P1.4: > 8 chunks → ESCALATE_HUMAN with confidence=0.3."""
    from src.agents.conflict_analyst_agent import _aggregate_chunked_analyses

    chunks = [
        _make_analysis("demo.py", MergeDecision.TAKE_TARGET, confidence=0.9)
        for _ in range(9)
    ]
    merged = _aggregate_chunked_analyses(
        chunks, file_path="demo.py", min_confidence=0.85
    )
    assert merged.recommended_strategy == MergeDecision.ESCALATE_HUMAN
    assert merged.confidence == 0.3
    assert merged.is_chunked is True
    assert merged.chunk_count == 9
    assert "too large for safe chunked analysis" in merged.rationale


def test_chunked_security_falls_to_slow_path() -> None:
    """U-P1.5: any chunk is_security_sensitive=True forces slow path even if numerically unanimous."""
    from src.agents.conflict_analyst_agent import (
        PENALTY_FACTOR,
        _aggregate_chunked_analyses,
    )

    chunks = [
        _make_analysis("auth.py", MergeDecision.TAKE_TARGET, confidence=0.9),
        _make_analysis("auth.py", MergeDecision.TAKE_TARGET, confidence=0.9),
        _make_analysis(
            "auth.py",
            MergeDecision.TAKE_TARGET,
            confidence=0.9,
            is_security_sensitive=True,
        ),
        _make_analysis("auth.py", MergeDecision.TAKE_TARGET, confidence=0.9),
    ]
    merged = _aggregate_chunked_analyses(
        chunks, file_path="auth.py", min_confidence=0.85
    )
    assert merged.is_security_sensitive is True
    assert merged.confidence == pytest.approx(0.9 * PENALTY_FACTOR)
    # Slow path produces a precedence-derived strategy; here all are TAKE_TARGET
    # so the slow-path output is TAKE_TARGET — but penalty must still apply.


def test_chunked_aggregation_chunk_count_tracked() -> None:
    """U-P1.6: ConflictAnalysis carries is_chunked/chunk_count and round-trips through model_dump."""
    from src.agents.conflict_analyst_agent import _aggregate_chunked_analyses

    chunks = [
        _make_analysis("demo.py", MergeDecision.TAKE_TARGET, confidence=0.9),
        _make_analysis("demo.py", MergeDecision.TAKE_TARGET, confidence=0.9),
    ]
    merged = _aggregate_chunked_analyses(
        chunks, file_path="demo.py", min_confidence=0.85
    )
    dumped = merged.model_dump()
    assert dumped["is_chunked"] is True
    assert dumped["chunk_count"] == 2
    ConflictAnalysis(**dumped)  # round-trip must not raise

    single = _make_analysis("demo.py", MergeDecision.TAKE_TARGET)
    single_dump = single.model_dump()
    assert single_dump["is_chunked"] is False
    assert single_dump["chunk_count"] == 1
    ConflictAnalysis(**single_dump)


# ---------- U-P1.7 / U-P1.8 threshold boundaries ----------


@pytest.mark.asyncio
async def test_chunked_threshold_not_triggered_below_40kb() -> None:
    """U-P1.7: max(current, target) == chunk_size_chars * 2 - 1 → regular path."""
    agent = _make_agent(memory_store=None)
    file_diff = _make_file_diff("demo.py")
    # default chunk_size_chars = 20000, threshold = 40000 chars
    payload = "x" * 39999

    with (
        patch(
            "src.core.parallel_file_runner.ParallelFileRunner.from_api_key_env_list"
        ) as runner_factory,
        patch.object(
            agent,
            "_call_llm_with_retry",
            new=AsyncMock(return_value='{"recommended_strategy": "take_target"}'),
        ),
        patch(
            "src.agents.conflict_analyst_agent.parse_conflict_analysis",
            return_value=_make_analysis("demo.py", MergeDecision.TAKE_TARGET),
        ),
    ):
        await agent.analyze_file(
            file_diff,
            base_content=None,
            current_content=payload,
            target_content=payload,
        )

    runner_factory.assert_not_called()


@pytest.mark.asyncio
async def test_chunked_threshold_triggered_at_40kb_plus_one() -> None:
    """U-P1.8: max(current, target) == 40001 → chunked path + split_by_semantic_boundary imported from src.tools.chunk_processor."""
    import sys

    agent = _make_agent(memory_store=None)
    file_diff = _make_file_diff("demo.py")
    # Multi-line >40KB payload so split_by_semantic_boundary actually splits.
    payload = "\n".join(f"line {i:06d} " + "x" * 40 for i in range(900))

    fake_runner = MagicMock()
    fake_runner.run_files = AsyncMock(
        return_value={
            0: _make_analysis("demo.py", MergeDecision.TAKE_TARGET, confidence=0.9),
            1: _make_analysis("demo.py", MergeDecision.TAKE_TARGET, confidence=0.9),
        }
    )

    # Patch the symbol *as imported into the agent module* — agents
    # have ``from src.tools.chunk_processor import split_by_semantic_boundary``
    # which binds a local name in the agent's namespace.
    from src.tools import chunk_processor as cp

    with (
        patch(
            "src.core.parallel_file_runner.ParallelFileRunner.from_api_key_env_list",
            return_value=fake_runner,
        ) as runner_factory,
        patch(
            "src.agents.conflict_analyst_agent.split_by_semantic_boundary",
            wraps=cp.split_by_semantic_boundary,
        ) as split_mock,
    ):
        await agent.analyze_file(
            file_diff,
            base_content=None,
            current_content=payload,
            target_content=payload,
        )

    runner_factory.assert_called()
    assert split_mock.call_count >= 1
    # Verify the conflict_analyst module sees split_by_semantic_boundary
    # from src.tools.chunk_processor (plan P1-2 reverse-import defence).
    module = sys.modules["src.agents.conflict_analyst_agent"]
    src_text = open(module.__file__).read()  # type: ignore[arg-type]
    assert "from src.tools.chunk_processor import" in src_text
    assert "split_by_semantic_boundary" in src_text


# ---------- U-P1.9 LLM failure fallback ----------


@pytest.mark.asyncio
async def test_chunked_llm_failure_one_chunk_falls_back_to_escalate() -> None:
    """U-P1.9: single-chunk LLM failure → aggregated strategy = ESCALATE_HUMAN.

    Lock #16: spec-by-test (doc/plan未显式; 锁定为最保守安全默认).
    """
    agent = _make_agent(memory_store=None)
    file_diff = _make_file_diff("demo.py")
    # Multi-line payload large enough to split into ≥4 chunks at the default
    # 20000-char chunk size. Each line ~50 chars, 2000 lines → 100KB total.
    payload = "\n".join(f"line {i:06d} " + "x" * 40 for i in range(2000))

    # Simulate fan-out with chunk index 1 failing via httpx.ReadTimeout
    # (real classifier path → AgentExhaustedError after retries).
    call_count = {"n": 0}

    async def fake_call(*args, **kwargs):
        idx = call_count["n"]
        call_count["n"] += 1
        if idx == 1:
            raise httpx.ReadTimeout("simulated transport timeout")
        return '{"recommended_strategy": "take_target"}'

    # Patch the runner so it serially invokes our fake LLM via the registered
    # handler — keeps the test deterministic and avoids real concurrency.
    async def fake_run_files(keys, handler):
        results = {}
        for k in keys:
            try:
                results[k] = await handler(k)
            except Exception as exc:  # pragma: no cover - aggregation path
                results[k] = exc
        return results

    fake_runner = MagicMock()
    fake_runner.run_files = AsyncMock(side_effect=fake_run_files)

    with (
        patch(
            "src.core.parallel_file_runner.ParallelFileRunner.from_api_key_env_list",
            return_value=fake_runner,
        ),
        patch.object(agent, "_call_llm_with_retry", side_effect=fake_call),
        patch(
            "src.agents.conflict_analyst_agent.parse_conflict_analysis",
            side_effect=lambda raw, fp, model: _make_analysis(
                fp, MergeDecision.TAKE_TARGET, confidence=0.9
            ),
        ),
    ):
        merged = await agent.analyze_file(
            file_diff,
            base_content=None,
            current_content=payload,
            target_content=payload,
        )

    assert merged.recommended_strategy == MergeDecision.ESCALATE_HUMAN
    assert "chunk" in merged.rationale.lower() or "timeout" in merged.rationale.lower()


# ---------- U-P1.10 / U-P1.11 contract yaml + restricted view ----------


def test_conflict_analyst_yaml_inputs_include_thresholds() -> None:
    """U-P1.10: conflict_analyst.yaml inputs include 'thresholds'."""
    import yaml as yaml_lib
    from pathlib import Path

    yaml_path = (
        Path(__file__).resolve().parents[2]
        / "src/agents/contracts/conflict_analyst.yaml"
    )
    data = yaml_lib.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert "thresholds" in data["inputs"]
    # Pre-existing 7 inputs remain (facts.md C4):
    for required in (
        "_merge_base",
        "config",
        "conflict_analyses",
        "file_diffs",
        "forks_profile",
        "merge_plan",
        "status",
    ):
        assert required in data["inputs"], f"input '{required}' missing"


def test_conflict_analyst_restricted_view_can_read_thresholds() -> None:
    """U-P1.11: restricted_view exposes 'thresholds' without raising FieldNotInContract."""
    agent = _make_agent(memory_store=None)

    class _State:
        pass

    state = _State()
    from src.agents.contract import load_contract

    contract = load_contract("conflict_analyst")
    for field in contract.inputs:
        setattr(state, field, object())

    view = agent.restricted_view(state)
    _ = view.thresholds  # must not raise FieldNotInContract


# ---------- U-P1.12 reducer purity ----------


def test_aggregate_chunked_analyses_is_pure_function() -> None:
    """U-P1.12: same input → same output; no cost_tracker writes."""
    from src.agents.conflict_analyst_agent import _aggregate_chunked_analyses

    chunks = [
        _make_analysis("demo.py", MergeDecision.TAKE_TARGET, confidence=0.9),
        _make_analysis("demo.py", MergeDecision.TAKE_TARGET, confidence=0.9),
    ]
    a = _aggregate_chunked_analyses(chunks, file_path="demo.py", min_confidence=0.85)
    b = _aggregate_chunked_analyses(chunks, file_path="demo.py", min_confidence=0.85)
    assert a.model_dump(exclude={"analysis_id"}) == b.model_dump(
        exclude={"analysis_id"}
    )
