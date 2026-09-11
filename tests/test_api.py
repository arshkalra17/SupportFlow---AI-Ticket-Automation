"""
Stage 8 — HTTP API endpoint tests (integration, no Groq).

Covers:
- POST /tickets (create + queue)
- GET /tickets/{id} (found, not found)
- GET /orders/{id} (auth, ownership, not found)
- POST /support/process (JWT required, extra fields forbidden, idempotency header)
- POST /support/process with mocked LangGraph for deterministic behavior
"""

import pytest
from unittest.mock import patch, MagicMock


# ══════════════════════════════════════════════════════════════════════
# Ticket endpoints
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.integration
class TestTicketEndpoints:
    def test_create_ticket_returns_201(self, client, fake_redis):
        with patch("app.main.enqueue_ticket_processing"):
            resp = client.post("/tickets", json={"message": "My order is late."})
        assert resp.status_code == 201
        body = resp.json()
        assert "id" in body
        assert body["status"] == "PENDING"

    def test_create_ticket_stores_message(self, client, db, fake_redis):
        with patch("app.main.enqueue_ticket_processing"):
            resp = client.post("/tickets", json={"message": "Order #1001 issue"})
        assert resp.status_code == 201
        ticket_id = resp.json()["id"]

        resp2 = client.get(f"/tickets/{ticket_id}")
        assert resp2.status_code == 200
        assert resp2.json()["customer_message"] == "Order #1001 issue"

    def test_get_ticket_not_found_returns_404(self, client):
        resp = client.get("/tickets/999999")
        assert resp.status_code == 404

    def test_get_ticket_found(self, client, fake_redis):
        with patch("app.main.enqueue_ticket_processing"):
            create_resp = client.post("/tickets", json={"message": "Help needed"})
        ticket_id = create_resp.json()["id"]
        resp = client.get(f"/tickets/{ticket_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == ticket_id


# ══════════════════════════════════════════════════════════════════════
# Order endpoint — authorization
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.integration
class TestOrderEndpoint:
    def test_get_owned_order_returns_200(self, client, customer, orders, auth_headers):
        resp = client.get("/orders/1001", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["order_id"] == 1001
        assert resp.json()["status"] == "SHIPPED"

    def test_get_unowned_order_returns_403(self, client, customer, orders, auth_headers):
        # Order 1004 belongs to customer2
        resp = client.get("/orders/1004", headers=auth_headers)
        assert resp.status_code == 403

    def test_get_nonexistent_order_returns_200_with_error(self, client, customer, auth_headers):
        """
        authorize_and_get_order_status returns (True, None, {"error": "not found"})
        for missing orders — this is surfaced as a successful authorized response.
        """
        resp = client.get("/orders/99999", headers=auth_headers)
        assert resp.status_code == 200
        assert "error" in resp.json()

    def test_get_order_without_jwt_returns_401(self, client, orders):
        resp = client.get("/orders/1001")
        assert resp.status_code == 401


# ══════════════════════════════════════════════════════════════════════
# POST /support/process — request validation
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.integration
class TestSupportProcessValidation:
    def test_missing_jwt_returns_401(self, client):
        resp = client.post("/support/process", json={"message": "hello"})
        assert resp.status_code == 401

    def test_extra_field_in_body_returns_422(self, client, auth_headers):
        """ConfigDict(extra='forbid') must reject unexpected fields."""
        resp = client.post(
            "/support/process",
            json={"message": "hello", "authenticated_customer_id": 999},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_missing_message_field_returns_422(self, client, auth_headers):
        resp = client.post(
            "/support/process",
            json={},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_empty_message_accepted(self, client, auth_headers, customer):
        """Empty string is a valid message field (business logic handles it)."""
        mock_state = {
            "final_response": "How can I help you?",
            "classification": {"category": "General Inquiry", "priority": "LOW", "sentiment": "NEUTRAL"},
            "tool_calls": [],
            "approval_status": None,
            "retrieved_documents": [],
            "error": None,
        }
        with patch("app.main.run_supportflow", return_value=mock_state):
            resp = client.post(
                "/support/process",
                json={"message": ""},
                headers=auth_headers,
            )
        assert resp.status_code == 200


# ══════════════════════════════════════════════════════════════════════
# POST /support/process — idempotency header
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.integration
class TestSupportProcessIdempotency:
    def _mock_state(self):
        return {
            "final_response": "Order #1001 status is SHIPPED.",
            "classification": {"category": "Order Issue", "priority": "LOW", "sentiment": "NEUTRAL"},
            "tool_calls": [{"tool_name": "get_order_status", "tool_args": {"order_id": 1001}}],
            "approval_status": None,
            "retrieved_documents": [],
            "error": None,
        }

    def test_first_request_executes(self, client, customer, orders, auth_headers):
        with patch("app.main.run_supportflow", return_value=self._mock_state()):
            resp = client.post(
                "/support/process",
                json={"message": "Status of order 1001?"},
                headers={**auth_headers, "Idempotency-Key": "api-test-key-001"},
            )
        assert resp.status_code == 200
        assert resp.json()["idempotency_replayed"] is False

    def test_repeated_request_replays(self, client, customer, orders, auth_headers):
        call_count = {"n": 0}

        def mock_run(**kwargs):
            call_count["n"] += 1
            return self._mock_state()

        with patch("app.main.run_supportflow", side_effect=mock_run):
            client.post(
                "/support/process",
                json={"message": "Status of order 1001?"},
                headers={**auth_headers, "Idempotency-Key": "api-test-key-replay"},
            )
            resp2 = client.post(
                "/support/process",
                json={"message": "Status of order 1001?"},
                headers={**auth_headers, "Idempotency-Key": "api-test-key-replay"},
            )
        assert resp2.status_code == 200
        assert resp2.json()["idempotency_replayed"] is True
        # run_supportflow must have been called exactly once
        assert call_count["n"] == 1

    def test_payload_mismatch_returns_409(self, client, customer, orders, auth_headers):
        with patch("app.main.run_supportflow", return_value=self._mock_state()):
            client.post(
                "/support/process",
                json={"message": "First message"},
                headers={**auth_headers, "Idempotency-Key": "api-conflict-key"},
            )
            resp = client.post(
                "/support/process",
                json={"message": "Different message"},
                headers={**auth_headers, "Idempotency-Key": "api-conflict-key"},
            )
        assert resp.status_code == 409

    def test_without_idempotency_key_always_executes(self, client, customer, auth_headers):
        call_count = {"n": 0}

        def mock_run(**kwargs):
            call_count["n"] += 1
            return self._mock_state()

        with patch("app.main.run_supportflow", side_effect=mock_run):
            client.post("/support/process", json={"message": "hello"}, headers=auth_headers)
            client.post("/support/process", json={"message": "hello"}, headers=auth_headers)

        assert call_count["n"] == 2


# ══════════════════════════════════════════════════════════════════════
# POST /support/process — identity isolation
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.integration
class TestSupportProcessIdentityIsolation:
    def test_identity_comes_from_jwt_not_body(
        self, client, customer, customer2, orders, auth_headers
    ):
        """
        The authenticated_customer_id used inside run_supportflow must
        be derived from the JWT, not from any client-supplied value.
        """
        captured = {}

        def mock_run(customer_message, authenticated_customer_id, **kwargs):
            captured["customer_id"] = authenticated_customer_id
            return {
                "final_response": "OK",
                "classification": None,
                "tool_calls": [],
                "approval_status": None,
                "retrieved_documents": [],
                "error": None,
            }

        with patch("app.main.run_supportflow", side_effect=mock_run):
            client.post(
                "/support/process",
                json={"message": "check my order"},
                headers=auth_headers,  # JWT for customer (not customer2)
            )

        assert captured["customer_id"] == customer.id

    def test_customer2_jwt_uses_customer2_identity(
        self, client, customer, customer2, orders, auth_headers2
    ):
        captured = {}

        def mock_run(customer_message, authenticated_customer_id, **kwargs):
            captured["customer_id"] = authenticated_customer_id
            return {
                "final_response": "OK",
                "classification": None,
                "tool_calls": [],
                "approval_status": None,
                "retrieved_documents": [],
                "error": None,
            }

        with patch("app.main.run_supportflow", side_effect=mock_run):
            client.post(
                "/support/process",
                json={"message": "check my order"},
                headers=auth_headers2,
            )

        assert captured["customer_id"] == customer2.id
