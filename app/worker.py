import json
import logging
import os
import time
# pyrefly: ignore [missing-import]
from dotenv import load_dotenv

from app.database import SessionLocal
from app.models import Ticket
from app.llm import classify_ticket
from app.queue import redis_client, QUEUE_NAME

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("supportflow.worker")


def process_single_job(payload_json: str) -> bool:
    """Processes a single ticket classification job from Redis.

    Args:
        payload_json (str): Raw JSON string from Redis queue.

    Returns:
        bool: True if job was processed or safely handled, False if DB/unrecoverable error occurred.
    """
    try:
        data = json.loads(payload_json)
        ticket_id = data.get("ticket_id")
    except Exception as err:
        logger.error(f"Invalid job payload JSON: {payload_json!r}. Error: {err}")
        return False

    if not ticket_id:
        logger.error(f"Job payload missing 'ticket_id': {data}")
        return False

    logger.info(f"Processing ticket_id={ticket_id}...")

    db = SessionLocal()
    try:
        ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
        if not ticket:
            logger.warning(f"Ticket id={ticket_id} not found in database. Discarding job.")
            return True

        # Synchronously call classify_ticket
        try:
            classification = classify_ticket(ticket.customer_message)
            ticket.category = classification.get("category")
            ticket.priority = classification.get("priority")
            ticket.status = "PROCESSED"
            db.commit()
            logger.info(
                f"Successfully processed ticket_id={ticket_id}: "
                f"category='{ticket.category}', priority='{ticket.priority}'"
            )
        except Exception as classification_err:
            logger.error(f"Classification failed for ticket_id={ticket_id}: {classification_err}")
            ticket.status = "CLASSIFICATION_FAILED"
            db.commit()

        return True
    except Exception as db_err:
        logger.error(f"Database error while processing ticket_id={ticket_id}: {db_err}")
        db.rollback()
        return False
    finally:
        db.close()


def run_worker(stop_after_empty: bool = False):
    """Main worker loop listening for jobs on Redis queue using BLPOP.

    Args:
        stop_after_empty (bool): If True, stops when queue times out (useful for testing).
    """
    logger.info(f"Worker started. Listening on queue '{QUEUE_NAME}'...")
    while True:
        try:
            pop_result = redis_client.blpop(QUEUE_NAME, timeout=2)
            if pop_result is None:
                if stop_after_empty:
                    logger.info("No more jobs found in queue. Exiting worker loop.")
                    break
                continue

            queue_name, payload_json = pop_result
            process_single_job(payload_json)
        except KeyboardInterrupt:
            logger.info("Worker stopped by user.")
            break
        except Exception as err:
            logger.error(f"Unexpected error in worker loop: {err}")
            time.sleep(1)


if __name__ == "__main__":
    run_worker()
