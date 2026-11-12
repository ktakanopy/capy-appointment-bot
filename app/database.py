from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row


def psycopg_url(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


@contextmanager
def connect(database_url: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(psycopg_url(database_url), row_factory=dict_row) as connection:
        yield connection


def database_is_ready(database_url: str) -> bool:
    try:
        with connect(database_url) as connection:
            connection.execute("SELECT 1")
        return True
    except Exception:
        return False
