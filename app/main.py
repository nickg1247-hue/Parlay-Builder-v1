import app.config  # noqa: F401 — load .env before auth middleware reads env vars
from app.config import PROJECT_ROOT

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import date as date_type
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.auth.admin_auth import (
    AdminAuthMiddleware,
    auth_enabled,
    auth_misconfigured,
    clear_session_cookie,
    is_authenticated,
    set_session_cookie,
    verify_credentials,
)
from app.db.database import get_connection, init_db
from app.models.constants import DEFAULT_MIN_EDGE
from app.parlay.ev_ranker import DEFAULT_MAX_PARLAYS
from app.services.daily_board import build_daily_board
from app.services.forward_clv import summarize_clv as summarize_mlb_clv
from app.services.prop_pick_tracker import (
    backfill_prop_results,
    summarize_prop_tracker,
)
from app.services.nba_custom_weights import (
    default_weights_payload,
    load_custom_weights_config,
    save_custom_weights_config,
    weights_payload,
)
from app.services.nba_daily_board import build_nba_daily_board
from app.odds.odds_repository import get_today_snapshot
from app.odds.live_odds import live_odds_enabled
from app.services.morning_refresh import get_refresh_status
from app.services.odds_hourly_refresh import hourly_refresh_enabled, run_hourly_odds_refresh
from app.services.prop_tracker_refresh import (
    prop_tracker_auto_enabled,
    run_prop_tracker_refresh,
)
from app.services.game_insights import build_game_insights
from app.services.props_mlb import (
    build_daily_top_props,
    DEFAULT_DISPLAY_BOOKMAKER,
    build_game_props,
    ensure_props_cache_generation,
    evaluate_prop_parlay,
    export_slip_for_bookmaker,
    get_props_cache_meta,
    list_prop_bookmakers,
    list_prop_market_types,
    search_daily_props,
)
from app.services.home_summary import get_home_today_summary
from app.services.news_feed import get_news_headlines
from app.services.cfb_daily_board import build_cfb_daily_board
from app.services.cfb_slate_predictions import predict_slate
from app.services.cfb_backtest_report import (
    load_saved_cfb_backtest_report,
    run_cfb_walk_forward_backtest,
)
from app.services.schedule_cfb import get_cfb_game, get_cfb_schedule
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


async def _maintenance_loop() -> None:
    """Hourly odds refresh + prop tracker grading while the server is up."""
    await asyncio.sleep(30)
    while True:
        try:
            if hourly_refresh_enabled() and live_odds_enabled():
                run_hourly_odds_refresh()
            if prop_tracker_auto_enabled():
                run_prop_tracker_refresh()
        except Exception as exc:
            logger.warning("Background maintenance error: %s", exc)
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    wiped = ensure_props_cache_generation()
    if wiped:
        logger.info(
            "Props cache wiped on startup (generation=%s, removed=%s files)",
            wiped.get("generation"),
            wiped.get("removed_files"),
        )
    maintenance_task: asyncio.Task | None = None
    try:
        import pandas as pd

        from app.features.mlb_pregame import get_team_tracker_before
        from app.features.mlb_totals_pregame import get_runs_tracker_before

        today = pd.Timestamp(date_type.today())
        get_team_tracker_before(today)
        get_runs_tracker_before(today)
    except Exception:
        pass
    if (hourly_refresh_enabled() and live_odds_enabled()) or prop_tracker_auto_enabled():
        maintenance_task = asyncio.create_task(_maintenance_loop())
        logger.info(
            "Background maintenance started (3600s): odds=%s props=%s",
            hourly_refresh_enabled() and live_odds_enabled(),
            prop_tracker_auto_enabled(),
        )
    if auth_enabled():
        if auth_misconfigured():
            logger.error(
                "Admin auth is ON but ADMIN_PASSWORD is not set — "
                "boards/lab are locked until you add ADMIN_PASSWORD to .env"
            )
        else:
            logger.info("Admin auth enabled for boards and model lab")
    yield
    if maintenance_task is not None:
        maintenance_task.cancel()
        try:
            await maintenance_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="NTG Sports", lifespan=lifespan)
app.add_middleware(AdminAuthMiddleware)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

_HTML_NO_CACHE = {"Cache-Control": "no-cache, must-revalidate"}


def _html_page(name: str) -> FileResponse:
    return FileResponse(STATIC_DIR / name, headers=_HTML_NO_CACHE)


class LoginRequest(BaseModel):
    username: str
    password: str


class SaveCustomWeightsRequest(BaseModel):
    factors: dict[str, float] = Field(..., min_length=1)


class PropParlayLeg(BaseModel):
    player: str = Field(..., min_length=1)
    market_type: str = Field(..., min_length=1)
    market_label: str | None = None
    side: str = Field(..., pattern="^(over|under)$")
    line: float
    american_odds: int
    game_id: str | None = None
    matchup: str | None = None
    score: float | None = None


class PropParlayEvalRequest(BaseModel):
    legs: list[PropParlayLeg] = Field(default_factory=list)


class PropSlipExportRequest(BaseModel):
    legs: list[PropParlayLeg] = Field(default_factory=list)
    bookmaker: str = Field(
        DEFAULT_DISPLAY_BOOKMAKER,
        description="Target sportsbook key for export (DraftKings, FanDuel, etc.).",
    )
    refresh_links: bool = Field(
        True,
        description="When cached props lack deeplinks, refresh from Odds API (quota-gated).",
    )


@app.get("/login")
async def login_page():
    return FileResponse(STATIC_DIR / "login.html")


@app.post("/api/auth/login")
async def auth_login(body: LoginRequest):
    if not auth_enabled():
        return JSONResponse(
            status_code=503,
            content={"detail": "Admin auth is not configured on this server"},
        )
    if auth_misconfigured():
        return JSONResponse(
            status_code=503,
            content={
                "detail": "ADMIN_PASSWORD is not set on the server — add it to .env and restart",
            },
        )
    if not verify_credentials(body.username, body.password):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    response = JSONResponse({"ok": True})
    set_session_cookie(response)
    return response


@app.post("/api/auth/logout")
async def auth_logout():
    response = JSONResponse({"ok": True})
    clear_session_cookie(response)
    return response


@app.get("/api/auth/status")
async def auth_status(request: Request):
    return {
        "auth_enabled": auth_enabled(),
        "auth_misconfigured": auth_misconfigured(),
        "authenticated": is_authenticated(request),
    }


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


@app.get("/api/build")
async def build_info():
    """Deploy verification: BUILD id + props cache health (public)."""
    build_path = PROJECT_ROOT / "BUILD"
    build_id = build_path.read_text(encoding="utf-8").strip() if build_path.exists() else "unknown"
    props_dir = PROJECT_ROOT / "data" / "processed" / "props_repository"
    props_sample = build_daily_top_props(date_type.today(), limit=3, scan=False)
    return {
        "build_id": build_id,
        "project_root": str(PROJECT_ROOT),
        "features": {
            "mlb_player_props": True,
            "home_prop_slip": True,
            "matchup_ranked_props": True,
            "bet_context_line_strength": True,
        },
        "props_cache_games": len(list(props_dir.glob("*.json"))) if props_dir.exists() else 0,
        "props_repository_exists": props_dir.exists(),
        "props_service": (PROJECT_ROOT / "app" / "services" / "props_mlb.py").exists(),
        "props_api": {
            "total_actionable": props_sample.get("total_actionable", 0),
            "top_count": len(props_sample.get("top_props") or []),
            "source": props_sample.get("source"),
            "hint": props_sample.get("hint"),
            "live_odds_enabled": props_sample.get("live_odds_enabled"),
        },
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


@app.get("/api/props/tracker/summary")
async def props_tracker_summary(
    days: int = Query(30, ge=1, le=365),
):
    return summarize_prop_tracker(days=days)


@app.post("/api/props/tracker/backfill")
async def props_tracker_backfill(
    date_param: str | None = Query(None, alias="date"),
):
    game_date = date_type.fromisoformat(date_param) if date_param else None
    return backfill_prop_results(game_date)


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
    skip_totals: bool | None = Query(
        None,
        description="Skip O/U model columns (default: true in demo/use_cache mode)",
    ),
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
        skip_totals=skip_totals,
    )


@app.get("/api/nba/custom-weights")
async def nba_custom_weights_get():
    """Global factor weights for the weighted NBA model (applies to all games)."""
    return weights_payload()


@app.put("/api/nba/custom-weights")
async def nba_custom_weights_put(body: SaveCustomWeightsRequest):
    try:
        cfg = load_custom_weights_config()
        cfg["factors"] = body.factors
        saved = save_custom_weights_config(cfg)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **weights_payload(), "model_id": saved.get("model_id")}


@app.post("/api/nba/custom-weights/reset")
async def nba_custom_weights_reset():
    return {"ok": True, **default_weights_payload()}


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


@app.get("/api/schedule/cfb")
async def cfb_schedule(
    date_param: str | None = Query(None, alias="date"),
    refresh: bool = Query(False, description="Bypass saved cache; re-fetch ingest or ESPN"),
):
    if date_param:
        game_date = date_type.fromisoformat(date_param)
        return get_cfb_schedule(game_date, auto_resolve=False, force_live=refresh)
    return get_cfb_schedule(None, auto_resolve=True, force_live=refresh)


@app.get("/api/cfb/daily")
async def cfb_daily(
    date_param: str | None = Query(None, alias="date"),
    min_edge: float = Query(DEFAULT_MIN_EDGE, ge=0.0, le=0.5),
    refresh: bool = Query(False, description="Force refresh live CFB odds from API"),
    use_cache: bool = Query(
        False,
        description="Demo mode — fixed holdout date with CFBD cached lines",
    ),
):
    game_date = (
        date_type.fromisoformat(date_param) if date_param else date_type.today()
    )
    return build_cfb_daily_board(
        game_date=game_date,
        min_edge=min_edge,
        use_cache=use_cache,
        force_refresh=refresh and not use_cache,
    )


@app.get("/api/cfb/predictions")
async def cfb_predictions(
    date_param: str | None = Query(None, alias="date"),
):
    game_date = date_type.fromisoformat(date_param) if date_param else None
    return predict_slate(game_date)


@app.get("/api/cfb/backtest")
async def cfb_backtest(
    refresh: bool = Query(False, description="Re-run walk-forward backtest"),
):
    if refresh:
        return run_cfb_walk_forward_backtest(write_cache=True)
    saved = load_saved_cfb_backtest_report()
    if saved.get("status") not in (None, "missing", "error"):
        return saved
    return run_cfb_walk_forward_backtest(write_cache=True)


@app.get("/api/cfb/backtest/saved")
async def cfb_backtest_saved():
    return load_saved_cfb_backtest_report()


@app.get("/api/scores/today")
async def scores_today(
    sport: str = Query("mlb", pattern="^(mlb|nba|cfb|all)$"),
    date_param: str | None = Query(None, alias="date"),
):
    game_date = date_type.fromisoformat(date_param) if date_param else None
    auto_resolve = date_param is None and sport in ("nba", "cfb", "all")
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


@app.get("/api/games/mlb/{game_id}/props")
async def mlb_game_props(
    game_id: str,
    date_param: str | None = Query(None, alias="date"),
    refresh: bool = Query(False),
    bookmaker: str | None = Query(
        DEFAULT_DISPLAY_BOOKMAKER,
        description="Sportsbook key (default DraftKings; consensus = median across major books).",
    ),
):
    game_date = (
        date_type.fromisoformat(date_param) if date_param else date_type.today()
    )
    payload = build_game_props(
        game_id,
        game_date=game_date,
        refresh=refresh,
        bookmaker=bookmaker,
    )
    if payload is None:
        raise HTTPException(status_code=404, detail="Game not found")
    return payload


@app.get("/api/props/bookmakers")
async def prop_bookmakers():
    return {"bookmakers": list_prop_bookmakers()}


@app.get("/api/props/markets")
async def prop_markets():
    return {"markets": list_prop_market_types()}


@app.get("/api/props/search")
async def props_search(
    date_param: str | None = Query(None, alias="date"),
    bookmaker: str | None = Query(DEFAULT_DISPLAY_BOOKMAKER),
    market_type: str | None = Query(None),
    min_odds: int | None = Query(
        None,
        description="Minimum American odds on the recommended side (e.g. -200).",
    ),
    line_kind: str | None = Query(
        None,
        description="main, alternate, or both (default both).",
    ),
    line_value: float | None = Query(
        None,
        description="Exact line value filter (e.g. 2.0).",
    ),
    actionable_only: bool = Query(False),
    very_strong_only: bool = Query(False),
    include_alternates: bool = Query(False),
    limit: int = Query(100, ge=1, le=200),
    scan: bool = Query(False),
    refresh: bool = Query(False),
):
    game_date = (
        date_type.fromisoformat(date_param) if date_param else date_type.today()
    )
    result = search_daily_props(
        game_date,
        bookmaker=bookmaker,
        market_type=market_type,
        min_odds=min_odds,
        line_kind=line_kind,
        line_value=line_value,
        actionable_only=actionable_only,
        limit=limit,
        scan=scan,
        refresh=refresh,
        include_alternates=include_alternates,
        very_strong_only=very_strong_only,
    )
    if not result.get("props") and not scan and not refresh:
        result = search_daily_props(
            game_date,
            bookmaker=bookmaker,
            market_type=market_type,
            min_odds=min_odds,
            line_kind=line_kind,
            line_value=line_value,
            actionable_only=actionable_only,
            limit=limit,
            scan=True,
            include_alternates=include_alternates,
            very_strong_only=very_strong_only,
        )
        result["auto_scanned"] = True
    return result


@app.post("/api/parlay/props/eval")
async def prop_parlay_eval(body: PropParlayEvalRequest):
    legs = [leg.model_dump() for leg in body.legs]
    return evaluate_prop_parlay(legs)


@app.post("/api/props/slip/export")
async def prop_slip_export(body: PropSlipExportRequest):
    legs = [leg.model_dump() for leg in body.legs]
    return export_slip_for_bookmaker(
        legs,
        body.bookmaker,
        refresh_links=body.refresh_links,
    )


@app.get("/api/props/cache-meta")
async def props_cache_meta():
    return get_props_cache_meta()


@app.get("/api/daily/props")
async def daily_top_props(
    date_param: str | None = Query(None, alias="date"),
    limit: int = Query(10, ge=1, le=30),
    scan: bool = Query(
        False,
        description="Scan slate for props (uses cache; fetches missing games up to cap).",
    ),
    refresh: bool = Query(
        False,
        description="Force refresh props for the selected sportsbook.",
    ),
    bookmaker: str | None = Query(
        DEFAULT_DISPLAY_BOOKMAKER,
        description="Sportsbook key (default DraftKings; consensus = median across major books).",
    ),
):
    game_date = (
        date_type.fromisoformat(date_param) if date_param else date_type.today()
    )
    cache_meta = get_props_cache_meta()
    if cache_meta.get("requires_refresh"):
        scan = True
        refresh = True
    result = build_daily_top_props(
        game_date,
        limit=limit,
        scan=scan,
        refresh=refresh,
        bookmaker=bookmaker,
    )
    if not result.get("top_props") and not scan and not refresh:
        result = build_daily_top_props(
            game_date,
            limit=limit,
            scan=True,
            bookmaker=bookmaker,
        )
        result["auto_scanned"] = True
    return result


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
    return _html_page("index.html")


@app.get("/mlb")
async def mlb_slate():
    return FileResponse(STATIC_DIR / "mlb_slate.html")


@app.get("/nba")
async def nba_slate():
    return FileResponse(STATIC_DIR / "nba_slate.html")


@app.get("/cfb")
async def cfb_slate():
    return FileResponse(STATIC_DIR / "cfb_slate.html")


@app.get("/cfb/board")
async def cfb_board():
    return FileResponse(STATIC_DIR / "cfb_board.html")


@app.get("/cfb/game/{game_id}")
async def cfb_game_page(game_id: str):
    return FileResponse(STATIC_DIR / "cfb_game.html")


@app.get("/api/games/cfb/{game_id}/insights")
async def cfb_game_insights(
    game_id: str,
    date_param: str | None = Query(None, alias="date"),
    use_cache: bool = Query(False),
    refresh: bool = Query(False),
):
    from app.services.cfb_game_insights import build_cfb_game_insights

    game_date = date_type.fromisoformat(date_param) if date_param else None
    insights = build_cfb_game_insights(
        game_id,
        game_date=game_date,
        use_cache=use_cache,
        refresh=refresh,
    )
    if insights is None:
        raise HTTPException(status_code=404, detail="Game not found")
    return insights


@app.get("/api/games/cfb/{game_id}")
async def cfb_game_detail(
    game_id: str,
    date_param: str | None = Query(None, alias="date"),
):
    game_date = date_type.fromisoformat(date_param) if date_param else None
    detail = get_cfb_game(game_id, game_date)
    if detail is None:
        raise HTTPException(status_code=404, detail="Game not found")
    return detail


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
    return _html_page("game.html")


@app.get("/sandbox")
async def sandbox_page():
    return FileResponse(STATIC_DIR / "sandbox.html")


@app.get("/updates")
async def updates_page():
    return FileResponse(STATIC_DIR / "updates.html")


@app.get("/nba/board")
async def nba_board():
    return FileResponse(STATIC_DIR / "nba.html")


@app.get("/nba/board/factors")
async def nba_board_factors():
    return FileResponse(STATIC_DIR / "nba_factors.html")


@app.get("/mlb/props")
async def mlb_props_page():
    return _html_page("mlb_props.html")


@app.get("/mlb/board")
async def mlb_board():
    return FileResponse(STATIC_DIR / "mlb.html")


@app.get("/mlb/board/demo")
async def mlb_board_demo():
    return FileResponse(STATIC_DIR / "mlb_board_demo.html")


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
