from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.db.database import get_connection, init_db
from app.db.market_status import get_market_eval_status
from app.db.mlb_status import get_mlb_data_status

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
        "phase": "3",
        **data_status,
        **get_market_eval_status(),
    }


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")
