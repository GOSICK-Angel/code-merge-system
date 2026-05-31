"""Tests for the analyst-driven decision-proposal flow (Round-3 D).

Covers:
- ``parse_decision_proposals`` JSON parsing + graceful degrade
- ``ConflictAnalystAgent.propose_decision_options`` happy path and
  LLM-failure fallback
- ``PlanReviewPhase._collect_analyst_proposals`` opt-in wiring and
  per-file error isolation
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.llm.prompts.analyst_prompts import (
    build_decision_proposal_prompt,
    parse_decision_proposals,
)
from src.models.config import (
    AgentLLMConfig,
    MergeConfig,
    PlanReviewConfig,
)
from src.models.diff import FileChangeCategory, FileDiff, FileStatus, RiskLevel
from src.models.plan import MergePhase, MergePlan, PhaseFileBatch, RiskSummary
from src.models.plan_review import DecisionOption
from src.models.state import MergeState


class TestParseDecisionProposals:
    def test_parses_well_formed_json(self):
        raw = """{
          "proposals": [
            {
              "key": "merge-struct-fields",
              "label": "Merge struct fields from both sides",
              "description": "Add CreatedUnix/UserAgent and LoginSourceID into AuthorizationToken.",
              "preview": "struct { ID int64; CreatedUnix ...; LoginSourceID ...; UserAgent ... }"
            },
            {"key": "take-upstream", "label": "Take upstream version", "description": "Drop fork additions."}
          ]
        }"""
        out = parse_decision_proposals(raw)
        assert len(out) == 2
        assert out[0]["key"] == "merge-struct-fields"
        assert out[0]["label"].startswith("Merge struct fields")
        assert "CreatedUnix" in out[0]["preview"]
        assert out[1]["preview"] == ""

    def test_strips_code_fences(self):
        raw = (
            "```json\n"
            + '{"proposals":[{"key":"k","label":"L","description":"d"}]}'
            + "\n```"
        )
        out = parse_decision_proposals(raw)
        assert len(out) == 1
        assert out[0]["key"] == "k"

    def test_returns_empty_on_invalid_json(self):
        assert parse_decision_proposals("not json at all") == []
        assert parse_decision_proposals("") == []
        assert parse_decision_proposals(None) == []  # type: ignore[arg-type]

    def test_returns_empty_when_proposals_missing(self):
        assert parse_decision_proposals('{"other": []}') == []

    def test_drops_proposal_with_missing_key_or_label(self):
        raw = '{"proposals":[{"label":"no key"},{"key":"no-label"},{"key":"ok","label":"OK"}]}'
        out = parse_decision_proposals(raw)
        assert len(out) == 1
        assert out[0]["key"] == "ok"

    def test_truncates_overlong_label(self):
        long = "x" * 200
        raw = f'{{"proposals":[{{"key":"k","label":"{long}","description":""}}]}}'
        out = parse_decision_proposals(raw)
        assert len(out[0]["label"]) == 80


class TestProposalPromptShape:
    def test_prompt_includes_three_way_content(self):
        prompt = build_decision_proposal_prompt(
            "models/auth/auth_token.go",
            base_content="package auth\ntype T struct { ID int64 }",
            fork_content="package auth\ntype T struct { ID int64; CreatedUnix int }",
            upstream_content="package auth\ntype T struct { ID int64; LoginSourceID int }",
            language="go",
        )
        assert "models/auth/auth_token.go" in prompt
        assert "CreatedUnix" in prompt
        assert "LoginSourceID" in prompt
        assert "Base (common ancestor)" in prompt
        assert "Fork side" in prompt
        assert "Upstream side" in prompt
        assert "Respond with ONLY the JSON" in prompt

    def test_prompt_omits_empty_project_context(self):
        prompt = build_decision_proposal_prompt(
            "f.py", None, "fork", "upstream", project_context=""
        )
        assert "Project Context" not in prompt

    def test_prompt_includes_project_context_when_given(self):
        prompt = build_decision_proposal_prompt(
            "f.py", None, "fork", "upstream", project_context="Internal billing tool"
        )
        assert "Project Context" in prompt
        assert "Internal billing tool" in prompt


class TestConflictAnalystProposeOptions:
    @pytest.mark.asyncio
    async def test_returns_parsed_proposals_on_llm_success(self):
        from src.agents.conflict_analyst_agent import ConflictAnalystAgent

        with patch("src.llm.client.LLMClientFactory.create"):
            analyst = ConflictAnalystAgent(AgentLLMConfig())
        analyst._call_llm_with_retry = AsyncMock(
            return_value='{"proposals":[{"key":"k1","label":"Strategy A","description":"do A"}]}'
        )

        out = await analyst.propose_decision_options(
            "f.go",
            base_content="b",
            fork_content="f",
            upstream_content="u",
            language="go",
        )
        assert len(out) == 1
        assert out[0]["key"] == "k1"
        assert out[0]["label"] == "Strategy A"

    @pytest.mark.asyncio
    async def test_returns_empty_on_llm_failure(self):
        from src.agents.conflict_analyst_agent import ConflictAnalystAgent

        with patch("src.llm.client.LLMClientFactory.create"):
            analyst = ConflictAnalystAgent(AgentLLMConfig())
        analyst._call_llm_with_retry = AsyncMock(side_effect=RuntimeError("boom"))

        out = await analyst.propose_decision_options(
            "f.go", "b", "f", "u", language="go"
        )
        assert out == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_parse_failure(self):
        from src.agents.conflict_analyst_agent import ConflictAnalystAgent

        with patch("src.llm.client.LLMClientFactory.create"):
            analyst = ConflictAnalystAgent(AgentLLMConfig())
        analyst._call_llm_with_retry = AsyncMock(return_value="not json")

        out = await analyst.propose_decision_options(
            "f.go", "b", "f", "u", language="go"
        )
        assert out == []


def _make_plan_with_hr(file_path: str = "models/auth/auth_token.go") -> MergePlan:
    return MergePlan(
        created_at=datetime.now(),
        upstream_ref="up",
        fork_ref="fork",
        merge_base_commit="base",
        phases=[
            PhaseFileBatch(
                batch_id="b0",
                phase=MergePhase.HUMAN_REVIEW,
                file_paths=[file_path],
                risk_level=RiskLevel.HUMAN_REQUIRED,
            )
        ],
        risk_summary=RiskSummary(
            total_files=1,
            auto_safe_count=0,
            auto_risky_count=0,
            human_required_count=1,
            deleted_only_count=0,
            binary_count=0,
            excluded_count=0,
            estimated_auto_merge_rate=0.0,
        ),
        project_context_summary="",
    )


def _make_state(
    enable_analyst: bool, file_path: str = "models/auth/auth_token.go"
) -> MergeState:
    config = MergeConfig(
        upstream_ref="upstream/main",
        fork_ref="feature/fork",
        plan_review=PlanReviewConfig(
            analyst_decision_options_enabled=enable_analyst,
        ),
    )
    state = MergeState(config=config)
    state.merge_base_commit = "base123"
    state.merge_plan = _make_plan_with_hr(file_path)
    state.file_diffs = [
        FileDiff(
            file_path=file_path,
            file_status=FileStatus.MODIFIED,
            risk_level=RiskLevel.HUMAN_REQUIRED,
            risk_score=0.8,
            lines_added=5,
            lines_deleted=0,
            upstream_lines_added=4,
            upstream_lines_deleted=0,
            change_category=FileChangeCategory.C,
            language="go",
        )
    ]
    return state


class TestPlanReviewPhaseAnalystProposalsOptIn:
    @pytest.mark.asyncio
    async def test_flag_off_skips_analyst_call(self):
        from src.core.phases.plan_review import PlanReviewPhase

        state = _make_state(enable_analyst=False)
        analyst = MagicMock()
        analyst.propose_decision_options = AsyncMock()

        ctx = MagicMock()
        ctx.agents = {"conflict_analyst": analyst}

        phase = PlanReviewPhase()
        items = await phase._build_user_decision_items(state, ctx)

        analyst.propose_decision_options.assert_not_called()
        assert len(items) == 1
        # Without analyst options, the file gets only the base ladder +
        # the always-emitted Round-2/3 extras (no `analyst::` prefixed keys).
        assert not any(o.key.startswith("analyst::") for o in items[0].options)

    @pytest.mark.asyncio
    async def test_flag_on_prepends_analyst_options(self):
        from src.core.phases.plan_review import PlanReviewPhase

        state = _make_state(enable_analyst=True)
        analyst = MagicMock()
        analyst.propose_decision_options = AsyncMock(
            return_value=[
                {
                    "key": "merge-additions",
                    "label": "Merge fork+upstream struct additions",
                    "description": "Both sides only added fields.",
                    "preview": "struct { ... }",
                }
            ]
        )

        git_tool = MagicMock()
        git_tool.get_three_way_diff = MagicMock(
            return_value=("base content", "fork content", "upstream content")
        )

        ctx = MagicMock()
        ctx.agents = {"conflict_analyst": analyst}
        ctx.git_tool = git_tool

        phase = PlanReviewPhase()
        items = await phase._build_user_decision_items(state, ctx)

        analyst.propose_decision_options.assert_awaited_once()
        assert len(items) == 1
        keys = [o.key for o in items[0].options]
        # analyst-proposed options come first (prepended) with the
        # ``analyst::`` namespace prefix.
        assert keys[0].startswith("analyst::")
        analyst_opt = items[0].options[0]
        assert analyst_opt.kind == "analyst_proposed"
        assert analyst_opt.label == "Merge fork+upstream struct additions"
        assert analyst_opt.preview == "struct { ... }"

    @pytest.mark.asyncio
    async def test_per_file_failure_does_not_break_others(self):
        from src.core.phases.plan_review import PlanReviewPhase

        state = _make_state(enable_analyst=True)
        # Add a second HR file so one can fail while the other succeeds.
        state.merge_plan.phases[0].file_paths.append("routers/web/auth/oauth.go")
        state.file_diffs.append(
            FileDiff(
                file_path="routers/web/auth/oauth.go",
                file_status=FileStatus.MODIFIED,
                risk_level=RiskLevel.HUMAN_REQUIRED,
                risk_score=0.8,
                lines_added=3,
                lines_deleted=0,
                upstream_lines_added=2,
                upstream_lines_deleted=0,
                change_category=FileChangeCategory.C,
                language="go",
            )
        )

        async def fake_propose(fp, *args, **kwargs):
            if fp.endswith("auth_token.go"):
                raise RuntimeError("simulated LLM crash")
            return [
                {
                    "key": "rework-oauth-flow",
                    "label": "Reorder OAuth audit + prompt",
                    "description": "Run validation, then audit.",
                }
            ]

        analyst = MagicMock()
        analyst.propose_decision_options = AsyncMock(side_effect=fake_propose)

        git_tool = MagicMock()
        git_tool.get_three_way_diff = MagicMock(return_value=("b", "f", "u"))

        ctx = MagicMock()
        ctx.agents = {"conflict_analyst": analyst}
        ctx.git_tool = git_tool

        phase = PlanReviewPhase()
        items = await phase._build_user_decision_items(state, ctx)
        by_path = {it.file_path: it for it in items}

        # auth_token.go: analyst crashed → no analyst-prefixed options,
        # but the base ladder is still present.
        token_opts = by_path["models/auth/auth_token.go"].options
        assert not any(o.kind == "analyst_proposed" for o in token_opts)
        assert any(o.kind == "keep_head" for o in token_opts)

        # oauth.go: analyst succeeded → analyst option is first.
        oauth_opts = by_path["routers/web/auth/oauth.go"].options
        assert oauth_opts[0].kind == "analyst_proposed"
        assert oauth_opts[0].key.startswith("analyst::")
