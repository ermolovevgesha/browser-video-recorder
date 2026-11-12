from __future__ import annotations

import os
from typing import Any

import psycopg2
from flask import g, has_app_context
from psycopg2.extras import RealDictCursor


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
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS videos (
                        id SERIAL PRIMARY KEY,
                        company_name TEXT NOT NULL UNIQUE,
                        file_path TEXT NOT NULL,
                        video_url TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                    """
                )
    finally:
        conn.close()


def get_db() -> psycopg2.extensions.connection:
    if has_app_context():
        if "db" not in g:
            g.db = psycopg2.connect(**get_db_params(), cursor_factory=RealDictCursor)
        return g.db

    return psycopg2.connect(**get_db_params(), cursor_factory=RealDictCursor)


def close_db(exception: BaseException | None = None) -> None:
    db = g.pop("db", None)
    if db is not None and not db.closed:
        db.close()


def save_video(company_name: str, file_path: str, video_url: str) -> None:
    db = get_db()
    with db:
        with db.cursor() as cur:
            cur.execute(
                """
                INSERT INTO videos (company_name, file_path, video_url, updated_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (company_name) DO UPDATE SET
                    file_path = EXCLUDED.file_path,
                    video_url = EXCLUDED.video_url,
                    updated_at = NOW();
                """,
                (company_name, file_path, video_url),
            )


def get_video(company_name: str) -> dict[str, Any] | None:
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "SELECT company_name, video_url, file_path FROM videos WHERE company_name = %s",
            (company_name,),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def get_company_by_path(file_path: str) -> str | None:
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "SELECT company_name FROM videos WHERE file_path = %s",
            (file_path,),
        )
        row = cur.fetchone()
    return str(row["company_name"]) if row else None


def get_all_videos() -> list[dict[str, Any]]:
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT
                id,
                company_name,
                file_path,
                video_url,
                TO_CHAR(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS created_at,
                TO_CHAR(updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS updated_at
            FROM videos
            ORDER BY id
            """
        )
        rows = cur.fetchall()
    return [dict(row) for row in rows]
