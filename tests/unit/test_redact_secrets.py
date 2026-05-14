from src.agents.base_agent import _redact_secrets


def test_redacts_anthropic_key():
    msg = "request failed: api_key=sk-ant-aaaa1111bbbb2222 timed out"
    out = _redact_secrets(msg)
    assert "sk-ant-aaaa1111bbbb2222" not in out
    assert "sk-ant-<redacted>" in out


def test_redacts_openai_key():
    msg = "Authorization: Bearer sk-proj-zzzz9999yyyy8888xxxx"
    out = _redact_secrets(msg)
    assert "sk-proj-zzzz9999yyyy8888xxxx" not in out


def test_redacts_bearer_header():
    msg = "Authorization: Bearer abcdef1234567890ABCDEF"
    out = _redact_secrets(msg)
    assert "abcdef1234567890ABCDEF" not in out
    assert "Bearer <redacted>" in out


def test_redacts_api_key_kv():
    msg = "config: api_key='abcdef1234567890ABCD' enabled=true"
    out = _redact_secrets(msg)
    assert "abcdef1234567890ABCD" not in out


def test_non_secret_passthrough():
    msg = "Bad request (anthropic): Operation failed"
    assert _redact_secrets(msg) == msg
