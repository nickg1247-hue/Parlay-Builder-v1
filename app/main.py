import asyncio
import logging
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
from app.services.forward_clv import summarize_clv as summarize_mlb_clv
from app.services.nba_daily_board import build_nba_daily_board
from app.odds.odds_repository import get_today_snapshot
from app.odds.live_odds import live_odds_enabled
from app.services.morning_refresh import get_refresh_status
from app.services.odds_hourly_refresh import hourly_refresh_enabled, run_hourly_odds_refresh
from app.services.game_insights import build_game_insights
from app.services.home_summary import get_home_today_summary
from app.services.news_feed import get_news_headlines
from app.services.schedule_mlb import get_mlb_game, get_mlb_schedule
from app.services.schedule_nba import get_nba_game, get_nba_schedule
from app.services.scores_today import get_scores_today
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


logger = logging.getLogger(__name__)


async def _hourly_odds_loop() -> None:
    while True:
        await asyncio.sleep(3600)
        try:
            run_hourly_odds_refresh()
        except Exception as exc:
            logger.warning("In-app hourly odds refresh error: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    hourly_task: asyncio.Task | None = None
    try:
        import pandas as pd

        from app.features.mlb_pregame import get_team_tracker_before
        from app.features.mlb_totals_pregame import get_runs_tracker_before

        today = pd.Timestamp(date_type.today())
        get_team_tracker_before(today)
        get_runs_tracker_before(today)
    except Exception:
        pass
    if hourly_refresh_enabled() and live_odds_enabled():
        hourly_task = asyncio.create_task(_hourly_odds_loop())
        logger.info("Odds hourly refresh scheduler started (3600s)")
    yield
    if hourly_task is not None:
        hourly_task.cancel()
        try:
            await hourly_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="NTG Sports", lifespan=lifespan)
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


@app.get("/api/clv/summary")
async def clv_summary(
    days: int = Query(30, ge=1, le=365),
    sport: str = Query("mlb", pattern="^(mlb|nba)$"),
):
    if sport == "nba":
        from app.services.nba_forward_clv import summarize_clv as summarize_nba_clv

        return summarize_nba_clv(days=days)
    return summarize_mlb_clv(days=days)


@app.get("/api/nba/daily")
async def nba_daily(
    date_param: str | None = Query(None, alias="date"),
    min_edge: float = Query(DEFAULT_MIN_EDGE, ge=0.0, le=0.5),
    refresh: bool = Query(False, description="Force refresh live NBA odds from API"),
    use_cache: bool = Query(
        False,
        description="Demo mode — load odds from CSV/repository only (no Odds API)",
    ),
    log_clv: bool = Query(True, description="Append +EV singles to forward CLV log"),
):
    game_date = (
        date_type.fromisoformat(date_param) if date_param else date_type.today()
    )
    return build_nba_daily_board(
        game_date=game_date,
        min_edge=min_edge,
        force_refresh=refresh and not use_cache,
        use_cache=use_cache,
        log_clv=log_clv,
    )


@app.get("/api/status/refresh")
async def refresh_status():
    return get_refresh_status()


@app.get("/api/news")
async def news_headlines(refresh: bool = Query(False)):
    """RSS sports headlines (15 min cache)."""
    return get_news_headlines(force_refresh=refresh)


@app.get("/api/home/today")
async def home_today(
    date_param: str | None = Query(None, alias="date"),
):
    """Today at a glance + best bets from cached daily board (no rebuild)."""
    game_date = (
        date_type.fromisoformat(date_param) if date_param else date_type.today()
    )
    return get_home_today_summary(game_date)


@app.get("/api/odds/today")
async def odds_today():
    """Today's repository snapshot + quota counters (no API call)."""
    return get_today_snapshot()


@app.get("/api/schedule/mlb")
async def mlb_schedule(
    date_param: str | None = Query(None, alias="date"),
):
    game_date = (
        date_type.fromisoformat(date_param) if date_param else date_type.today()
    )
    return get_mlb_schedule(game_date)


@app.get("/api/schedule/nba")
async def nba_schedule(
    date_param: str | None = Query(None, alias="date"),
):
    if date_param:
        game_date = date_type.fromisoformat(date_param)
        return get_nba_schedule(game_date, auto_resolve=False)
    return get_nba_schedule(None, auto_resolve=True)


@app.get("/api/scores/today")
async def scores_today(
    sport: str = Query("mlb", pattern="^(mlb|nba|all)$"),
    date_param: str | None = Query(None, alias="date"),
):
    game_date = date_type.fromisoformat(date_param) if date_param else None
    auto_resolve = date_param is None and sport in ("nba", "all")
    return get_scores_today(sport=sport, game_date=game_date, auto_resolve=auto_resolve)


@app.get("/api/games/mlb/{game_id}")
async def mlb_game_detail(
    game_id: str,
    date_param: str | None = Query(None, alias="date"),
):
    game_date = (
        date_type.fromisoformat(date_param) if date_param else date_type.today()
    )
    detail = get_mlb_game(game_id, game_date)
    if detail is None:
        raise HTTPException(status_code=404, detail="Game not found")
    return detail


@app.get("/api/games/mlb/{game_id}/insights")
async def mlb_game_insights(
    game_id: str,
    date_param: str | None = Query(None, alias="date"),
    use_cache: bool = Query(False),
    refresh: bool = Query(False),
):
    game_date = (
        date_type.fromisoformat(date_param) if date_param else date_type.today()
    )
    insights = build_game_insights(
        game_id,
        game_date=game_date,
        use_cache=use_cache,
        refresh=refresh,
    )
    if insights is None:
        raise HTTPException(status_code=404, detail="Game not found")
    return insights


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
    live_test: bool = Query(
        False,
        description=(
            "Live board bypass: force odds refresh, full totals board, "
            "and sync repository + daily_board for main-site game pages."
        ),
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
        live_test=live_test,
    )


@app.get("/")
async def home():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/mlb")
async def mlb_slate():
    return FileResponse(STATIC_DIR / "mlb_slate.html")


@app.get("/nba")
async def nba_slate():
    return FileResponse(STATIC_DIR / "nba_slate.html")


@app.get("/nba/game/{game_id}")
async def nba_game_page(game_id: str):
    return FileResponse(STATIC_DIR / "nba_game.html")


@app.get("/api/games/nba/{game_id}")
async def nba_game_detail(
    game_id: str,
    date_param: str | None = Query(None, alias="date"),
):
    game_date = date_type.fromisoformat(date_param) if date_param else None
    detail = get_nba_game(game_id, game_date)
    if detail is None:
        raise HTTPException(status_code=404, detail="Game not found")
    return detail


@app.get("/api/games/nba/{game_id}/insights")
async def nba_game_insights(
    game_id: str,
    date_param: str | None = Query(None, alias="date"),
    use_cache: bool = Query(False),
    refresh: bool = Query(False),
):
    from app.services.nba_game_insights import build_nba_game_insights

    game_date = (
        date_type.fromisoformat(date_param) if date_param else date_type.today()
    )
    insights = build_nba_game_insights(
        game_id,
        game_date=game_date,
        use_cache=use_cache,
        refresh=refresh,
    )
    if insights is None:
        raise HTTPException(status_code=404, detail="Game not found")
    return insights


@app.get("/mlb/game/{game_id}")
async def mlb_game_page(game_id: str):
    return FileResponse(STATIC_DIR / "game.html")


@app.get("/nba/board")
async def nba_board():
    return FileResponse(STATIC_DIR / "nba.html")


@app.get("/mlb/board")
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
