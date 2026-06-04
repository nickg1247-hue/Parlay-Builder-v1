import sqlite3
from pathlib import Path

from app.config import PROJECT_ROOT, settings


def get_sqlite_path() -> Path:
    url = settings.database_url
    if not url.startswith("sqlite:///"):
        raise ValueError(f"Unsupported database URL: {url}")
    path_str = url.removeprefix("sqlite:///")
    path = Path(path_str)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def get_connection() -> sqlite3.Connection:
    db_path = get_sqlite_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(db_path)


def init_db() -> None:
    """Ensure SQLite file exists; schema added in later phases."""
    conn = get_connection()
    try:
        conn.execute("SELECT 1")
    finally:
        conn.close()
