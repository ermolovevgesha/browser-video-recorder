from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg2
from flask import g, has_app_context
from psycopg2.extras import RealDictCursor

SCHEMA_SQL_PATH = Path(__file__).resolve().parent.parent / "schema.sql"


def get_db_params() -> dict[str, Any]:
    return {
        "host": os.environ["DB_HOST"],
        "port": int(os.environ["DB_PORT"]),
        "dbname": os.environ["DB_NAME"],
        "user": os.environ["DB_USER"],
        "password": os.environ["DB_PASSWORD"],
    }


def init_db() -> None:
    conn = psycopg2.connect(**get_db_params())
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(SCHEMA_SQL_PATH.read_text(encoding="utf-8"))
    finally:
        conn.close()


def get_db() -> psycopg2.extensions.connection:
    if has_app_context():
        if "db" not in g:
            g.db = psycopg2.connect(**get_db_params(), cursor_factory=RealDictCursor)
        return g.db

    return psycopg2.connect(**get_db_params(), cursor_factory=RealDictCursor)


@contextmanager
def managed_db() -> Iterator[psycopg2.extensions.connection]:
    if has_app_context():
        yield get_db()
        return

    conn = psycopg2.connect(**get_db_params(), cursor_factory=RealDictCursor)
    try:
        yield conn
    finally:
        if not conn.closed:
            conn.close()


def close_db(exception: BaseException | None = None) -> None:
    db = g.pop("db", None)
    if db is not None and not db.closed:
        db.close()
