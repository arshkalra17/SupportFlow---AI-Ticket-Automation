import json
import logging
import os
import time
from dotenv import load_dotenv

from app.database import SessionLocal
from app.models import Ticket
from app.llm import classify_ticket
from app.queue import redis_client, QUEUE_NAME
from app.idempotency import is_transient_error

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("supportflow.worker")

MAX_JOB_RETRIES = 3


def process_single_job(payload_json: str, classify_fn=None) -> bool:
    """Processes a single ticket classification job from Redis with bounded retries.

    Args:
        payload_json (str): Raw JSON string from Redis queue.
        classify_fn (Callable, optional): Override for testing classification callback.

    Returns:
        bool: True if job was processed or safely handled/re-enqueued, False if unrecoverable payload error.
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

    retry_count = data.get("retry_count", 0)
    logger.info(f"Processing ticket_id={ticket_id} (retry_count={retry_count}/{MAX_JOB_RETRIES})...")

    db = SessionLocal()
    try:
        ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
        if not ticket:
            logger.warning(f"Ticket id={ticket_id} not found in database. Discarding job.")
            return True

        # Synchronously call classify_ticket or mock override
        try:
            fn = classify_fn or classify_ticket
            classification = fn(ticket.customer_message)
            ticket.category = classification.get("category")
            ticket.priority = classification.get("priority")
            ticket.status = "PROCESSED"
            db.commit()
            logger.info(
                f"Successfully processed ticket_id={ticket_id}: "
                f"category='{ticket.category}', priority='{ticket.priority}'"
            )
            return True
        except Exception as classification_err:
            if is_transient_error(classification_err) and retry_count < MAX_JOB_RETRIES:
                data["retry_count"] = retry_count + 1
                new_payload = json.dumps(data)
                redis_client.rpush(QUEUE_NAME, new_payload)
                logger.warning(
                    f"Transient failure for ticket_id={ticket_id} (attempt {retry_count + 1}/{MAX_JOB_RETRIES}): "
                    f"{classification_err}. Re-enqueued job."
                )
                return True
            else:
                logger.error(
                    f"Permanent failure for ticket_id={ticket_id} (retry_count={retry_count}): {classification_err}"
                )
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
