from contextlib import asynccontextmanager
from datetime import date as date_type
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.db.database import get_connection, init_db
from app.services.daily_board import build_daily_board
from app.db.market_status import get_market_eval_status
from app.db.parlay_status import get_parlay_status
from app.db.mlb_status import get_mlb_data_status
from app.db.totals_status import get_totals_model_status

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
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


@app.get("/api/daily")
async def daily_board(
    date_param: str | None = Query(None, alias="date"),
    use_cache: bool = Query(False),
    refresh: bool = Query(False),
):
    game_date = (
        date_type.fromisoformat(date_param) if date_param else date_type.today()
    )
    return build_daily_board(
        game_date=game_date,
        use_cache=use_cache,
        refresh=refresh,
    )


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")
