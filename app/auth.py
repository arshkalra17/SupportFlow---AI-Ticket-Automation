"""JWT Authentication Module for SupportFlow.

Provides password hashing via bcrypt, JWT creation and verification using PyJWT,
and FastAPI dependency `get_current_customer()` for protecting endpoints.
"""

from datetime import datetime, timedelta, timezone
import os
import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Customer

# Secret configuration (never log, never hardcode in production)
JWT_SECRET = os.getenv("JWT_SECRET", "dev_secret_supportflow_key_change_in_prod")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

security_scheme = HTTPBearer(auto_error=False)


# ── Password Hashing ───────────────────────────────────────────────────

def hash_password(password: str) -> str:
    """Hashes a plaintext password using bcrypt."""
    pwd_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    """Verifies a plaintext password against a bcrypt hash."""
    return bcrypt.checkpw(plain_password.encode("utf-8"), password_hash.encode("utf-8"))


# ── JWT Token Utilities ────────────────────────────────────────────────

def create_access_token(customer_id: int, expires_delta: timedelta | None = None) -> str:
    """Generates a signed JWT access token for the given customer_id."""
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    payload = {
        "sub": str(customer_id),
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Decodes and validates a JWT access token.

    Raises:
        HTTPException 401: If token is expired or malformed.
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or malformed authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ── FastAPI Dependency ─────────────────────────────────────────────────

def get_current_customer(
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
    db: Session = Depends(get_db),
) -> Customer:
    """FastAPI dependency to extract and verify JWT identity from Authorization header.

    Returns the trusted `Customer` database object.
    Raises HTTP 401 for missing, invalid, or expired tokens, or non-existent customers.
    """
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header / Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    payload = decode_access_token(token)

    customer_id_str = payload.get("sub")
    if not customer_id_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload: missing subject claim",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        customer_id = int(customer_id_str)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid customer ID format in token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Customer associated with this token no longer exists",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return customer


def get_current_admin(
    current_customer: Customer = Depends(get_current_customer),
) -> Customer:
    """FastAPI dependency to ensure the current user is an admin.

    Returns the trusted `Customer` database object if they are an admin.
    Raises HTTP 403 if the customer is not an admin.
    """
    if not current_customer.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return current_customer
