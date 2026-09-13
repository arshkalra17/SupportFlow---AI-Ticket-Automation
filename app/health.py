"""Health check endpoints for liveness and readiness probes.

Liveness: process-only check (always succeeds if process is running)
Readiness: verifies PostgreSQL and Redis connectivity
"""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import text
from app.database import engine
from app.queue import redis_client

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
def liveness_probe():
    """Process-only liveness check.
    
    Returns 200 if the process is running.
    Does NOT fail if dependencies are unavailable.
    """
    return {"status": "alive"}


@router.get("/ready")
def readiness_probe():
    """Readiness check for PostgreSQL and Redis connectivity.
    
    Returns 200 only if both PostgreSQL and Redis are reachable.
    """
    health_status = {
        "postgres": "unknown",
        "redis": "unknown",
    }
    all_ready = True

    # Check PostgreSQL
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        health_status["postgres"] = "ready"
    except Exception as e:
        health_status["postgres"] = f"unavailable: {type(e).__name__}"
        all_ready = False

    # Check Redis
    try:
        redis_client.ping()
        health_status["redis"] = "ready"
    except Exception as e:
        health_status["redis"] = f"unavailable: {type(e).__name__}"
        all_ready = False

    if not all_ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=health_status,
        )

    return {"status": "ready", "checks": health_status}
