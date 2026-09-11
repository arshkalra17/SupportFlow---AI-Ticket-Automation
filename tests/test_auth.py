"""
Stage 8 — Authentication tests (deterministic, no Groq required).

Covers:
- Registration (success, duplicate email)
- Password hashing (verify correct + wrong passwords)
- Login (valid, wrong password, nonexistent user)
- JWT creation and verification
- Missing / invalid / expired JWT handling
- get_current_customer FastAPI dependency
- Cross-customer identity isolation via HTTP
"""

import time
import pytest
from datetime import timedelta

from app.auth import (
    hash_password,
    verify_password,
    create_access_token,
    decode_access_token,
)
from app.models import Customer
from fastapi import HTTPException


# ══════════════════════════════════════════════════════════════════════
# Password hashing
# ══════════════════════════════════════════════════════════════════════

class TestPasswordHashing:
    def test_hash_is_not_plaintext(self):
        pwd_hash = hash_password("secret123")
        assert pwd_hash != "secret123"

    def test_hash_is_bcrypt(self):
        pwd_hash = hash_password("secret123")
        assert pwd_hash.startswith("$2b$")

    def test_correct_password_verifies(self):
        pwd_hash = hash_password("correct_horse")
        assert verify_password("correct_horse", pwd_hash) is True

    def test_wrong_password_rejected(self):
        pwd_hash = hash_password("correct_horse")
        assert verify_password("wrong_horse", pwd_hash) is False

    def test_different_hashes_for_same_password(self):
        """bcrypt salts must produce different hashes each time."""
        h1 = hash_password("same_password")
        h2 = hash_password("same_password")
        assert h1 != h2
        # But both must still verify
        assert verify_password("same_password", h1) is True
        assert verify_password("same_password", h2) is True


# ══════════════════════════════════════════════════════════════════════
# JWT token utilities
# ══════════════════════════════════════════════════════════════════════

class TestJWTTokens:
    def test_token_creates_and_decodes(self):
        token = create_access_token(customer_id=42)
        payload = decode_access_token(token)
        assert payload["sub"] == "42"

    def test_expired_token_raises_401(self):
        token = create_access_token(
            customer_id=1,
            expires_delta=timedelta(seconds=-1),  # already expired
        )
        with pytest.raises(HTTPException) as exc_info:
            decode_access_token(token)
        assert exc_info.value.status_code == 401
        assert "expired" in exc_info.value.detail.lower()

    def test_malformed_token_raises_401(self):
        with pytest.raises(HTTPException) as exc_info:
            decode_access_token("not.a.valid.token")
        assert exc_info.value.status_code == 401

    def test_tampered_token_raises_401(self):
        token = create_access_token(customer_id=1)
        tampered = token[:-5] + "XXXXX"
        with pytest.raises(HTTPException) as exc_info:
            decode_access_token(tampered)
        assert exc_info.value.status_code == 401

    def test_token_payload_contains_expected_claims(self):
        token = create_access_token(customer_id=7)
        payload = decode_access_token(token)
        assert "sub" in payload
        assert "exp" in payload
        assert "iat" in payload
        assert payload["sub"] == "7"


# ══════════════════════════════════════════════════════════════════════
# HTTP registration and login endpoints
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.integration
class TestRegistrationEndpoint:
    def test_register_new_customer(self, client):
        resp = client.post("/auth/register", json={
            "email": "newuser@example.com",
            "password": "StrongPass1!",
        })
        assert resp.status_code == 201
        body = resp.json()
        assert body["email"] == "newuser@example.com"
        assert "id" in body
        # Password must never be returned
        assert "password" not in body
        assert "password_hash" not in body

    def test_register_duplicate_email_rejected(self, client):
        client.post("/auth/register", json={
            "email": "dup@example.com", "password": "pass1",
        })
        resp = client.post("/auth/register", json={
            "email": "dup@example.com", "password": "pass2",
        })
        assert resp.status_code == 400
        assert "already registered" in resp.json()["detail"].lower()

    def test_register_invalid_email_rejected(self, client):
        resp = client.post("/auth/register", json={
            "email": "not-an-email", "password": "pass1",
        })
        assert resp.status_code == 422


@pytest.mark.integration
class TestLoginEndpoint:
    def test_valid_login_returns_token(self, client):
        client.post("/auth/register", json={
            "email": "login@example.com", "password": "MyPass123",
        })
        resp = client.post("/auth/login", json={
            "email": "login@example.com", "password": "MyPass123",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert body["token_type"] == "bearer"

    def test_wrong_password_returns_401(self, client):
        client.post("/auth/register", json={
            "email": "wrongpwd@example.com", "password": "RealPass1",
        })
        resp = client.post("/auth/login", json={
            "email": "wrongpwd@example.com", "password": "WrongPass",
        })
        assert resp.status_code == 401

    def test_nonexistent_user_returns_401(self, client):
        resp = client.post("/auth/login", json={
            "email": "ghost@example.com", "password": "whatever",
        })
        assert resp.status_code == 401

    def test_login_token_is_valid_jwt(self, client):
        client.post("/auth/register", json={
            "email": "jwttest@example.com", "password": "Pass123",
        })
        resp = client.post("/auth/login", json={
            "email": "jwttest@example.com", "password": "Pass123",
        })
        token = resp.json()["access_token"]
        payload = decode_access_token(token)
        assert payload["sub"].isdigit()


# ══════════════════════════════════════════════════════════════════════
# Protected endpoint: missing / invalid / expired JWT
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.integration
class TestProtectedEndpointAuth:
    def test_missing_jwt_returns_401(self, client, orders):
        resp = client.get("/orders/1001")
        assert resp.status_code == 401

    def test_invalid_jwt_returns_401(self, client, orders):
        resp = client.get(
            "/orders/1001",
            headers={"Authorization": "Bearer completely.invalid.token"},
        )
        assert resp.status_code == 401

    def test_expired_jwt_returns_401(self, client, orders):
        token = create_access_token(customer_id=1, expires_delta=timedelta(seconds=-1))
        resp = client.get(
            "/orders/1001",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401

    def test_valid_jwt_accesses_owned_order(self, client, customer, orders, auth_headers):
        resp = client.get("/orders/1001", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["order_id"] == 1001

    def test_identity_cannot_be_overridden_via_request_body(self, client, customer, orders, auth_headers):
        """
        SupportProcessRequest has extra='forbid'.
        Passing authenticated_customer_id in JSON body must return 422,
        not silently accept a spoofed identity.
        """
        resp = client.post(
            "/support/process",
            json={"message": "hello", "authenticated_customer_id": 999},
            headers=auth_headers,
        )
        assert resp.status_code == 422
