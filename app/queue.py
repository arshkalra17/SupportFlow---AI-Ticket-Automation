import json
import os
# pyrefly: ignore [missing-import]
from dotenv import load_dotenv
# pyrefly: ignore [missing-import]
import redis

from app.observability import traced_span, safe_set_attribute

load_dotenv()

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
QUEUE_NAME = os.getenv("TICKET_QUEUE_NAME", "supportflow:ticket_queue")

redis_client = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=0,
    decode_responses=True
)


@traced_span("queue.enqueue_ticket_processing")
def enqueue_ticket_processing(ticket_id: int) -> str:
    """Enqueues a ticket-processing job payload into Redis.

    Args:
        ticket_id (int): ID of the ticket to process.

    Returns:
        str: The JSON payload pushed to Redis.
    """
    from opentelemetry import trace
    span = trace.get_current_span()

    if not isinstance(ticket_id, int) or ticket_id <= 0:
        safe_set_attribute(span, "queue.validation_error", True)
        raise ValueError(f"Invalid ticket_id: {ticket_id}")

    safe_set_attribute(span, "queue.ticket_id", ticket_id)
    safe_set_attribute(span, "queue.queue_name", QUEUE_NAME)

    job_payload = {
        "ticket_id": ticket_id
    }
    payload_json = json.dumps(job_payload)

    try:
        redis_client.rpush(QUEUE_NAME, payload_json)
        safe_set_attribute(span, "queue.enqueued", True)
    except Exception as err:
        safe_set_attribute(span, "queue.enqueue_error", True)
        safe_set_attribute(span, "error.type", type(err).__name__)
        raise

    return payload_json
