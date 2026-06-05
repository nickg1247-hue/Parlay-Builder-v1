from contextlib import asynccontextmanager
from datetime import date as date_type
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.db.database import get_connection, init_db
from app.models.constants import DEFAULT_MIN_EDGE
from app.parlay.ev_ranker import DEFAULT_MAX_PARLAYS
from app.services.daily_board import build_daily_board
from app.services.backtest_report import load_saved_backtest_report, run_backtest_report
from app.services.model_lab import (
    confirm_locked_test,
    get_lab_meta,
    get_run,
    list_runs,
    run_experiment,
    run_until_within_goal,
)
from app.db.market_status import get_market_eval_status
from app.db.parlay_status import get_parlay_status
from app.db.mlb_status import get_mlb_data_status
from app.db.totals_status import get_totals_model_status

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


class LabRunRequest(BaseModel):
    experiment_id: str = Field(..., min_length=1, max_length=64)
    track: str = Field(..., pattern="^(moneyline|totals)$")
    feature_set: str = Field(..., min_length=1)
    goal_metric: str = Field("log_loss_model")
    goal_value: float = Field(...)
    until_within_pct: float | None = Field(
        0.05,
        ge=0.0,
        le=0.5,
        description="Try feature sets until within this fraction of goal (0.05 = 5%). "
        "Set 0 for a single run only.",
    )


class LabConfirmRequest(BaseModel):
    run_id: str = Field(..., min_length=1)
    promote: bool = False


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
    min_edge: float = Query(
        DEFAULT_MIN_EDGE,
        ge=0.0,
        le=0.5,
        description="Minimum edge/EV for singles, parlays, and totals flags.",
    ),
    max_parlays: int = Query(
        DEFAULT_MAX_PARLAYS,
        ge=1,
        le=20,
        description="Maximum ranked parlays to return.",
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
        min_edge=min_edge,
        max_parlays=max_parlays,
    )


@app.get("/")
async def home():
    return FileResponse(STATIC_DIR / "home.html")


@app.get("/mlb")
async def mlb_board():
    return FileResponse(STATIC_DIR / "mlb.html")


@app.get("/mlb/lab")
async def mlb_lab():
    return FileResponse(STATIC_DIR / "mlb_lab.html")


@app.get("/api/lab/meta")
async def lab_meta():
    return get_lab_meta()


@app.post("/api/lab/run")
async def lab_run(body: LabRunRequest):
    try:
        if body.until_within_pct and body.until_within_pct > 0:
            return run_until_within_goal(
                experiment_id=body.experiment_id,
                track=body.track,
                start_feature_set=body.feature_set,
                goal_metric=body.goal_metric,
                goal_value=body.goal_value,
                until_within_pct=body.until_within_pct,
            )
        return run_experiment(
            experiment_id=body.experiment_id,
            track=body.track,
            feature_set=body.feature_set,
            goal_metric=body.goal_metric,
            goal_value=body.goal_value,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Lab run failed: {exc}",
        ) from exc


@app.get("/api/lab/runs")
async def lab_runs():
    return {"runs": list_runs()}


@app.get("/api/lab/runs/{run_id}")
async def lab_run_detail(run_id: str):
    run = get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@app.post("/api/lab/confirm-test")
async def lab_confirm_test(body: LabConfirmRequest):
    try:
        return confirm_locked_test(body.run_id, promote=body.promote)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Confirm failed: {exc}",
        ) from exc
