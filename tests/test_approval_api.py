"""
Tests for admin approval API endpoints.

Coverage:
- GET /approvals/pending (admin only)
- POST /approvals/{id}/approve (admin only)
- POST /approvals/{id}/reject (admin only)
- Authorization: admin vs non-admin
- Error handling: 404, 409 conflict
- resolved_by comes from authenticated admin
"""

import pytest
from app.models import Action, Approval, Customer
from app.auth import hash_password


@pytest.fixture()
def admin_customer(db):
    """Creates an admin customer for testing."""
    admin = Customer(
        email="admin@example.com",
        password_hash=hash_password("adminpass"),
        is_admin=1,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    return admin


@pytest.fixture()
def admin_token(admin_customer):
    """Returns a valid JWT token for the admin customer."""
    from app.auth import create_access_token
    return create_access_token(customer_id=admin_customer.id)


@pytest.fixture()
def admin_headers(admin_token):
    """Returns Authorization headers for admin."""
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture()
def pending_approvals(db, customer):
    """Creates test approval requests in PENDING state."""
    # Create actions
    action1 = Action(
        action_type="REFUND",
        reference_id=1001,
        customer_id=customer.id,
        amount=99.99,
        status="PENDING",
    )
    action2 = Action(
        action_type="REPLACEMENT_REQUEST",
        reference_id=1002,
        customer_id=customer.id,
        amount=None,
        status="PENDING",
    )
    db.add_all([action1, action2])
    db.flush()

    # Create linked approvals
    approval1 = Approval(action_id=action1.id, status="PENDING")
    approval2 = Approval(action_id=action2.id, status="PENDING")
    db.add_all([approval1, approval2])
    db.commit()
    db.refresh(approval1)
    db.refresh(approval2)

    return [approval1, approval2]


@pytest.fixture()
def resolved_approval(db, customer):
    """Creates an already-resolved (APPROVED) approval for conflict tests."""
    action = Action(
        action_type="REFUND",
        reference_id=2001,
        customer_id=customer.id,
        amount=50.00,
        status="COMPLETED",
    )
    db.add(action)
    db.flush()

    approval = Approval(
        action_id=action.id,
        status="APPROVED",
        resolved_by="previous.admin@example.com",
        reason="Already processed",
    )
    db.add(approval)
    db.commit()
    db.refresh(approval)
    return approval


# ── Test 1: Admin can list pending approvals ──────────────────────────

def test_admin_can_list_pending_approvals(client, admin_headers, pending_approvals, customer):
    """Admin can retrieve all PENDING approvals."""
    response = client.get("/approvals/pending", headers=admin_headers)

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 2

    # Verify structure
    first = data[0]
    assert "approval_id" in first
    assert "action_id" in first
    assert "action_type" in first
    assert "reference_id" in first
    assert "customer_id" in first
    assert "amount" in first
    assert "approval_status" in first
    assert first["approval_status"] == "PENDING"
    assert "created_at" in first

    # Verify customer_id matches
    assert first["customer_id"] == customer.id


# ── Test 2: Non-admin cannot list pending approvals ───────────────────

def test_non_admin_cannot_list_pending_approvals(client, auth_headers, pending_approvals):
    """Non-admin customer receives 403 Forbidden."""
    response = client.get("/approvals/pending", headers=auth_headers)

    assert response.status_code == 403
    assert "admin privileges required" in response.json()["detail"].lower()


# ── Test 3: Admin can approve a PENDING approval ──────────────────────

def test_admin_can_approve_pending_approval(client, admin_headers, admin_customer, pending_approvals, db):
    """Admin can approve a PENDING approval and action transitions to COMPLETED."""
    approval_id = pending_approvals[0].id

    response = client.post(
        f"/approvals/{approval_id}/approve",
        headers=admin_headers,
        json={"reason": "Verified legitimate request"},
    )

    assert response.status_code == 200
    data = response.json()

    # Verify response structure
    assert data["approval_id"] == approval_id
    assert data["approval_status"] == "APPROVED"
    assert data["action_status"] == "COMPLETED"
    assert data["resolved_by"] == admin_customer.email  # Uses admin's email
    assert data["reason"] == "Verified legitimate request"
    assert data["executed"] is True
    assert data["resolved_at"] is not None

    # Verify database state
    db.refresh(pending_approvals[0])
    assert pending_approvals[0].status == "APPROVED"
    assert pending_approvals[0].resolved_by == admin_customer.email


# ── Test 4: Admin can reject a PENDING approval ───────────────────────

def test_admin_can_reject_pending_approval(client, admin_headers, admin_customer, pending_approvals, db):
    """Admin can reject a PENDING approval and action transitions to REJECTED."""
    approval_id = pending_approvals[1].id

    response = client.post(
        f"/approvals/{approval_id}/reject",
        headers=admin_headers,
        json={"reason": "Insufficient documentation"},
    )

    assert response.status_code == 200
    data = response.json()

    # Verify response structure
    assert data["approval_id"] == approval_id
    assert data["approval_status"] == "REJECTED"
    assert data["action_status"] == "REJECTED"
    assert data["resolved_by"] == admin_customer.email  # Uses admin's email
    assert data["reason"] == "Insufficient documentation"
    assert data["executed"] is False
    assert data["resolved_at"] is not None

    # Verify database state
    db.refresh(pending_approvals[1])
    assert pending_approvals[1].status == "REJECTED"
    assert pending_approvals[1].resolved_by == admin_customer.email


# ── Test 5: Non-admin cannot approve ──────────────────────────────────

def test_non_admin_cannot_approve(client, auth_headers, pending_approvals):
    """Non-admin customer receives 403 when attempting to approve."""
    approval_id = pending_approvals[0].id

    response = client.post(
        f"/approvals/{approval_id}/approve",
        headers=auth_headers,
        json={"reason": "Should not work"},
    )

    assert response.status_code == 403
    assert "admin privileges required" in response.json()["detail"].lower()


# ── Test 6: Non-admin cannot reject ───────────────────────────────────

def test_non_admin_cannot_reject(client, auth_headers, pending_approvals):
    """Non-admin customer receives 403 when attempting to reject."""
    approval_id = pending_approvals[0].id

    response = client.post(
        f"/approvals/{approval_id}/reject",
        headers=auth_headers,
        json={"reason": "Should not work"},
    )

    assert response.status_code == 403
    assert "admin privileges required" in response.json()["detail"].lower()


# ── Test 7: Missing approval returns 404 ──────────────────────────────

def test_approve_missing_approval_returns_404(client, admin_headers):
    """Approving a non-existent approval returns 404."""
    response = client.post(
        "/approvals/99999/approve",
        headers=admin_headers,
        json={"reason": "Test"},
    )

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_reject_missing_approval_returns_404(client, admin_headers):
    """Rejecting a non-existent approval returns 404."""
    response = client.post(
        "/approvals/99999/reject",
        headers=admin_headers,
        json={"reason": "Test"},
    )

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


# ── Test 8: Approving already-resolved approval returns 409 ───────────

def test_approve_already_resolved_returns_409(client, admin_headers, resolved_approval):
    """Approving an already-resolved approval returns 409 Conflict."""
    response = client.post(
        f"/approvals/{resolved_approval.id}/approve",
        headers=admin_headers,
        json={"reason": "Too late"},
    )

    assert response.status_code == 409
    assert "already" in response.json()["detail"].lower()
    assert "approved" in response.json()["detail"].lower()


# ── Test 9: Rejecting already-resolved approval returns 409 ───────────

def test_reject_already_resolved_returns_409(client, admin_headers, resolved_approval):
    """Rejecting an already-resolved approval returns 409 Conflict."""
    response = client.post(
        f"/approvals/{resolved_approval.id}/reject",
        headers=admin_headers,
        json={"reason": "Too late"},
    )

    assert response.status_code == 409
    assert "already" in response.json()["detail"].lower()
    assert "approved" in response.json()["detail"].lower()


# ── Test 10: resolved_by comes from authenticated admin ───────────────

def test_resolved_by_uses_authenticated_admin_email(client, admin_headers, admin_customer, pending_approvals, db):
    """The resolved_by field is set from the authenticated admin's email, not request body."""
    approval_id = pending_approvals[0].id

    # Attempt to provide a fake resolved_by in the request (should be ignored)
    response = client.post(
        f"/approvals/{approval_id}/approve",
        headers=admin_headers,
        json={
            "reason": "Test",
            "resolved_by": "fake.admin@example.com",  # Should be ignored
        },
    )

    assert response.status_code == 200
    data = response.json()

    # Verify the resolved_by is the actual authenticated admin, not the fake one
    assert data["resolved_by"] == admin_customer.email
    assert data["resolved_by"] != "fake.admin@example.com"

    # Verify database state
    db.refresh(pending_approvals[0])
    assert pending_approvals[0].resolved_by == admin_customer.email


# ── Test 11: Approve without reason (optional) ────────────────────────

def test_approve_without_reason(client, admin_headers, admin_customer, db, customer):
    """Admin can approve without providing a reason."""
    # Create a new pending approval
    action = Action(
        action_type="REFUND",
        reference_id=3001,
        customer_id=customer.id,
        amount=25.00,
        status="PENDING",
    )
    db.add(action)
    db.flush()

    approval = Approval(action_id=action.id, status="PENDING")
    db.add(approval)
    db.commit()
    db.refresh(approval)

    response = client.post(
        f"/approvals/{approval.id}/approve",
        headers=admin_headers,
        json={},  # No reason provided
    )

    assert response.status_code == 200
    data = response.json()
    assert data["approval_status"] == "APPROVED"
    assert data["reason"] is None
    assert data["resolved_by"] == admin_customer.email


# ── Test 12: Unauthenticated requests return 401 ──────────────────────

def test_unauthenticated_list_pending_returns_401(client):
    """Unauthenticated request to list pending approvals returns 401."""
    response = client.get("/approvals/pending")
    assert response.status_code == 401


def test_unauthenticated_approve_returns_401(client, pending_approvals):
    """Unauthenticated request to approve returns 401."""
    response = client.post(
        f"/approvals/{pending_approvals[0].id}/approve",
        json={"reason": "Test"},
    )
    assert response.status_code == 401


def test_unauthenticated_reject_returns_401(client, pending_approvals):
    """Unauthenticated request to reject returns 401."""
    response = client.post(
        f"/approvals/{pending_approvals[0].id}/reject",
        json={"reason": "Test"},
    )
    assert response.status_code == 401
