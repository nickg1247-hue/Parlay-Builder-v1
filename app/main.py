from contextlib import asynccontextmanager
from datetime import date as date_type
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.db.database import get_connection, init_db
from app.services.daily_board import build_daily_board
from app.services.backtest_report import load_saved_backtest_report, run_backtest_report
from app.db.market_status import get_market_eval_status
from app.db.parlay_status import get_parlay_status
from app.db.mlb_status import get_mlb_data_status
from app.db.totals_status import get_totals_model_status

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    try:
        import pandas as pd

        from app.features.mlb_pregame import get_team_tracker_before
        from app.features.mlb_totals_pregame import get_runs_tracker_before

        today = pd.Timestamp(date_type.today())
        get_team_tracker_before(today)
        get_runs_tracker_before(today)
    except Exception:
        pass
    yield


app = FastAPI(title="Parlay Builder v1", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/health")
async def health():
    conn = get_connection()
    try:
        data_status = get_mlb_data_status(conn)
    finally:
        conn.close()
    return {
        "status": "ok",
        "sport": "mlb",
        "phase": "5",
        **data_status,
        **get_market_eval_status(),
        **get_parlay_status(),
        **get_totals_model_status(),
    }


@app.get("/api/backtest")
async def backtest_report(days: int = Query(30, ge=1, le=120)):
    return run_backtest_report(days, write_cache=True)


@app.get("/api/backtest/saved")
async def backtest_saved():
    return load_saved_backtest_report()


@app.get("/api/daily")
async def daily_board(
    date_param: str | None = Query(None, alias="date"),
    use_cache: bool = Query(False),
    refresh: bool = Query(False),
    skip_totals: bool | None = Query(
        None,
        description="Skip totals model (default: true for live, false for demo cache).",
    ),
):
    game_date = (
        date_type.fromisoformat(date_param) if date_param else date_type.today()
    )
    return build_daily_board(
        game_date=game_date,
        use_cache=use_cache,
        refresh=refresh,
        skip_totals=skip_totals,
    )


@app.get("/")
async def home():
    return FileResponse(STATIC_DIR / "home.html")


@app.get("/mlb")
async def mlb_board():
    return FileResponse(STATIC_DIR / "mlb.html")
