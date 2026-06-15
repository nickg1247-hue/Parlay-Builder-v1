CFB_GAMES_COLUMNS = [
    "game_id",
    "date",
    "season",
    "game_type",
    "home_team",
    "away_team",
    "home_score",
    "away_score",
    "home_win",
    "home_rest_days",
    "away_rest_days",
    "home_b2b",
    "away_b2b",
    "neutral_site",
    "conference_game",
    "home_conference",
    "away_conference",
    "week",
]

_CREATE_CFB_GAMES_BASE = """
CREATE TABLE IF NOT EXISTS cfb_games (
    game_id TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    season INTEGER NOT NULL,
    game_type TEXT NOT NULL,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    home_score INTEGER NOT NULL,
    away_score INTEGER NOT NULL,
    home_win INTEGER NOT NULL,
    home_rest_days REAL NOT NULL,
    away_rest_days REAL NOT NULL,
    home_b2b INTEGER NOT NULL,
    away_b2b INTEGER NOT NULL,
    neutral_site INTEGER NOT NULL DEFAULT 0,
    conference_game INTEGER NOT NULL DEFAULT 0,
    home_conference TEXT NOT NULL DEFAULT '',
    away_conference TEXT NOT NULL DEFAULT '',
    week INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_cfb_games_date ON cfb_games(date);
CREATE INDEX IF NOT EXISTS idx_cfb_games_season ON cfb_games(season);
"""

_CFB_MIGRATION_COLUMNS: tuple[tuple[str, str], ...] = (
    ("neutral_site", "INTEGER NOT NULL DEFAULT 0"),
    ("conference_game", "INTEGER NOT NULL DEFAULT 0"),
    ("home_conference", "TEXT NOT NULL DEFAULT ''"),
    ("away_conference", "TEXT NOT NULL DEFAULT ''"),
    ("week", "INTEGER NOT NULL DEFAULT 0"),
)

CREATE_CFB_GAMES_SQL = _CREATE_CFB_GAMES_BASE


def ensure_cfb_games_table(conn) -> None:
    conn.executescript(_CREATE_CFB_GAMES_BASE)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(cfb_games)")}
    for col, ddl in _CFB_MIGRATION_COLUMNS:
        if col not in existing:
            conn.execute(f"ALTER TABLE cfb_games ADD COLUMN {col} {ddl}")
    conn.commit()
