"""H3 hotfix tests: ``submit_decision`` / ``submit_conflict_decisions_batch``
must persist ``reviewer_notes`` and ``custom_content`` onto the
``HumanDecisionRequest`` so downstream consumers (``executor_agent``
reads ``request.custom_content`` for MANUAL_PATCH apply,
``request.reviewer_notes`` feeds ``FileDecisionRecord.rationale``)
observe the user's input.

Also verifies M10: ``submit_decision`` accepts both ``filePath``
(legacy camelCase) and ``file_path`` (snake_case) so the wire format
can converge on snake_case over time without breaking older clients.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from src.models.config import MergeConfig
from src.models.conflict import (
    ChangeIntent,
    ConflictPoint,
    ConflictType,
)
from src.models.decision import MergeDecision
from src.models.human import DecisionOption, HumanDecisionRequest
from src.models.state import MergeState, SystemStatus
from src.web.ws_bridge import MergeWSBridge


def _make_request(file_path: str) -> HumanDecisionRequest:
    return HumanDecisionRequest(
        file_path=file_path,
        priority=5,
        conflict_points=[
            ConflictPoint(
                file_path=file_path,
                hunk_id="h-1",
                conflict_type=ConflictType.LOGIC_CONTRADICTION,
                upstream_intent=ChangeIntent(
                    description="u",
                    intent_type="feature",
                    confidence=0.8,
                ),
                fork_intent=ChangeIntent(
                    description="f",
                    intent_type="feature",
                    confidence=0.8,
                ),
                can_coexist=False,
                suggested_decision=MergeDecision.TAKE_CURRENT,
                confidence=0.8,
                rationale="r",
            )
        ],
        context_summary="ctx",
        upstream_change_summary="u",
        fork_change_summary="f",
        analyst_recommendation=MergeDecision.TAKE_CURRENT,
        analyst_confidence=0.8,
        analyst_rationale="rationale",
        options=[
            DecisionOption(
                option_key="opt",
                decision=MergeDecision.MANUAL_PATCH,
                description="manual",
            )
        ],
        created_at=datetime.now(),
    )


def _make_bridge() -> MergeWSBridge:
    cfg = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/x")
    state = MergeState(config=cfg, status=SystemStatus.AWAITING_HUMAN)
    state.human_decision_requests["a.py"] = _make_request("a.py")
    state.human_decision_requests["b.py"] = _make_request("b.py")
    return MergeWSBridge(state)


class _NoopWS:
    """``submit_decision`` calls ``broadcast_state_patch`` after applying;
    the patch path requires a writable WS but doesn't need to roundtrip
    for these tests. The async ``broadcast_state_patch`` writes to an
    empty ``_clients`` set so we just need a placeholder."""

    sent: list[str] = []

    async def send(self, data: str) -> None:  # pragma: no cover - unused
        self.sent.append(data)


class TestSubmitDecisionPersistsReviewerFields:
    @pytest.mark.asyncio
    async def test_persists_reviewer_notes_and_custom_content(self) -> None:
        bridge = _make_bridge()
        await bridge._handle_command(  # type: ignore[arg-type]
            _NoopWS(),
            {
                "type": "submit_decision",
                "payload": {
                    "filePath": "a.py",
                    "decision": "manual_patch",
                    "reviewer_notes": "verified the patch locally",
                    "custom_content": "--- a/x\n+++ b/x\n@@ ...",
                },
            },
        )
        req = bridge._state.human_decision_requests["a.py"]
        assert req.human_decision == MergeDecision.MANUAL_PATCH
        assert req.reviewer_notes == "verified the patch locally"
        assert req.custom_content == "--- a/x\n+++ b/x\n@@ ..."

    @pytest.mark.asyncio
    async def test_backward_compatible_without_optional_fields(self) -> None:
        """A client that only sends ``{filePath, decision}`` must not
        clobber existing ``reviewer_notes`` / ``custom_content`` (None
        in this fixture, but the rule applies equally to pre-populated
        values)."""
        bridge = _make_bridge()
        await bridge._handle_command(  # type: ignore[arg-type]
            _NoopWS(),
            {
                "type": "submit_decision",
                "payload": {
                    "filePath": "a.py",
                    "decision": "take_current",
                },
            },
        )
        req = bridge._state.human_decision_requests["a.py"]
        assert req.human_decision == MergeDecision.TAKE_CURRENT
        assert req.reviewer_notes is None
        assert req.custom_content is None

    @pytest.mark.asyncio
    async def test_accepts_snake_case_file_path(self) -> None:
        """M10: snake_case ``file_path`` must work alongside the
        legacy camelCase ``filePath`` so the wire schema can converge
        on Pydantic-aligned naming."""
        bridge = _make_bridge()
        await bridge._handle_command(  # type: ignore[arg-type]
            _NoopWS(),
            {
                "type": "submit_decision",
                "payload": {
                    "file_path": "a.py",  # snake_case only
                    "decision": "skip",
                },
            },
        )
        req = bridge._state.human_decision_requests["a.py"]
        assert req.human_decision == MergeDecision.SKIP

    @pytest.mark.asyncio
    async def test_invalid_decision_does_not_corrupt_state(self) -> None:
        bridge = _make_bridge()
        await bridge._handle_command(  # type: ignore[arg-type]
            _NoopWS(),
            {
                "type": "submit_decision",
                "payload": {
                    "filePath": "a.py",
                    "decision": "not-a-real-enum",
                    "reviewer_notes": "...",
                    "custom_content": "...",
                },
            },
        )
        req = bridge._state.human_decision_requests["a.py"]
        assert req.human_decision is None
        assert req.reviewer_notes is None
        assert req.custom_content is None


class TestSubmitBatchPersistsReviewerFields:
    @pytest.mark.asyncio
    async def test_batch_persists_per_item_fields(self) -> None:
        bridge = _make_bridge()
        await bridge._handle_command(  # type: ignore[arg-type]
            _NoopWS(),
            {
                "type": "submit_conflict_decisions_batch",
                "payload": {
                    "items": [
                        {
                            "file_path": "a.py",
                            "decision": "manual_patch",
                            "reviewer_notes": "patch reviewed",
                            "custom_content": "diff for a",
                        },
                        {
                            "file_path": "b.py",
                            "decision": "take_target",
                            # reviewer_notes / custom_content omitted —
                            # legacy clients still work.
                        },
                    ]
                },
            },
        )
        a = bridge._state.human_decision_requests["a.py"]
        b = bridge._state.human_decision_requests["b.py"]
        assert a.human_decision == MergeDecision.MANUAL_PATCH
        assert a.reviewer_notes == "patch reviewed"
        assert a.custom_content == "diff for a"
        assert b.human_decision == MergeDecision.TAKE_TARGET
        assert b.reviewer_notes is None
        assert b.custom_content is None

    @pytest.mark.asyncio
    async def test_batch_signals_all_decided(self) -> None:
        bridge = _make_bridge()
        await bridge._handle_command(  # type: ignore[arg-type]
            _NoopWS(),
            {
                "type": "submit_conflict_decisions_batch",
                "payload": {
                    "items": [
                        {
                            "file_path": "a.py",
                            "decision": "take_current",
                        },
                        {
                            "file_path": "b.py",
                            "decision": "take_current",
                        },
                    ]
                },
            },
        )
        # Both files decided → orchestrator event should be set
        assert bridge._human_decisions_received.is_set()


class TestPayloadFilePathFallback:
    """Coverage for the ``filePath`` / ``file_path`` fallback chain in
    both ``submit_decision`` and ``submit_conflict_decisions_batch``
    items. Both keys are accepted; ``file_path`` wins when present (the
    documented future-default)."""

    @pytest.mark.asyncio
    async def test_batch_item_filePath_camelCase_also_works(self) -> None:
        bridge = _make_bridge()
        await bridge._handle_command(  # type: ignore[arg-type]
            _NoopWS(),
            {
                "type": "submit_conflict_decisions_batch",
                "payload": {
                    "items": [
                        {
                            "filePath": "a.py",
                            "decision": "skip",
                        }
                    ]
                },
            },
        )
        assert (
            bridge._state.human_decision_requests["a.py"].human_decision
            == MergeDecision.SKIP
        )


def _broadcast_state(_: Any) -> None:  # pragma: no cover - unused helper
    return None
