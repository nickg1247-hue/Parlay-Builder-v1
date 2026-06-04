MLB_GAMES_COLUMNS = [
    "game_id",
    "date",
    "home_team",
    "away_team",
    "home_score",
    "away_score",
    "home_win",
    "home_starting_pitcher",
    "away_starting_pitcher",
    "home_pitcher_era",
    "home_pitcher_fip",
    "away_pitcher_era",
    "away_pitcher_fip",
    "home_last10_win_pct",
    "away_last10_win_pct",
    "home_last10_run_diff",
    "away_last10_run_diff",
    "home_rest_days",
    "away_rest_days",
]

CREATE_MLB_GAMES_SQL = """
CREATE TABLE IF NOT EXISTS mlb_games (
    game_id TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    home_score INTEGER NOT NULL,
    away_score INTEGER NOT NULL,
    home_win INTEGER NOT NULL,
    home_starting_pitcher TEXT,
    away_starting_pitcher TEXT,
    home_pitcher_era REAL,
    home_pitcher_fip REAL,
    away_pitcher_era REAL,
    away_pitcher_fip REAL,
    home_last10_win_pct REAL,
    away_last10_win_pct REAL,
    home_last10_run_diff REAL,
    away_last10_run_diff REAL,
    home_rest_days INTEGER,
    away_rest_days INTEGER
);
CREATE INDEX IF NOT EXISTS idx_mlb_games_date ON mlb_games(date);
"""


def ensure_mlb_games_table(conn) -> None:
    conn.executescript(CREATE_MLB_GAMES_SQL)
    conn.commit()
