from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from enum import IntEnum
from pathlib import Path
from typing import Any

import psycopg2
from flask import g, has_app_context
from psycopg2.extras import Json, RealDictCursor


class CompanyStatus(IntEnum):
    OK = 0
    NO_IMPRESSUM_LINK_FOUND = 1
    PARSING_FAILED = 2
    ADDRESS_INCOMPLETE = 3
    ERROR = 4
    NO_WEBSITE = 5


SCHEMA_SQL_PATH = Path(__file__).resolve().parent / "schema.sql"


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
                        sidecar_path TEXT,
                        processed_file_path TEXT,
                        processed_video_url TEXT,
                        processing_status TEXT NOT NULL DEFAULT 'none',
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                    """
                )
                cur.execute("ALTER TABLE videos ADD COLUMN IF NOT EXISTS sidecar_path TEXT;")
                cur.execute("ALTER TABLE videos ADD COLUMN IF NOT EXISTS processed_file_path TEXT;")
                cur.execute("ALTER TABLE videos ADD COLUMN IF NOT EXISTS processed_video_url TEXT;")
                cur.execute(
                    "ALTER TABLE videos ADD COLUMN IF NOT EXISTS processing_status TEXT NOT NULL DEFAULT 'none';"
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS jobs (
                        id SERIAL PRIMARY KEY,
                        video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                        company_name TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending',
                        input_path TEXT NOT NULL,
                        sidecar_path TEXT,
                        output_path TEXT,
                        processed_video_url TEXT,
                        base_url TEXT,
                        error_message TEXT,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                    """
                )
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
def _managed_db() -> Iterator[psycopg2.extensions.connection]:
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


def save_video(
    company_name: str,
    file_path: str,
    video_url: str,
    sidecar_path: str | None = None,
    processing_status: str = "none",
) -> int:
    with _managed_db() as db:
        with db:
            with db.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO videos (
                        company_name,
                        file_path,
                        video_url,
                        sidecar_path,
                        processed_file_path,
                        processed_video_url,
                        processing_status,
                        updated_at
                    )
                    VALUES (%s, %s, %s, %s, NULL, NULL, %s, NOW())
                    ON CONFLICT (company_name) DO UPDATE SET
                        file_path = EXCLUDED.file_path,
                        video_url = EXCLUDED.video_url,
                        sidecar_path = EXCLUDED.sidecar_path,
                        processed_file_path = NULL,
                        processed_video_url = NULL,
                        processing_status = EXCLUDED.processing_status,
                        updated_at = NOW()
                    RETURNING id;
                    """,
                    (company_name, file_path, video_url, sidecar_path, processing_status),
                )
                row = cur.fetchone()
                return int(row["id"])


def get_video(company_name: str) -> dict[str, Any] | None:
    with _managed_db() as db:
        with db.cursor() as cur:
            cur.execute(
                """
                SELECT
                    v.id,
                    v.company_name,
                    v.video_url,
                    v.file_path,
                    v.sidecar_path,
                    v.processed_file_path,
                    v.processed_video_url,
                    v.processing_status,
                    j.id AS job_id
                FROM videos v
                LEFT JOIN LATERAL (
                    SELECT id FROM jobs WHERE video_id = v.id ORDER BY id DESC LIMIT 1
                ) j ON TRUE
                WHERE v.company_name = %s
                """,
                (company_name,),
            )
            row = cur.fetchone()
    return dict(row) if row else None


def get_video_by_id(video_id: int) -> dict[str, Any] | None:
    with _managed_db() as db:
        with db.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id,
                    company_name,
                    video_url,
                    file_path,
                    sidecar_path,
                    processed_file_path,
                    processed_video_url,
                    processing_status
                FROM videos
                WHERE id = %s
                """,
                (video_id,),
            )
            row = cur.fetchone()
    return dict(row) if row else None


def get_company_by_path(file_path: str) -> str | None:
    with _managed_db() as db:
        with db.cursor() as cur:
            cur.execute(
                "SELECT company_name FROM videos WHERE file_path = %s",
                (file_path,),
            )
            row = cur.fetchone()
    return str(row["company_name"]) if row else None


def get_all_videos() -> list[dict[str, Any]]:
    with _managed_db() as db:
        with db.cursor() as cur:
            cur.execute(
                """
                SELECT
                    v.id,
                    v.company_name,
                    v.file_path,
                    v.video_url,
                    v.sidecar_path,
                    v.processed_file_path,
                    v.processed_video_url,
                    v.processing_status,
                    j.id AS job_id,
                    TO_CHAR(v.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS created_at,
                    TO_CHAR(v.updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS updated_at
                FROM videos v
                LEFT JOIN LATERAL (
                    SELECT id FROM jobs WHERE video_id = v.id ORDER BY id DESC LIMIT 1
                ) j ON TRUE
                ORDER BY v.id
                """
            )
            rows = cur.fetchall()
    return [dict(row) for row in rows]


def create_job(
    video_id: int,
    company_name: str,
    input_path: str,
    sidecar_path: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    with _managed_db() as db:
        with db:
            with db.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO jobs (video_id, company_name, status, input_path, sidecar_path, base_url)
                    VALUES (%s, %s, 'pending', %s, %s, %s)
                    RETURNING
                        id, video_id, company_name, status, input_path, sidecar_path,
                        output_path, processed_video_url, base_url, error_message,
                        TO_CHAR(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS created_at,
                        TO_CHAR(updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS updated_at;
                    """,
                    (video_id, company_name, input_path, sidecar_path, base_url),
                )
                job = dict(cur.fetchone())
                cur.execute(
                    "UPDATE videos SET processing_status = 'pending', updated_at = NOW() WHERE id = %s;",
                    (video_id,),
                )
    return job


def get_job(job_id: int) -> dict[str, Any] | None:
    with _managed_db() as db:
        with db.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id, video_id, company_name, status, input_path, sidecar_path,
                    output_path, processed_video_url, base_url, error_message,
                    TO_CHAR(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS created_at,
                    TO_CHAR(updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS updated_at
                FROM jobs
                WHERE id = %s
                """,
                (job_id,),
            )
            row = cur.fetchone()
    return dict(row) if row else None


def get_all_jobs() -> list[dict[str, Any]]:
    with _managed_db() as db:
        with db.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id, video_id, company_name, status, input_path, sidecar_path,
                    output_path, processed_video_url, base_url, error_message,
                    TO_CHAR(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS created_at,
                    TO_CHAR(updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS updated_at
                FROM jobs
                ORDER BY id DESC
                """
            )
            rows = cur.fetchall()
    return [dict(row) for row in rows]


def update_job_sidecar(job_id: int, sidecar_path: str) -> None:
    with _managed_db() as db:
        with db:
            with db.cursor() as cur:
                cur.execute(
                    "UPDATE jobs SET sidecar_path = %s, updated_at = NOW() WHERE id = %s RETURNING video_id;",
                    (sidecar_path, job_id),
                )
                row = cur.fetchone()
                if row:
                    cur.execute(
                        "UPDATE videos SET sidecar_path = %s, updated_at = NOW() WHERE id = %s;",
                        (sidecar_path, row["video_id"]),
                    )


def claim_job(job_id: int, allow_rerun: bool = False) -> dict[str, Any] | None:
    allowed = ("pending", "failed", "done") if allow_rerun else ("pending", "failed")
    with _managed_db() as db:
        with db:
            with db.cursor() as cur:
                cur.execute(
                    """
                    UPDATE jobs
                    SET status = 'processing', error_message = NULL, updated_at = NOW()
                    WHERE id = %s AND status = ANY(%s)
                    RETURNING
                        id, video_id, company_name, status, input_path, sidecar_path,
                        output_path, processed_video_url, base_url, error_message,
                        TO_CHAR(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS created_at,
                        TO_CHAR(updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS updated_at;
                    """,
                    (job_id, list(allowed)),
                )
                row = cur.fetchone()
                if not row:
                    return None
                job = dict(row)
                cur.execute(
                    "UPDATE videos SET processing_status = 'processing', updated_at = NOW() WHERE id = %s;",
                    (job["video_id"],),
                )
    return job


def complete_job(job_id: int, output_path: str, processed_video_url: str) -> dict[str, Any] | None:
    with _managed_db() as db:
        with db:
            with db.cursor() as cur:
                cur.execute(
                    """
                    UPDATE jobs
                    SET status = 'done',
                        output_path = %s,
                        processed_video_url = %s,
                        error_message = NULL,
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING
                        id, video_id, company_name, status, input_path, sidecar_path,
                        output_path, processed_video_url, base_url, error_message,
                        TO_CHAR(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS created_at,
                        TO_CHAR(updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS updated_at;
                    """,
                    (output_path, processed_video_url, job_id),
                )
                row = cur.fetchone()
                if not row:
                    return None
                job = dict(row)
                cur.execute(
                    """
                    UPDATE videos
                    SET processed_file_path = %s,
                        processed_video_url = %s,
                        processing_status = 'done',
                        updated_at = NOW()
                    WHERE id = %s;
                    """,
                    (output_path, processed_video_url, job["video_id"]),
                )
    return job


def fail_job(job_id: int, error_message: str) -> dict[str, Any] | None:
    with _managed_db() as db:
        with db:
            with db.cursor() as cur:
                cur.execute(
                    """
                    UPDATE jobs
                    SET status = 'failed',
                        error_message = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING
                        id, video_id, company_name, status, input_path, sidecar_path,
                        output_path, processed_video_url, base_url, error_message,
                        TO_CHAR(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS created_at,
                        TO_CHAR(updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS updated_at;
                    """,
                    (error_message, job_id),
                )
                row = cur.fetchone()
                if not row:
                    return None
                job = dict(row)
                cur.execute(
                    "UPDATE videos SET processing_status = 'failed', updated_at = NOW() WHERE id = %s;",
                    (job["video_id"],),
                )
    return job


_COMPANY_SELECT_COLS = """
    id,
    company_name,
    language,
    country,
    postcode,
    city,
    street,
    house_no,
    web,
    status,
    impressum_url,
    legal_name,
    postal_address,
    handelsregister,
    vat_number,
    phone,
    email,
    responsible_person,
    raw_json,
    TO_CHAR(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS created_at,
    TO_CHAR(updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS updated_at
"""


def upsert_companies(rows: list[dict[str, Any]]) -> dict[str, int]:
    imported = 0
    updated = 0
    with _managed_db() as db:
        with db:
            with db.cursor() as cur:
                for row in rows:
                    cur.execute(
                        """
                        INSERT INTO companies (
                            company_name,
                            language,
                            country,
                            postcode,
                            city,
                            street,
                            house_no,
                            web,
                            updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                        ON CONFLICT (company_name) DO UPDATE SET
                            language   = EXCLUDED.language,
                            country    = EXCLUDED.country,
                            postcode   = EXCLUDED.postcode,
                            city       = EXCLUDED.city,
                            street     = EXCLUDED.street,
                            house_no   = EXCLUDED.house_no,
                            web        = EXCLUDED.web,
                            updated_at = NOW()
                        RETURNING (xmax = 0) AS inserted;
                        """,
                        (
                            row["company_name"],
                            row.get("language"),
                            row.get("country"),
                            row.get("postcode"),
                            row.get("city"),
                            row.get("street"),
                            row.get("house_no"),
                            row.get("web"),
                        ),
                    )
                    res = cur.fetchone()
                    if res and res["inserted"]:
                        imported += 1
                    else:
                        updated += 1
    return {
        "imported": imported,
        "updated": updated,
        "total_rows": imported + updated,
    }


def get_all_companies(status: int | None = None) -> list[dict[str, Any]]:
    with _managed_db() as db:
        with db.cursor() as cur:
            if status is not None:
                cur.execute(
                    f"SELECT {_COMPANY_SELECT_COLS} FROM companies WHERE status = %s ORDER BY id",
                    (status,),
                )
            else:
                cur.execute(f"SELECT {_COMPANY_SELECT_COLS} FROM companies ORDER BY id")
            rows = cur.fetchall()
    return [dict(row) for row in rows]


def get_company(company_name: str) -> dict[str, Any] | None:
    with _managed_db() as db:
        with db.cursor() as cur:
            cur.execute(
                f"SELECT {_COMPANY_SELECT_COLS} FROM companies WHERE company_name = %s",
                (company_name,),
            )
            row = cur.fetchone()
    return dict(row) if row else None


def get_companies_for_enrichment(
    company_name: str | None = None,
    status: int | str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    if company_name:
        clauses.append("company_name = %s")
        params.append(company_name)
    elif status == "null":
        clauses.append("status IS NULL")
    elif status is not None:
        clauses.append("status = %s")
        params.append(int(status))
    else:
        clauses.append("(status IS NULL OR status <> %s)")
        params.append(int(CompanyStatus.OK))

    sql = f"SELECT {_COMPANY_SELECT_COLS} FROM companies"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY id"
    if limit is not None:
        sql += " LIMIT %s"
        params.append(int(limit))

    with _managed_db() as db:
        with db.cursor() as cur:
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
    return [dict(row) for row in rows]


def update_company_status(
    company_id: int,
    status: CompanyStatus | int,
    impressum_url: str | None = None,
) -> None:
    with _managed_db() as db:
        with db:
            with db.cursor() as cur:
                if impressum_url is not None:
                    cur.execute(
                        """
                        UPDATE companies
                        SET status = %s,
                            impressum_url = %s,
                            updated_at = NOW()
                        WHERE id = %s;
                        """,
                        (int(status), impressum_url, company_id),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE companies
                        SET status = %s,
                            updated_at = NOW()
                        WHERE id = %s;
                        """,
                        (int(status), company_id),
                    )


def update_company_enrichment(
    company_id: int,
    status: CompanyStatus | int,
    impressum_url: str | None,
    legal_name: str | None,
    postal_address: str | None,
    handelsregister: str | None,
    vat_number: str | None,
    phone: str | None,
    email: str | None,
    responsible_person: str | None,
    raw_json: dict[str, Any] | None,
) -> None:
    with _managed_db() as db:
        with db:
            with db.cursor() as cur:
                cur.execute(
                    """
                    UPDATE companies
                    SET status = %s,
                        impressum_url = %s,
                        legal_name = %s,
                        postal_address = %s,
                        handelsregister = %s,
                        vat_number = %s,
                        phone = %s,
                        email = %s,
                        responsible_person = %s,
                        raw_json = %s,
                        updated_at = NOW()
                    WHERE id = %s;
                    """,
                    (
                        int(status),
                        impressum_url,
                        legal_name,
                        postal_address,
                        handelsregister,
                        vat_number,
                        phone,
                        email,
                        responsible_person,
                        Json(raw_json) if raw_json is not None else None,
                        company_id,
                    ),
                )
