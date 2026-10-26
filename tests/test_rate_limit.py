"""
Rate limiting: sliding-window counter, per-customer limits, shared token budget.

No LLM and no real Redis: counters live in fakeredis and time is passed in, so
the window arithmetic is tested exactly.
"""

import pytest
from unittest.mock import MagicMock, patch

import redis

from app import rate_limit
from app.rate_limit import RateLimited, check_customer_rate, check_token_budget, hit, record_token_usage

pytestmark = pytest.mark.unit

MODEL = "test/budgeted-model"
T0 = 6000.0  # start of a minute bucket and of a day bucket (6000 = 100 * 60)


@pytest.fixture(autouse=True)
def limiter_on(monkeypatch, fake_redis):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "1")
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "3")
    monkeypatch.setenv("TOKEN_BUDGET_MODEL", MODEL)
    monkeypatch.setenv("TOKEN_BUDGET_PER_MINUTE", "1000")
    monkeypatch.setenv("TOKEN_BUDGET_PER_DAY", "5000")
    return fake_redis


# ── per-customer requests ─────────────────────────────────────────────

def test_requests_up_to_the_limit_pass_and_the_next_is_refused():
    for _ in range(3):
        check_customer_rate("support", "customer:1", now=T0)
    with pytest.raises(RateLimited) as err:
        check_customer_rate("support", "customer:1", now=T0)
    assert err.value.limit == 3 and err.value.retry_after >= 1


def test_customers_are_limited_independently():
    for _ in range(3):
        check_customer_rate("support", "customer:1", now=T0)
    check_customer_rate("support", "customer:2", now=T0)  # not affected


def test_scopes_are_limited_independently():
    for _ in range(3):
        check_customer_rate("support", "customer:1", now=T0)
    check_customer_rate("ticket", "customer:1", now=T0)


def test_a_refused_request_does_not_use_up_allowance(limiter_on):
    for _ in range(3):
        check_customer_rate("support", "customer:1", now=T0)
    for _ in range(5):
        with pytest.raises(RateLimited):
            check_customer_rate("support", "customer:1", now=T0)
    assert int(limiter_on.get(f"{rate_limit.KEY_PREFIX}:support:customer:1:{int(T0 // 60)}")) == 3


def test_retry_after_is_the_time_it_actually_takes():
    """limit 3 at the start of a bucket: refused, then allowed again exactly at the stated time."""
    for _ in range(3):
        check_customer_rate("support", "customer:1", now=T0)
    with pytest.raises(RateLimited) as err:
        check_customer_rate("support", "customer:1", now=T0)
    wait = err.value.retry_after
    assert wait == 80  # 60s to the next bucket, then 1/3 of it for the old hits to fade

    with pytest.raises(RateLimited):
        check_customer_rate("support", "customer:1", now=T0 + wait - 2)
    check_customer_rate("support", "customer:1", now=T0 + wait)


def test_a_full_window_later_the_whole_allowance_is_back():
    for _ in range(3):
        check_customer_rate("support", "customer:1", now=T0)
    for _ in range(3):
        check_customer_rate("support", "customer:1", now=T0 + 120)


def test_old_requests_fade_out_gradually_not_all_at_once():
    """Three hits late in one bucket: halfway through the next, only part of them still count."""
    for _ in range(3):
        check_customer_rate("support", "customer:1", now=T0 + 55)
    # 30s into the next bucket the previous one weighs 0.5 -> 1.5 counted, so one more fits
    check_customer_rate("support", "customer:1", now=T0 + 90)
    with pytest.raises(RateLimited):
        for _ in range(3):
            check_customer_rate("support", "customer:1", now=T0 + 90)


def test_a_boundary_does_not_double_the_allowance():
    """A plain fixed window would let 3 + 3 through across the boundary; this does not."""
    for _ in range(3):
        check_customer_rate("support", "customer:1", now=T0 + 59)
    with pytest.raises(RateLimited):
        check_customer_rate("support", "customer:1", now=T0 + 60)


def test_disabled_limiter_allows_everything(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "0")
    for _ in range(50):
        check_customer_rate("support", "customer:1", now=T0)


def test_redis_failure_fails_open(monkeypatch, limiter_on, caplog):
    broken = MagicMock()
    broken.pipeline.side_effect = redis.ConnectionError("down")
    monkeypatch.setattr("app.queue.redis_client", broken)
    for _ in range(10):
        check_customer_rate("support", "customer:1", now=T0)
    assert "Rate limiter unavailable" in caplog.text


# ── shared token budget ───────────────────────────────────────────────

def test_budget_allows_requests_until_tokens_are_spent():
    check_token_budget(now=T0)
    record_token_usage(MODEL, 600, now=T0)
    check_token_budget(now=T0)  # 600 of 1000
    record_token_usage(MODEL, 500, now=T0)
    with pytest.raises(RateLimited) as err:
        check_token_budget(now=T0)  # 1100 >= 1000
    assert err.value.scope == "tokens_minute" and err.value.limit == 1000


def test_budget_only_counts_the_budgeted_model():
    record_token_usage("some/other-model", 99999, now=T0)
    check_token_budget(now=T0)


def test_minute_budget_recovers_but_the_day_budget_holds(monkeypatch):
    monkeypatch.setenv("TOKEN_BUDGET_PER_MINUTE", "1000")
    for i in range(5):  # 5 minutes of 1000 tokens = the 5000 daily budget
        record_token_usage(MODEL, 1000, now=T0 + i * 60)
    with pytest.raises(RateLimited) as err:
        check_token_budget(now=T0 + 5 * 60 + 90)  # minute window is clear, day is spent
    assert err.value.scope == "tokens_day"
    assert err.value.retry_after > 60


def test_budget_retry_after_points_at_the_next_minute():
    record_token_usage(MODEL, 1000, now=T0 + 10)
    with pytest.raises(RateLimited) as err:
        check_token_budget(now=T0 + 10)
    assert 50 <= err.value.retry_after <= 120
    check_token_budget(now=T0 + 10 + err.value.retry_after)


def test_a_zero_budget_disables_that_window(monkeypatch):
    monkeypatch.setenv("TOKEN_BUDGET_PER_MINUTE", "0")
    monkeypatch.setenv("TOKEN_BUDGET_PER_DAY", "0")
    record_token_usage(MODEL, 10**7, now=T0)
    check_token_budget(now=T0)


def test_budget_redis_failure_fails_open(monkeypatch, caplog):
    broken = MagicMock()
    broken.mget.side_effect = redis.ConnectionError("down")
    monkeypatch.setattr("app.queue.redis_client", broken)
    check_token_budget(now=T0)
    assert "Token budget unavailable" in caplog.text


def test_llm_client_reports_usage_to_listeners():
    """Usage from a real call path reaches the budget: this is the wiring that feeds it."""
    from app import llm_client

    seen = []
    listener = lambda model, tokens: seen.append((model, tokens))
    llm_client.add_usage_listener(listener)
    try:
        response = MagicMock(usage=MagicMock(prompt_tokens=300, completion_tokens=45))
        llm_client._record_usage("m", response)
    finally:
        llm_client._usage_listeners.remove(listener)
    assert seen == [("m", 345)]


def test_a_failing_usage_listener_does_not_break_the_call():
    from app import llm_client

    def boom(model, tokens):
        raise RuntimeError("listener bug")

    llm_client.add_usage_listener(boom)
    try:
        llm_client._record_usage("m", MagicMock(usage=MagicMock(prompt_tokens=1, completion_tokens=1)))
    finally:
        llm_client._usage_listeners.remove(boom)


# ── HTTP behaviour ────────────────────────────────────────────────────

_STATE = {
    "final_response": "ok", "classification": {"category": "General Inquiry"},
    "tool_calls": [], "approval_status": None, "retrieved_documents": [], "error": None,
}


@pytest.mark.integration
class TestEndpoints:
    def test_support_endpoint_returns_429_with_retry_after(self, client, customer, auth_headers):
        with patch("app.main.run_supportflow", return_value=_STATE):
            codes = [client.post("/support/process", json={"message": f"hi {i}"}, headers=auth_headers).status_code
                     for i in range(4)]
        assert codes == [200, 200, 200, 429]

    def test_429_body_and_headers_say_what_to_do(self, client, customer, auth_headers):
        with patch("app.main.run_supportflow", return_value=_STATE):
            for i in range(3):
                client.post("/support/process", json={"message": f"hi {i}"}, headers=auth_headers)
            resp = client.post("/support/process", json={"message": "again"}, headers=auth_headers)
        assert resp.status_code == 429
        assert int(resp.headers["Retry-After"]) >= 1
        assert resp.headers["X-RateLimit-Limit"] == "3"
        assert resp.json()["detail"]["scope"] == "support"

    def test_one_customers_burst_does_not_block_another(self, client, customer, customer2, auth_headers, auth_headers2):
        with patch("app.main.run_supportflow", return_value=_STATE):
            for i in range(4):
                client.post("/support/process", json={"message": f"hi {i}"}, headers=auth_headers)
            resp = client.post("/support/process", json={"message": "hello"}, headers=auth_headers2)
        assert resp.status_code == 200

    def test_a_refused_request_never_reaches_the_pipeline(self, client, customer, auth_headers):
        with patch("app.main.run_supportflow", return_value=_STATE) as run:
            for i in range(6):
                client.post("/support/process", json={"message": f"hi {i}"}, headers=auth_headers)
        assert run.call_count == 3

    def test_spent_token_budget_refuses_before_any_model_call(self, client, customer, auth_headers):
        record_token_usage(MODEL, 10_000)
        with patch("app.main.run_supportflow", return_value=_STATE) as run:
            resp = client.post("/support/process", json={"message": "hi"}, headers=auth_headers)
        assert resp.status_code == 429
        assert resp.json()["detail"]["scope"] == "tokens_minute"
        run.assert_not_called()

    def test_authentication_is_checked_before_limits(self, client):
        assert client.post("/support/process", json={"message": "hi"}).status_code in (401, 403)

    def test_ticket_endpoint_limits_anonymous_callers_by_address(self, client):
        with patch("app.main.enqueue_ticket_processing"):
            codes = [client.post("/tickets", json={"message": f"late {i}"}).status_code for i in range(4)]
        assert codes == [201, 201, 201, 429]

    def test_ticket_limit_follows_the_customer_not_the_address(self, client, customer, auth_headers):
        with patch("app.main.enqueue_ticket_processing"):
            for i in range(3):
                client.post("/tickets", json={"message": f"late {i}"}, headers=auth_headers)
            anonymous = client.post("/tickets", json={"message": "late"})  # different identity
            known = client.post("/tickets", json={"message": "late"}, headers=auth_headers)
        assert anonymous.status_code == 201
        assert known.status_code == 429
