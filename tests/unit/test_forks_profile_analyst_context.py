"""ConflictAnalyst forks-profile context injection.

Two layers of coverage:

1. ``format_analyst_context()`` — pure formatter unit tests
2. ``ConflictAnalystAgent.analyze_file()`` — end-to-end check that the
   profile block is passed through to ``build_conflict_analysis_prompt``
   via ``enriched_context``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.conflict_analyst_agent import ConflictAnalystAgent
from src.models.config import AgentLLMConfig
from src.models.diff import FileDiff, FileStatus, RiskLevel
from src.models.forks_profile import ForksProfile
from src.tools.forks_profile_loader import (
    format_analyst_context,
    is_path_profile_pinned,
)


def _profile() -> ForksProfile:
    return ForksProfile.model_validate(
        {
            "fork": {"name": "demo", "positioning": "internal-only baseline"},
            "removed_domains": [
                {
                    "name": "alpha",
                    "paths": ["svc/alpha/**"],
                    "reason": "out of scope",
                }
            ],
            "rewritten_modules": [
                {
                    "path": "svc/auth/**",
                    "policy": "escalate_human",
                    "note": "custom SSO",
                }
            ],
        }
    )


class TestFormatAnalystContext:
    def test_empty_profile_returns_empty_string(self):
        assert format_analyst_context(ForksProfile.model_validate({}), "x") == ""

    def test_positioning_only_still_emits_header(self):
        profile = ForksProfile.model_validate({"fork": {"positioning": "small fork"}})
        out = format_analyst_context(profile, "any/path.py")
        assert "Fork identity" in out
        assert "small fork" in out

    def test_includes_removed_domains_summary(self):
        out = format_analyst_context(_profile(), "unrelated/file.py")
        assert "Removed domains" in out
        assert "alpha: out of scope" in out

    def test_includes_rewritten_modules_summary(self):
        out = format_analyst_context(_profile(), "unrelated/file.py")
        assert "Rewritten modules" in out
        assert "svc/auth/**: escalate_human" in out
        assert "custom SSO" in out

    def test_no_per_file_section_when_no_match(self):
        out = format_analyst_context(_profile(), "unrelated/file.py")
        assert "This file:" not in out

    def test_per_file_section_when_path_in_removed_domain(self):
        out = format_analyst_context(_profile(), "svc/alpha/x.py")
        assert "This file:" in out
        assert "removed_domains[alpha]" in out
        assert "Avoid recommending take_target" in out

    def test_per_file_section_when_path_in_rewritten_module(self):
        out = format_analyst_context(_profile(), "svc/auth/login.py")
        assert "This file:" in out
        assert "rewritten_modules[svc/auth/**]" in out
        assert "policy=escalate_human" in out

    def test_truncates_when_many_removed_domains(self):
        domains = [
            {"name": f"d{i}", "paths": [f"d{i}/**"], "reason": f"r{i}"}
            for i in range(15)
        ]
        profile = ForksProfile.model_validate({"removed_domains": domains})
        out = format_analyst_context(profile, "unrelated/x.py")
        assert "(+7 more)" in out


class TestIsPathProfilePinned:
    def test_match_in_removed_domain(self):
        assert is_path_profile_pinned(_profile(), "svc/alpha/x.py") is True

    def test_match_in_rewritten_module(self):
        assert is_path_profile_pinned(_profile(), "svc/auth/login.py") is True

    def test_no_match(self):
        assert is_path_profile_pinned(_profile(), "src/random.py") is False


@pytest.mark.asyncio
async def test_analyze_file_prepends_profile_block_to_context():
    """End-to-end: forks-profile context lands in build_conflict_analysis_prompt."""
    with patch("src.llm.client.LLMClientFactory.create"):
        agent = ConflictAnalystAgent(AgentLLMConfig())

    file_diff = FileDiff(
        file_path="svc/auth/login.py",
        file_status=FileStatus.MODIFIED,
        hunks=[],
        lines_added=1,
        lines_deleted=1,
        risk_score=0.5,
        risk_level=RiskLevel.AUTO_RISKY,
    )

    captured: dict[str, str] = {}

    def fake_build(file_diff, base, current, target, context, **kwargs):
        captured["context"] = context or ""
        return "PROMPT"

    fake_parse = MagicMock(return_value=MagicMock(file_path="svc/auth/login.py"))

    agent._call_llm_with_retry = AsyncMock(return_value="{}")
    with (
        patch(
            "src.agents.conflict_analyst_agent.build_conflict_analysis_prompt",
            side_effect=fake_build,
        ),
        patch(
            "src.agents.conflict_analyst_agent.parse_conflict_analysis",
            fake_parse,
        ),
    ):
        await agent.analyze_file(
            file_diff,
            base_content="b",
            current_content="c",
            target_content="t",
            project_context="ORIGINAL",
            forks_profile=_profile(),
        )

    ctx = captured["context"]
    assert "Fork identity" in ctx
    assert "rewritten_modules[svc/auth/**]" in ctx
    assert "ORIGINAL" in ctx
    assert ctx.index("Fork identity") < ctx.index("ORIGINAL")


@pytest.mark.asyncio
async def test_analyze_file_no_profile_keeps_original_context_unchanged():
    with patch("src.llm.client.LLMClientFactory.create"):
        agent = ConflictAnalystAgent(AgentLLMConfig())

    file_diff = FileDiff(
        file_path="src/random.py",
        file_status=FileStatus.MODIFIED,
        hunks=[],
        lines_added=1,
        lines_deleted=1,
        risk_score=0.5,
        risk_level=RiskLevel.AUTO_RISKY,
    )

    captured: dict[str, str] = {}

    def fake_build(file_diff, base, current, target, context, **kwargs):
        captured["context"] = context or ""
        return "PROMPT"

    fake_parse = MagicMock(return_value=MagicMock(file_path="src/random.py"))
    agent._call_llm_with_retry = AsyncMock(return_value="{}")
    with (
        patch(
            "src.agents.conflict_analyst_agent.build_conflict_analysis_prompt",
            side_effect=fake_build,
        ),
        patch(
            "src.agents.conflict_analyst_agent.parse_conflict_analysis",
            fake_parse,
        ),
    ):
        await agent.analyze_file(
            file_diff,
            base_content="b",
            current_content="c",
            target_content="t",
            project_context="ORIGINAL",
            forks_profile=None,
        )

    assert captured["context"] == "ORIGINAL"
