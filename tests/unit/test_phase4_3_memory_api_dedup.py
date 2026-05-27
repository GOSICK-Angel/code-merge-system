"""Phase 4.3 — 内存按需加载 API 去重守护测试.

enhanced-context-memory 提案的 P1 分层加载已落地（get_memory_context →
LayeredMemoryLoader → get_relevant_context），并选用 get_relevant_context /
harmful_entry_ids 把以下符号取代：MemoryStore/SQLiteMemoryStore 的
query_by_{path,tags,type} 与 MemoryHitTracker.entry_outcome()。生产端零调用，
按 Part 3「被取代」原则删除。本测试守护"删除后不复生"且活路径仍在。
"""

from __future__ import annotations

from src.memory.hit_tracker import MemoryHitTracker
from src.memory.sqlite_store import SQLiteMemoryStore
from src.memory.store import MemoryStore

_REMOVED_STORE_METHODS = ("query_by_path", "query_by_tags", "query_by_type")


class TestSupersededQueryMethodsRemoved:
    def test_memory_store_has_no_query_by_methods(self) -> None:
        for name in _REMOVED_STORE_METHODS:
            assert not hasattr(MemoryStore, name), name

    def test_sqlite_store_has_no_query_by_methods(self) -> None:
        for name in _REMOVED_STORE_METHODS:
            assert not hasattr(SQLiteMemoryStore, name), name

    def test_hit_tracker_has_no_entry_outcome(self) -> None:
        assert not hasattr(MemoryHitTracker, "entry_outcome")


class TestLiveRetrievalSurfaceIntact:
    """去重不得殃及活的检索/反馈闭环。"""

    def test_get_relevant_context_is_the_live_retrieval(self) -> None:
        assert hasattr(MemoryStore, "get_relevant_context")
        assert hasattr(SQLiteMemoryStore, "get_relevant_context")

    def test_outcome_feedback_loop_intact(self) -> None:
        # record_outcome（judge_review 写入）+ harmful_entry_ids（L2 读取跳过）
        # 是活的反馈闭环，被删的只有 entry_outcome() 公开 getter。
        assert hasattr(MemoryHitTracker, "record_outcome")
        assert hasattr(MemoryHitTracker, "harmful_entry_ids")
