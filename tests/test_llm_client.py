"""
Unit tests for the LLM provider chain (app/llm_client.py).
No network: both provider clients are mocked.
"""

from unittest.mock import MagicMock, patch

import pytest

from app import llm_client
from app.llm_client import LLMUnavailableError, chat_completion

pytestmark = pytest.mark.unit


class FakeAPIError(Exception):
    def __init__(self, message, status_code):
        super().__init__(message)
        self.status_code = status_code


@pytest.fixture(autouse=True)
def both_providers(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDERS", "groq,gemini")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test")
    llm_client.reset_circuit_breakers()
    yield
    llm_client.reset_circuit_breakers()


def _clients(groq_effect=None, gemini_effect=None):
    """Patches both provider clients; returns (groq_create, gemini_create)."""
    groq_create = MagicMock(side_effect=groq_effect, return_value="groq-response")
    gemini_create = MagicMock(side_effect=gemini_effect, return_value="gemini-response")

    def fake_client(provider):
        client = MagicMock()
        client.chat.completions.create = groq_create if provider == "groq" else gemini_create
        return client

    return patch.object(llm_client, "_client", side_effect=fake_client), groq_create, gemini_create


def test_primary_answers_without_touching_fallback():
    patcher, groq, gemini = _clients()
    with patcher:
        result = chat_completion(messages=[])
    assert (result.provider, result.response) == ("groq", "groq-response")
    assert result.model == "openai/gpt-oss-120b"
    gemini.assert_not_called()


def test_rate_limit_falls_back_to_gemini():
    patcher, groq, gemini = _clients(groq_effect=FakeAPIError("429 tokens per minute", 429))
    with patcher:
        result = chat_completion(messages=[], temperature=0.0)
    assert result.provider == "gemini" and result.model == "gemini-3.5-flash"
    assert gemini.call_args.kwargs == {"model": "gemini-3.5-flash", "messages": [], "temperature": 0.0}


def test_bad_request_is_not_retried_elsewhere():
    patcher, groq, gemini = _clients(groq_effect=FakeAPIError("invalid tool schema", 400))
    with patcher, pytest.raises(FakeAPIError):
        chat_completion(messages=[])
    gemini.assert_not_called()


def test_failed_provider_is_skipped_during_cooldown():
    patcher, groq, gemini = _clients(groq_effect=FakeAPIError("503 overloaded", 503))
    with patcher:
        chat_completion(messages=[])
        chat_completion(messages=[])
    assert groq.call_count == 1  # second call went straight to Gemini
    assert gemini.call_count == 2


def test_daily_quota_gets_the_long_cooldown(monkeypatch):
    patcher, _, _ = _clients(groq_effect=FakeAPIError("429 tokens per day (TPD): Limit 200000", 429))
    monkeypatch.setattr(llm_client.time, "monotonic", lambda: 1000.0)
    with patcher:
        chat_completion(messages=[])
    assert llm_client._unavailable_until["groq"] == 1000.0 + llm_client.DAILY_QUOTA_COOLDOWN_SECONDS


def test_all_providers_failing_raises_with_every_reason():
    patcher, _, _ = _clients(
        groq_effect=FakeAPIError("groq 429 per day", 429),
        gemini_effect=FakeAPIError("gemini 503", 503),
    )
    with patcher, pytest.raises(LLMUnavailableError) as err:
        chat_completion(messages=[])
    assert "groq 429 per day" in str(err.value) and "gemini 503" in str(err.value)


def test_pinned_provider_never_falls_back(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDERS", "groq")
    patcher, _, gemini = _clients(groq_effect=FakeAPIError("429", 429))
    with patcher, pytest.raises(LLMUnavailableError):
        chat_completion(messages=[])
    gemini.assert_not_called()


def test_provider_without_key_is_skipped(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY")
    patcher, groq, _ = _clients()
    with patcher:
        assert chat_completion(messages=[]).provider == "gemini"
    groq.assert_not_called()


def test_no_keys_is_a_configuration_error(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY")
    monkeypatch.delenv("GEMINI_API_KEY")
    with pytest.raises(ValueError, match="No LLM provider configured"):
        chat_completion(messages=[])


def test_unknown_provider_name_is_rejected(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDERS", "groq,gemni")
    with pytest.raises(ValueError, match="gemni"):
        chat_completion(messages=[])


def test_model_can_be_overridden(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test-model")
    assert llm_client.model_for("gemini") == "gemini-test-model"


@pytest.mark.parametrize("text, daily", [
    ("Rate limit reached on tokens per day (TPD): Limit 200000", True),
    ("Quota exceeded for metric GenerateRequestsPerDayPerProjectPerModel-FreeTier", True),
    ("Rate limit reached on tokens per minute (TPM): Limit 8000", False),
])
def test_daily_quota_detection(text, daily):
    assert llm_client.is_daily_quota(text) is daily


@pytest.mark.parametrize("text, expected", [
    ("429 on tokens per minute (TPM): Limit 8000. Please try again in 2.55s.", 2.55),
    ("429 Please try again in 1m40.224s.", 100.224),
    ("429 TPM. Please try again in 840ms.", 0.84),
    ("429 tokens per day (TPD). Please try again in 1m40s.", None),  # daily: don't wait
    ("429 no hint here", None),
])
def test_retry_after_parsing(text, expected):
    assert llm_client.retry_after_seconds(FakeAPIError(text, 429)) == (
        pytest.approx(expected) if expected is not None else None
    )


def test_short_rate_limit_is_waited_out_on_the_last_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDERS", "groq")
    monkeypatch.setattr(llm_client.time, "sleep", lambda s: None)
    patcher, groq, _ = _clients(groq_effect=[FakeAPIError("429 TPM. try again in 2.5s", 429), "groq-response"])
    with patcher:
        assert chat_completion(messages=[]).response == "groq-response"
    assert groq.call_count == 2


def test_short_rate_limit_falls_back_when_another_provider_exists(monkeypatch):
    slept = []
    monkeypatch.setattr(llm_client.time, "sleep", slept.append)
    patcher, groq, gemini = _clients(groq_effect=FakeAPIError("429 TPM. try again in 2.5s", 429))
    with patcher:
        assert chat_completion(messages=[]).provider == "gemini"
    assert slept == [] and groq.call_count == 1


def test_small_tier_uses_small_model_and_low_reasoning():
    patcher, groq, _ = _clients()
    with patcher:
        result = chat_completion(tier="small", messages=[])
    assert result.model == "openai/gpt-oss-20b"
    assert groq.call_args.kwargs["reasoning_effort"] == "low"


def test_usage_is_accumulated_per_model():
    llm_client.reset_usage()
    response = MagicMock()
    response.usage.prompt_tokens, response.usage.completion_tokens = 100, 20
    patcher, _, _ = _clients()
    with patcher:
        with patch.object(llm_client, "_client") as client:
            client.return_value.chat.completions.create.return_value = response
            chat_completion(messages=[])
            chat_completion(messages=[])
    assert llm_client.get_usage() == {
        "openai/gpt-oss-120b": {"calls": 2, "prompt_tokens": 200, "completion_tokens": 40}
    }
