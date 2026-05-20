from unittest.mock import AsyncMock, MagicMock, patch

from src.llm.connectivity import probe_model, probe_provider


def _fake_client(complete_return=None, complete_raises=None):
    client = MagicMock()
    if complete_raises is not None:
        client.complete = AsyncMock(side_effect=complete_raises)
    else:
        client.complete = AsyncMock(return_value=complete_return)
    return client


class TestProbeModel:
    async def test_success_reports_ok_with_latency(self):
        with patch(
            "src.llm.connectivity.LLMClientFactory.create",
            return_value=_fake_client(complete_return="pong"),
        ):
            result = await probe_model("anthropic", "claude-opus-4-6", "key", None)
        assert result.ok is True
        assert result.model == "claude-opus-4-6"
        assert result.latency_ms is not None and result.latency_ms >= 0
        assert result.detail == "ok"

    async def test_empty_response_is_failure(self):
        with patch(
            "src.llm.connectivity.LLMClientFactory.create",
            return_value=_fake_client(complete_return="   "),
        ):
            result = await probe_model("openai", "gpt-4o", "key", None)
        assert result.ok is False
        assert result.detail == "empty response"

    async def test_exception_is_classified_not_raised(self):
        with patch(
            "src.llm.connectivity.LLMClientFactory.create",
            return_value=_fake_client(complete_raises=RuntimeError("boom")),
        ):
            result = await probe_model("openai", "gpt-4o", "key", None)
        assert result.ok is False
        assert result.latency_ms is None
        # classify_error maps a bare RuntimeError to the UNKNOWN category.
        assert result.detail.startswith("unknown:")

    async def test_passes_overrides_to_factory(self):
        with patch(
            "src.llm.connectivity.LLMClientFactory.create",
            return_value=_fake_client(complete_return="pong"),
        ) as create_mock:
            await probe_model("openai", "gpt-4o", "secret", "https://gw/v1")
        kwargs = create_mock.call_args.kwargs
        assert kwargs["api_key_override"] == "secret"
        assert kwargs["base_url_override"] == "https://gw/v1"


class TestProbeProvider:
    async def test_empty_models_returns_empty(self):
        results = await probe_provider("openai", [], "key", None)
        assert results == []

    async def test_probes_every_model(self):
        with patch(
            "src.llm.connectivity.LLMClientFactory.create",
            return_value=_fake_client(complete_return="pong"),
        ):
            results = await probe_provider("anthropic", ["m1", "m2", "m3"], "key", None)
        assert [r.model for r in results] == ["m1", "m2", "m3"]
        assert all(r.ok for r in results)
