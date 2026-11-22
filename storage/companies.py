from __future__ import annotations

from enum import IntEnum
from typing import Any

from psycopg2.extras import Json

from storage.connection import managed_db


class CompanyStatus(IntEnum):
    OK = 0
    NO_IMPRESSUM_LINK_FOUND = 1
    PARSING_FAILED = 2
    ADDRESS_INCOMPLETE = 3
    ERROR = 4
    NO_WEBSITE = 5


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
    with managed_db() as db:
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
    with managed_db() as db:
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
    with managed_db() as db:
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

    with managed_db() as db:
        with db.cursor() as cur:
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
    return [dict(row) for row in rows]


def update_company_status(
    company_id: int,
    status: CompanyStatus | int,
    impressum_url: str | None = None,
) -> None:
    with managed_db() as db:
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
    with managed_db() as db:
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
