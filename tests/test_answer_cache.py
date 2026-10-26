"""
Policy-answer cache: a repeated question over the same policy text is answered
without a model call, and anything that could make the answer stale is a miss.

Retrieval and the model are mocked; the cache is fakeredis.
"""

from unittest.mock import MagicMock, patch

import pytest
import redis

from app import answer_cache, rag

pytestmark = pytest.mark.unit

DOC = {
    "title": "Return and Refund Policy", "heading": "Return window", "source": "returns.md",
    "content": "Returns are accepted within 30 days of delivery.", "cosine_similarity": 0.8,
}


def _llm_result(answer="You have 30 days."):
    message = MagicMock(content=answer)
    response = MagicMock(choices=[MagicMock(message=message)])
    return MagicMock(response=response, model="test-model")


@pytest.fixture(autouse=True)
def cache_on(monkeypatch, fake_redis):
    monkeypatch.setenv("ANSWER_CACHE_ENABLED", "1")
    return fake_redis


def _ask(query="How long do I have to return something?", docs=(DOC,), model=None):
    model = model or MagicMock(return_value=_llm_result())
    with patch("app.rag.search_knowledge_base", return_value=list(docs)), patch("app.rag.chat_completion", model):
        return rag.answer_with_knowledge_base(query), model


def test_a_repeated_question_is_answered_without_a_model_call():
    first, model = _ask()
    second, same_model = _ask(model=model)

    assert first["cache_hit"] is False and second["cache_hit"] is True
    assert second["answer"] == first["answer"] and second["model"] == "test-model"
    assert model.call_count == 1  # the second call never reached the model


def test_case_and_spacing_do_not_matter():
    _, model = _ask("How long do I have to return something?")
    again, _ = _ask("  how LONG do I   have to return something?  ", model=model)
    assert again["cache_hit"] is True and model.call_count == 1


def test_a_different_question_is_a_miss():
    _, model = _ask("How long do I have to return something?")
    other, _ = _ask("Can I return something after 45 days?", model=model)
    assert other["cache_hit"] is False and model.call_count == 2


def test_an_edited_policy_is_never_served_a_stale_answer():
    _, model = _ask()
    edited = {**DOC, "content": "Returns are accepted within 14 days of delivery."}
    after_edit, _ = _ask(docs=(edited,), model=model)
    assert after_edit["cache_hit"] is False and model.call_count == 2


def test_a_new_prompt_version_does_not_reuse_old_answers(monkeypatch):
    _, model = _ask()
    monkeypatch.setattr(rag, "RAG_PROMPT_VERSION", "rag-v2")
    again, _ = _ask(model=model)
    assert again["cache_hit"] is False and model.call_count == 2


def test_it_is_off_unless_enabled(monkeypatch):
    monkeypatch.delenv("ANSWER_CACHE_ENABLED")
    _, model = _ask()
    again, _ = _ask(model=model)
    assert again["cache_hit"] is False and model.call_count == 2


def test_out_of_scope_questions_reach_no_model_and_are_not_cached(cache_on):
    model = MagicMock()
    result, _ = _ask("what is the best cake recipe", docs=(), model=model)
    assert result["relevant_context"] is False and "cache_hit" not in result
    model.assert_not_called()
    assert cache_on.keys(f"{answer_cache.KEY_PREFIX}:*") == []


def test_entries_expire(cache_on, monkeypatch):
    monkeypatch.setenv("ANSWER_CACHE_TTL_SECONDS", "120")
    _ask()
    (key,) = cache_on.keys(f"{answer_cache.KEY_PREFIX}:*")
    assert 0 < cache_on.ttl(key) <= 120


def test_a_redis_outage_costs_a_model_call_not_the_answer(monkeypatch, caplog):
    broken = MagicMock()
    broken.get.side_effect = redis.ConnectionError("down")
    broken.set.side_effect = redis.ConnectionError("down")
    monkeypatch.setattr("app.queue.redis_client", broken)

    result, model = _ask()
    assert result["answer"] == "You have 30 days." and result["cache_hit"] is False
    assert model.call_count == 1
    assert "Answer cache unavailable" in caplog.text


def test_a_corrupt_entry_is_a_miss(cache_on):
    _, model = _ask()
    (key,) = cache_on.keys(f"{answer_cache.KEY_PREFIX}:*")
    cache_on.set(key, "not json{")
    again, _ = _ask(model=model)
    assert again["cache_hit"] is False and model.call_count == 2
