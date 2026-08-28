"""Train/evaluate the separate FCS model. Never reads or writes FBS artifacts."""
from pathlib import Path
import pandas as pd
from app.config import PROJECT_ROOT
from app.models.fcs_baseline import train_separate

DATA=PROJECT_ROOT/"data/processed/fcs_games.parquet"
if __name__=="__main__":
    if not DATA.exists(): raise SystemExit("Missing authoritative FCS history: data/processed/fcs_games.parquet")
    games=pd.read_parquet(DATA)
    print(train_separate(games))
