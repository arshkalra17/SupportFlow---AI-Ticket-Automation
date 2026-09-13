"""Database migration service with PostgreSQL advisory lock.

This service should run as a one-shot init container before API/worker services.
Uses PostgreSQL advisory locks to ensure only one migration runs at a time.
"""

import os
import sys
import logging
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
import time

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("migrate")

# PostgreSQL advisory lock ID (arbitrary 64-bit integer)
MIGRATION_LOCK_ID = 987654321


def run_migrations():
    """Run Alembic migrations with advisory lock."""
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        logger.error("DATABASE_URL environment variable is not set")
        sys.exit(1)

    engine = create_engine(database_url)
    
    logger.info("Acquiring PostgreSQL advisory lock...")
    
    try:
        with engine.connect() as conn:
            # Try to acquire advisory lock (non-blocking)
            result = conn.execute(
                text("SELECT pg_try_advisory_lock(:lock_id)"),
                {"lock_id": MIGRATION_LOCK_ID}
            )
            acquired = result.scalar()
            
            if not acquired:
                logger.info("Another migration is already running. Waiting...")
                # Wait for lock (blocking)
                conn.execute(
                    text("SELECT pg_advisory_lock(:lock_id)"),
                    {"lock_id": MIGRATION_LOCK_ID}
                )
                logger.info("Lock acquired after waiting.")
            else:
                logger.info("Advisory lock acquired.")
            
            conn.commit()
        
        # Run Alembic migrations
        logger.info("Running Alembic migrations...")
        from alembic.config import Config
        from alembic import command
        
        alembic_cfg = Config("alembic.ini")
        command.upgrade(alembic_cfg, "head")
        
        logger.info("✅ Migrations completed successfully.")
        
    except Exception as e:
        logger.error(f"❌ Migration failed: {e}", exc_info=True)
        sys.exit(1)
    finally:
        # Release advisory lock
        try:
            with engine.connect() as conn:
                conn.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"),
                    {"lock_id": MIGRATION_LOCK_ID}
                )
                conn.commit()
                logger.info("Advisory lock released.")
        except Exception as e:
            logger.warning(f"Failed to release advisory lock: {e}")
        
        engine.dispose()


if __name__ == "__main__":
    run_migrations()
