"""
LangGraph checkpointer backed by the app's PostgreSQL database.

Graph state is saved after every node, keyed by thread_id. The
conversation id is the thread id, so a run paused for human approval
can be resumed later, by a different request or a different process,
from exactly where it stopped.

The checkpointer uses its own psycopg 3 connection pool (LangGraph's
saver needs autocommit connections); its tables are created on first use
by PostgresSaver.setup(), which is idempotent.
"""

from __future__ import annotations

import re
import threading

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.database import DATABASE_URL

_lock = threading.Lock()
_pool: ConnectionPool | None = None
_saver: PostgresSaver | None = None


def _conninfo(url: str) -> str:
    """SQLAlchemy URLs may name a driver ("postgresql+psycopg://");
    psycopg wants the plain form."""
    return re.sub(r"^postgres(?:ql)?\+\w+://", "postgresql://", url)


def get_checkpointer() -> PostgresSaver:
    global _pool, _saver
    with _lock:
        if _saver is None:
            _pool = ConnectionPool(
                _conninfo(DATABASE_URL),
                max_size=10,
                kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
                open=True,
            )
            _saver = PostgresSaver(_pool)
            _saver.setup()
        return _saver


def close_checkpointer() -> None:
    """Closes the pool. The next get_checkpointer() starts fresh, which is
    also how tests simulate a process restart."""
    global _pool, _saver
    with _lock:
        if _pool is not None:
            _pool.close()
        _pool, _saver = None, None
