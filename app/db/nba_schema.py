NBA_GAMES_COLUMNS = [
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
]

CREATE_NBA_GAMES_SQL = """
CREATE TABLE IF NOT EXISTS nba_games (
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
    away_b2b INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nba_games_date ON nba_games(date);
CREATE INDEX IF NOT EXISTS idx_nba_games_season ON nba_games(season);
"""


def ensure_nba_games_table(conn) -> None:
    conn.executescript(CREATE_NBA_GAMES_SQL)
    conn.commit()
