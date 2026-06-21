import os
import sqlite3
from pathlib import Path

from app.config import PROJECT_ROOT, settings


def _database_url() -> str:
    return os.getenv("DATABASE_URL", settings.database_url)


def get_sqlite_path() -> Path:
    url = _database_url()
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
    """Ensure SQLite file exists and sport tables are present."""
    from app.db.cfb_schema import ensure_cfb_games_table
    from app.db.nba_schema import ensure_nba_games_table
    from app.db.user_schema import ensure_users_table
    from app.services.user_teams import ensure_user_team_tables
    from app.services.user_players import ensure_user_player_tables

    conn = get_connection()
    try:
        conn.execute("SELECT 1")
        ensure_nba_games_table(conn)
        ensure_cfb_games_table(conn)
        ensure_users_table(conn)
        ensure_user_team_tables(conn)
        ensure_user_player_tables(conn)
    finally:
        conn.close()
