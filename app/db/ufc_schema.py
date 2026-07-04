UFC_FIGHTS_COLUMNS = [
    "fight_id",
    "event_id",
    "event_name",
    "date",
    "season",
    "home_team",
    "away_team",
    "home_win",
    "weight_class",
    "card_segment",
    "home_rest_days",
    "away_rest_days",
    "home_b2b",
    "away_b2b",
]

_CREATE_UFC_FIGHTS = """
CREATE TABLE IF NOT EXISTS ufc_fights (
    fight_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    event_name TEXT NOT NULL,
    date TEXT NOT NULL,
    season INTEGER NOT NULL,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    home_win INTEGER NOT NULL,
    weight_class TEXT NOT NULL DEFAULT '',
    card_segment TEXT NOT NULL DEFAULT '',
    home_rest_days REAL NOT NULL,
    away_rest_days REAL NOT NULL,
    home_b2b INTEGER NOT NULL,
    away_b2b INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ufc_fights_date ON ufc_fights(date);
CREATE INDEX IF NOT EXISTS idx_ufc_fights_season ON ufc_fights(season);
"""

CREATE_UFC_FIGHTS_SQL = _CREATE_UFC_FIGHTS


def ensure_ufc_fights_table(conn) -> None:
    conn.executescript(_CREATE_UFC_FIGHTS)
    conn.commit()
