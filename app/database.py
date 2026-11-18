from __future__ import annotations

import atexit
import os
from contextlib import contextmanager
from threading import Lock
from typing import Iterator

import psycopg
from psycopg.rows import dict_row

_pools = {}
_pools_lock = Lock()


def psycopg_url(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


@contextmanager
def connect(database_url: str) -> Iterator[psycopg.Connection]:
    maximum = int(os.getenv("CAPY_DB_POOL_MAX", "0"))
    if not 0 <= maximum <= 64:
        raise ValueError("CAPY_DB_POOL_MAX must be between 0 and 64.")
    url = psycopg_url(database_url)
    if not maximum:
        with psycopg.connect(url, row_factory=dict_row, connect_timeout=3) as connection:
            yield connection
        return
    with _pools_lock:
        pool = _pools.get((url, maximum))
        if pool is None:
            from psycopg_pool import ConnectionPool

            pool = ConnectionPool(
                url, min_size=0, max_size=maximum, timeout=3,
                kwargs={"row_factory": dict_row, "connect_timeout": 3}, open=True,
            )
            _pools[(url, maximum)] = pool
            atexit.register(pool.close)
    with pool.connection() as connection:
        yield connection


def database_is_ready(database_url: str) -> bool:
    try:
        with connect(database_url) as connection:
            connection.execute("SELECT 1")
        return True
    except Exception:
        return False
