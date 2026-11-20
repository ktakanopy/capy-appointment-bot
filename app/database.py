from __future__ import annotations

import atexit
import os
from contextlib import contextmanager
from threading import Lock
from typing import Iterator

from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, TimeoutError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

_engines_lock = Lock()
_engines: dict[tuple[str, int], Engine] = {}
DATABASE_ERRORS = (DBAPIError, TimeoutError)


def psycopg_url(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def _engine(database_url: str) -> Engine:
    maximum = int(os.getenv("CAPY_DB_POOL_MAX", "0"))
    if not 0 <= maximum <= 64:
        raise ValueError("CAPY_DB_POOL_MAX must be between 0 and 64.")
    url = psycopg_url(database_url).replace("postgresql://", "postgresql+psycopg://", 1)
    key = (url, maximum)
    with _engines_lock:
        if key not in _engines:
            pooling = (
                {"pool_size": maximum, "max_overflow": 0, "pool_timeout": 3}
                if maximum else {"poolclass": NullPool}
            )
            engine = create_engine(
                url, connect_args={"connect_timeout": 3}, hide_parameters=True, **pooling,
            )
            _engines[key] = engine
            atexit.register(engine.dispose)
    return _engines[key]


@contextmanager
def database_session(database_url: str) -> Iterator[Session]:
    """Give each operation its own session so concurrent workers never share a transaction."""
    with Session(_engine(database_url)) as session, session.begin():
        yield session


def database_is_ready(database_url: str) -> bool:
    try:
        with database_session(database_url) as session:
            session.scalar(select(1))
        return True
    except Exception:
        return False
