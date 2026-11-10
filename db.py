from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from flask import g, has_app_context

BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "videos.db"


def init_db() -> None:
    with sqlite3.connect(DATABASE_PATH, timeout=10.0) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_name TEXT NOT NULL UNIQUE,
                file_path TEXT NOT NULL,
                video_url TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """
        )
        conn.commit()


def get_db() -> sqlite3.Connection:
    if has_app_context():
        if "db" not in g:
            g.db = sqlite3.connect(DATABASE_PATH, timeout=10.0)
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA journal_mode=WAL;")
            g.db.execute("PRAGMA busy_timeout=5000;")
        return g.db

    conn = sqlite3.connect(DATABASE_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


def close_db(exception: BaseException | None = None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def save_video(company_name: str, file_path: str, video_url: str) -> None:
    db = get_db()
    with db:
        db.execute(
            """
            INSERT INTO videos (company_name, file_path, video_url, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(company_name) DO UPDATE SET
                file_path = excluded.file_path,
                video_url = excluded.video_url,
                updated_at = datetime('now');
            """,
            (company_name, file_path, video_url),
        )


def get_video(company_name: str) -> dict[str, Any] | None:
    db = get_db()
    cursor = db.execute(
        "SELECT company_name, video_url, file_path FROM videos WHERE company_name = ?",
        (company_name,),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def get_company_by_path(file_path: str) -> str | None:
    db = get_db()
    cursor = db.execute(
        "SELECT company_name FROM videos WHERE file_path = ?",
        (file_path,),
    )
    row = cursor.fetchone()
    return str(row["company_name"]) if row else None


def get_all_videos() -> list[dict[str, Any]]:
    db = get_db()
    cursor = db.execute(
        "SELECT id, company_name, file_path, video_url, created_at, updated_at FROM videos ORDER BY id"
    )
    return [dict(row) for row in cursor.fetchall()]
