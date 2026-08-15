NFL_GAMES_COLUMNS = [
    "game_id",
    "date",
    "season",
    "week",
    "game_type",
    "home_team",
    "away_team",
    "home_team_id",
    "away_team_id",
    "home_team_abbr",
    "away_team_abbr",
    "home_score",
    "away_score",
    "home_win",
    "home_rest_days",
    "away_rest_days",
    "divisional",
    "neutral_site",
    "espn_home_ml",
    "espn_away_ml",
    "espn_spread",
    "espn_ou",
]

CREATE_NFL_GAMES_SQL = """
CREATE TABLE IF NOT EXISTS nfl_games (
    game_id TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    season INTEGER NOT NULL,
    week INTEGER NOT NULL DEFAULT 0,
    game_type TEXT NOT NULL,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    home_team_id TEXT NOT NULL DEFAULT '',
    away_team_id TEXT NOT NULL DEFAULT '',
    home_team_abbr TEXT NOT NULL DEFAULT '',
    away_team_abbr TEXT NOT NULL DEFAULT '',
    home_score INTEGER NOT NULL,
    away_score INTEGER NOT NULL,
    home_win INTEGER NOT NULL,
    home_rest_days REAL NOT NULL,
    away_rest_days REAL NOT NULL,
    divisional INTEGER NOT NULL DEFAULT 0,
    neutral_site INTEGER NOT NULL DEFAULT 0,
    espn_home_ml INTEGER,
    espn_away_ml INTEGER,
    espn_spread REAL,
    espn_ou REAL
);
CREATE INDEX IF NOT EXISTS idx_nfl_games_date ON nfl_games(date);
CREATE INDEX IF NOT EXISTS idx_nfl_games_season ON nfl_games(season);
"""


_NFL_MIGRATION_COLUMNS: tuple[tuple[str, str], ...] = (
    ("espn_home_ml", "INTEGER"),
    ("espn_away_ml", "INTEGER"),
    ("espn_spread", "REAL"),
    ("espn_ou", "REAL"),
)


def ensure_nfl_games_table(conn) -> None:
    conn.executescript(CREATE_NFL_GAMES_SQL)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(nfl_games)")}
    for col, ddl in _NFL_MIGRATION_COLUMNS:
        if col not in existing:
            conn.execute(f"ALTER TABLE nfl_games ADD COLUMN {col} {ddl}")
    conn.commit()
