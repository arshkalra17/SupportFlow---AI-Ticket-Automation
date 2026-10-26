"""Load the retrieval models at startup instead of on the first customer request.

The embedding model and the re-ranker load lazily, so the first retrieval in a
process took 11.8 s against 0.1 s for the next ones (measured with a local
knowledge base; later calls 0.10 to 0.13 s). Whoever asked the first policy
question after a deploy paid for it. Running one throwaway search while the
service starts moves that cost to startup, where nobody is waiting on a reply.

WARM_UP_MODELS=0 skips it (the tests do). A failure is logged and ignored: a
service that starts cold is better than one that does not start.
"""

from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger(__name__)


def warm_up_models() -> float | None:
    """Returns the seconds the warm-up took, or None if it was skipped or failed."""
    if os.getenv("WARM_UP_MODELS", "1").strip().lower() in ("0", "false", "no", "off"):
        return None
    started = time.perf_counter()
    try:
        from app.kb import search_knowledge_base

        search_knowledge_base("warm up")
    except Exception as err:  # noqa: BLE001 - start cold rather than not start
        logger.warning("Model warm-up failed, first request will load them: %s", err)
        return None
    took = time.perf_counter() - started
    logger.info("Retrieval models warmed up in %.1fs", took)
    return took
