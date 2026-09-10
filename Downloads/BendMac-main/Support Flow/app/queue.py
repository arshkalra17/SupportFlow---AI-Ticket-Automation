import json
import os
# pyrefly: ignore [missing-import]
from dotenv import load_dotenv
# pyrefly: ignore [missing-import]
import redis

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


def enqueue_ticket_processing(ticket_id: int) -> str:
    """Enqueues a ticket-processing job payload into Redis.

    Args:
        ticket_id (int): ID of the ticket to process.

    Returns:
        str: The JSON payload pushed to Redis.
    """
    if not isinstance(ticket_id, int) or ticket_id <= 0:
        raise ValueError(f"Invalid ticket_id: {ticket_id}")

    job_payload = {
        "ticket_id": ticket_id
    }
    payload_json = json.dumps(job_payload)

    redis_client.rpush(QUEUE_NAME, payload_json)
    return payload_json
