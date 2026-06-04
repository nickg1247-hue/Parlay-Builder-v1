import sqlite3
from typing import Any


def get_mlb_data_status(conn: sqlite3.Connection) -> dict[str, Any]:
    try:
        row = conn.execute(
            "SELECT COUNT(*), MIN(date), MAX(date) FROM mlb_games"
        ).fetchone()
    except sqlite3.OperationalError:
        return {"mlb_games_count": 0, "mlb_date_range": None}

    count, min_date, max_date = row
    if not count:
        return {"mlb_games_count": 0, "mlb_date_range": None}
    return {
        "mlb_games_count": count,
        "mlb_date_range": f"{min_date}..{max_date}",
    }
