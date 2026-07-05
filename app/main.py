import app.config  # noqa: F401 — load .env before auth middleware reads env vars
from app.config import PROJECT_ROOT, prop_slip_public_enabled

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import date as date_type
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from app.auth.admin_auth import (
    AdminAuthMiddleware,
    auth_enabled,
    auth_misconfigured,
    clear_session_cookie,
    is_authenticated,
    set_session_cookie,
    verify_credentials,
)
from app.auth.public_api_gate import PublicApiGateMiddleware
from app.auth.user_auth import (
    UserPropsAuthMiddleware,
    can_access_props,
    clear_user_session_cookie,
    get_user_session,
    is_valid_email,
    props_require_verified_user,
    set_user_session_cookie,
    user_registration_enabled,
)
from app.services.mlb_page_data import (
    build_home_page_data,
    build_mlb_game_page_data,
    build_mlb_props_page_data,
    build_mlb_slate_page_data,
)
from app.services.page_data_cache import get_or_build
from app.services.page_render import render_static_page
from app.db.database import get_connection, init_db
from app.models.constants import DEFAULT_MIN_EDGE
from app.parlay.ev_ranker import DEFAULT_MAX_PARLAYS
from app.services.daily_board import build_daily_board
from app.services.forward_clv import summarize_clv as summarize_mlb_clv
from app.services.prop_pick_tracker import (
    backfill_prop_results,
    list_recent_picks,
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
from app.services.mlb_periodic_refresh import (
    periodic_refresh_enabled,
    periodic_refresh_interval_seconds,
    run_mlb_periodic_refresh,
)
from app.services.prop_tracker_refresh import (
    prop_tracker_auto_enabled,
    run_prop_tracker_refresh,
)
from app.services.game_insights import build_game_insights
from app.services.props_mlb import (
    build_daily_top_props,
    build_prop_debug_report,
    DEFAULT_DISPLAY_BOOKMAKER,
    build_game_props,
    ensure_props_cache_generation,
    evaluate_prop_parlay,
    export_slip_for_bookmaker,
    get_props_cache_meta,
    list_prop_bookmakers,
    list_prop_market_types,
    refresh_props_slate,
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
from app.services.schedule_ufc import get_ufc_fight, get_ufc_schedule
from app.services.ufc_daily_board import build_ufc_daily_board
from app.services.ufc_slate_predictions import predict_slate, _clean_json_value
from app.services.ufc_backtest_report import (
    load_saved_ufc_backtest_report,
    run_ufc_walk_forward_backtest,
)
from app.services.mlb_game_lineup import get_mlb_game_lineup
from app.services.schedule_mlb import get_mlb_game, get_mlb_schedule
from app.services.teams_hub import get_team_detail, list_teams
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
from app.services.performance_charts import model_vs_market_chart, performance_trend_chart
from app.services.prop_parlay_builder import build_auto_prop_parlay
from app.services.player_profile import get_player_profile
from app.services.player_context import get_player_prop_context, resolve_player_id_for_name
from app.services.slip_optimizer import suggest_prop_slip_swap
from app.services.matchup_preview import build_matchup_preview
from app.services.user_teams import (
    follow_team,
    get_alert_prefs,
    list_follows,
    set_alert_prefs,
    unfollow_team,
)
from app.services.user_players import (
    build_player_feed,
    follow_player,
    list_player_follows,
    unfollow_player,
)
from app.db.market_status import get_market_eval_status
from app.db.parlay_status import get_parlay_status
from app.db.mlb_status import get_mlb_data_status
from app.db.totals_status import get_totals_model_status
from app.services.user_accounts import (
    create_user,
    get_user_by_email,
    get_user_by_id,
    get_user_by_verification_token,
    mark_email_verified,
    rotate_verification_token,
    verification_token_valid,
    verify_user_credentials,
)
from app.services.email_service import (
    build_verification_url,
    dev_expose_verification_url,
    send_verification_email,
)

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


class TeamFollowRequest(BaseModel):
    sport: str = Field(..., pattern="^(mlb|nba|cfb)$")
    team_id: str = Field(..., min_length=1, max_length=32)


class PlayerFollowRequest(BaseModel):
    sport: str = Field(..., pattern="^(mlb|nba|cfb)$")
    player_id: str = Field(..., min_length=1, max_length=32)
    player_name: str = Field(..., min_length=1, max_length=120)
    team_id: str | None = Field(None, max_length=32)


class AlertPrefsRequest(BaseModel):
    daily_digest: bool = False
    digest_hour_et: int = Field(8, ge=0, le=23)


logger = logging.getLogger(__name__)


async def _maintenance_loop() -> None:
    """Background odds refresh, MLB ingest/board rebuild, and prop tracker grading."""
    import time

    await asyncio.sleep(30)
    last_odds = 0.0
    last_mlb = 0.0
    while True:
        try:
            now = time.time()
            if hourly_refresh_enabled() and live_odds_enabled() and now - last_odds >= 3600:
                run_hourly_odds_refresh()
                last_odds = now
            if periodic_refresh_enabled() and now - last_mlb >= periodic_refresh_interval_seconds():
                run_mlb_periodic_refresh()
                last_mlb = now
            if prop_tracker_auto_enabled():
                run_prop_tracker_refresh()
        except Exception as exc:
            logger.warning("Background maintenance error: %s", exc)
        await asyncio.sleep(60)


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
    try:
        from app.services.daily_board import ensure_today_daily_board

        await asyncio.to_thread(ensure_today_daily_board)
    except Exception as exc:
        logger.warning("Startup daily board ensure failed: %s", exc)
    if (
        (hourly_refresh_enabled() and live_odds_enabled())
        or periodic_refresh_enabled()
        or prop_tracker_auto_enabled()
    ):
        maintenance_task = asyncio.create_task(_maintenance_loop())
        logger.info(
            "Background maintenance started (60s tick): odds=%s mlb_periodic=%s props=%s",
            hourly_refresh_enabled() and live_odds_enabled(),
            periodic_refresh_enabled(),
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
app.add_middleware(PublicApiGateMiddleware)
app.add_middleware(UserPropsAuthMiddleware)
app.add_middleware(AdminAuthMiddleware)


class StaticCacheMiddleware(BaseHTTPMiddleware):
    """Long-cache versioned static assets; HTML stays no-cache."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/static/") and request.query_params.get("v"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


app.add_middleware(StaticCacheMiddleware)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

_HTML_NO_CACHE = {"Cache-Control": "no-cache, must-revalidate"}


def _html_page(name: str) -> FileResponse:
    return FileResponse(STATIC_DIR / name, headers=_HTML_NO_CACHE)


def _pct_query(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    parsed = float(value)
    if parsed > 1.0:
        parsed /= 100.0
    return parsed


def _bool_query(raw: str | None) -> bool:
    if raw is None:
        return False
    return raw.strip().lower() in ("1", "true", "yes", "on")


class LoginRequest(BaseModel):
    username: str
    password: str


class UserRegisterRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=8, max_length=128)
    accept_terms: bool = Field(..., description="Must accept Terms and Privacy Policy")


class UserLoginRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=1, max_length=128)


class VerifyEmailRequest(BaseModel):
    token: str = Field(..., min_length=8, max_length=256)


class ResendVerificationRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)


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


class PropParlayOptimizeRequest(BaseModel):
    legs: list[PropParlayLeg] = Field(default_factory=list)


class PropParlayBuildRequest(BaseModel):
    leg_count: int = Field(..., ge=2, le=25)
    target_american: int | None = Field(
        None,
        description="Target combined American payout (e.g. 5000 for +5000).",
    )
    bookmaker: str | None = None
    date: str | None = Field(None, description="ISO date (default today).")


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


@app.get("/signin")
async def signin_page():
    return _html_page("signin.html")


@app.get("/signup")
async def signup_page():
    return _html_page("signup.html")


@app.get("/verify-email")
async def verify_email_page():
    return _html_page("verify_email.html")


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
    session = get_user_session(request)
    user_row = get_user_by_id(session["user_id"]) if session else None
    return {
        "auth_enabled": auth_enabled(),
        "auth_misconfigured": auth_misconfigured(),
        "authenticated": is_authenticated(request),
        "user_auth": {
            "registration_enabled": user_registration_enabled(),
            "props_require_verified_user": props_require_verified_user(),
            "signed_in": session is not None,
            "email": session["email"] if session else None,
            "email_verified": bool(user_row and user_row.get("email_verified_at")),
            "can_access_props": can_access_props(request, user_row=user_row),
        },
    }


@app.post("/api/auth/user/register")
async def user_register(body: UserRegisterRequest):
    if not user_registration_enabled():
        raise HTTPException(status_code=503, detail="Registration is disabled")
    if not body.accept_terms:
        raise HTTPException(
            status_code=400,
            detail="You must accept the Terms of Service and Privacy Policy",
        )
    if not is_valid_email(body.email):
        raise HTTPException(status_code=400, detail="Invalid email address")
    if get_user_by_email(body.email):
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    user, token = create_user(body.email, body.password, terms_accepted=True)
    send_verification_email(user["email"], token)
    payload: dict[str, Any] = {
        "ok": True,
        "email": user["email"],
        "message": "Account created. Check your email to verify your address.",
    }
    if dev_expose_verification_url():
        payload["dev_verification_url"] = build_verification_url(token)
        payload["message"] = (
            "Account created. Local dev mode — no email sent. Use the verification link below."
        )
    response = JSONResponse(payload)
    set_user_session_cookie(response, user["id"], user["email"])
    return response


@app.post("/api/auth/user/login")
async def user_login(body: UserLoginRequest):
    if not is_valid_email(body.email):
        raise HTTPException(status_code=400, detail="Invalid email address")
    user = verify_user_credentials(body.email, body.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    response = JSONResponse(
        {
            "ok": True,
            "email": user["email"],
            "email_verified": bool(user.get("email_verified_at")),
        }
    )
    set_user_session_cookie(response, user["id"], user["email"])
    return response


@app.post("/api/auth/user/logout")
async def user_logout():
    response = JSONResponse({"ok": True})
    clear_user_session_cookie(response)
    return response


@app.post("/api/auth/user/verify-email")
async def user_verify_email(body: VerifyEmailRequest):
    user = get_user_by_verification_token(body.token)
    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired verification link")
    if user.get("email_verified_at"):
        return {"ok": True, "already_verified": True, "email": user["email"]}
    if not verification_token_valid(user):
        raise HTTPException(status_code=400, detail="Verification link expired — request a new one")
    mark_email_verified(user["id"])
    response = JSONResponse({"ok": True, "email": user["email"]})
    set_user_session_cookie(response, user["id"], user["email"])
    return response


@app.post("/api/auth/user/resend-verification")
async def user_resend_verification(body: ResendVerificationRequest):
    if not is_valid_email(body.email):
        raise HTTPException(status_code=400, detail="Invalid email address")
    user = get_user_by_email(body.email)
    if not user:
        return {"ok": True, "message": "If that email is registered, a verification link was sent."}
    if user.get("email_verified_at"):
        return {"ok": True, "already_verified": True}
    token = rotate_verification_token(user["id"])
    send_verification_email(user["email"], token)
    payload: dict[str, Any] = {"ok": True, "message": "Verification email sent."}
    if dev_expose_verification_url():
        payload["dev_verification_url"] = build_verification_url(token)
        payload["message"] = "Local dev mode — no email sent. Use the verification link below."
    return payload


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
    props_meta = get_props_cache_meta()
    slate_path = (
        PROJECT_ROOT
        / "data"
        / "processed"
        / "props_repository"
        / f"slate_{date_type.today().isoformat()}.draftkings.json"
    )
    slate_summary: dict[str, Any] = {}
    if slate_path.exists():
        try:
            slate = json.loads(slate_path.read_text(encoding="utf-8"))
            slate_summary = {
                "total_actionable": slate.get("total_actionable", 0),
                "top_count": len(slate.get("top_props") or []),
                "source": slate.get("source") or "slate_cache",
            }
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "build_id": build_id,
        "project_root": str(PROJECT_ROOT),
        "features": {
            "mlb_player_props": True,
            "home_prop_slip": prop_slip_public_enabled(),
            "prop_slip": prop_slip_public_enabled(),
            "props_require_verified_user": props_require_verified_user(),
            "user_registration_enabled": user_registration_enabled(),
            "matchup_ranked_props": True,
            "bet_context_line_strength": True,
        },
        "props_cache_games": len(list(props_dir.glob("*.json"))) if props_dir.exists() else 0,
        "props_repository_exists": props_dir.exists(),
        "props_service": (PROJECT_ROOT / "app" / "services" / "props_mlb.py").exists(),
        "props_api": {
            **props_meta,
            **slate_summary,
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
    sport: str = Query("mlb", pattern="^(mlb|nba|ufc)$"),
):
    if sport == "nba":
        from app.services.nba_forward_clv import summarize_clv as summarize_nba_clv

        return summarize_nba_clv(days=days)
    if sport == "ufc":
        from app.services.ufc_forward_clv import summarize_clv as summarize_ufc_clv

        return summarize_ufc_clv(days=days)
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
    try:
        return get_home_today_summary(game_date)
    except Exception as exc:
        logger.exception("Home summary failed for %s", game_date)
        raise HTTPException(
            status_code=503, detail="Home summary temporarily unavailable"
        ) from exc


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


@app.get("/api/schedule/ufc")
async def ufc_schedule(
    date_param: str | None = Query(None, alias="date"),
    refresh: bool = Query(False, description="Bypass saved cache; re-fetch ingest or ESPN"),
):
    if date_param:
        game_date = date_type.fromisoformat(date_param)
        return get_ufc_schedule(game_date, auto_resolve=False, force_live=refresh)
    return get_ufc_schedule(None, auto_resolve=True, force_live=refresh)


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


@app.get("/api/ufc/daily")
async def ufc_daily(
    date_param: str | None = Query(None, alias="date"),
    min_edge: float = Query(DEFAULT_MIN_EDGE, ge=0.0, le=0.5),
    refresh: bool = Query(False, description="Force refresh live UFC odds from API"),
    use_cache: bool = Query(
        False,
        description="Demo mode — fixed holdout card with cached lines",
    ),
):
    game_date = (
        date_type.fromisoformat(date_param) if date_param else date_type.today()
    )
    return build_ufc_daily_board(
        game_date=game_date,
        min_edge=min_edge,
        use_cache=use_cache,
        force_refresh=refresh and not use_cache,
    )


@app.get("/api/ufc/predictions")
async def ufc_predictions(
    date_param: str | None = Query(None, alias="date"),
):
    game_date = date_type.fromisoformat(date_param) if date_param else None
    return _clean_json_value(predict_slate(game_date))


@app.get("/api/ufc/backtest")
async def ufc_backtest(
    refresh: bool = Query(False, description="Re-run walk-forward backtest"),
):
    if refresh:
        return run_ufc_walk_forward_backtest(write_cache=True)
    saved = load_saved_ufc_backtest_report()
    if saved.get("status") not in (None, "missing", "error"):
        return saved
    return run_ufc_walk_forward_backtest(write_cache=True)


@app.get("/api/ufc/backtest/saved")
async def ufc_backtest_saved():
    return load_saved_ufc_backtest_report()


@app.get("/api/ufc/market")
async def ufc_market_eval(
    refresh: bool = Query(False),
    edge_threshold: float = Query(DEFAULT_MIN_EDGE, ge=0.0, le=0.5),
):
    from app.odds.ufc_market_eval import MARKET_EVAL_JSON, run_market_evaluation

    if refresh or not MARKET_EVAL_JSON.exists():
        return run_market_evaluation(edge_threshold=edge_threshold)
    try:
        return json.loads(MARKET_EVAL_JSON.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return run_market_evaluation(edge_threshold=edge_threshold)


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
    sport: str = Query("mlb", pattern="^(mlb|nba|cfb|ufc|all)$"),
    date_param: str | None = Query(None, alias="date"),
):
    game_date = date_type.fromisoformat(date_param) if date_param else None
    auto_resolve = date_param is None and sport in ("nba", "cfb", "ufc", "all")
    return get_scores_today(sport=sport, game_date=game_date, auto_resolve=auto_resolve)


@app.get("/api/games/mlb/{game_id}/lineup")
async def mlb_game_lineup(
    game_id: str,
    date_param: str | None = Query(None, alias="date"),
):
    game_date = (
        date_type.fromisoformat(date_param) if date_param else date_type.today()
    )
    try:
        return get_mlb_game_lineup(game_id, game_date)
    except Exception as exc:
        logger.exception("MLB lineup failed for game %s", game_id)
        raise HTTPException(status_code=503, detail="Lineup temporarily unavailable") from exc


@app.get("/api/teams")
async def teams_list(
    sport: str = Query("mlb", pattern="^(mlb|nba|cfb)$"),
    q: str | None = Query(None, min_length=1, max_length=64),
):
    return list_teams(sport, query=q)


@app.get("/api/teams/{sport}/{team_id}")
async def team_detail(sport: str, team_id: str):
    if sport not in ("mlb", "nba", "cfb"):
        raise HTTPException(status_code=400, detail="Unsupported sport")
    detail = get_team_detail(sport, team_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Team not found")
    return detail


@app.get("/api/games/mlb/{game_id}")
async def mlb_game_detail(
    game_id: str,
    date_param: str | None = Query(None, alias="date"),
):
    game_date = (
        date_type.fromisoformat(date_param) if date_param else date_type.today()
    )
    detail = get_mlb_game(game_id, game_date, allow_fetch=True)
    if detail is None:
        raise HTTPException(status_code=404, detail="Game not found")
    return detail


@app.get("/api/games/mlb/{game_id}/preview")
async def mlb_matchup_preview(
    game_id: str,
    date_param: str | None = Query(None, alias="date"),
    use_cache: bool = Query(False),
):
    game_date = date_type.fromisoformat(date_param) if date_param else date_type.today()
    return build_matchup_preview("mlb", game_id, game_date=game_date, use_cache=use_cache)


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
    try:
        insights = await asyncio.to_thread(
            build_game_insights,
            game_id,
            game_date=game_date,
            use_cache=use_cache,
            refresh=refresh,
        )
    except Exception as exc:
        logger.exception("MLB insights failed for game %s", game_id)
        raise HTTPException(status_code=503, detail="Game insights temporarily unavailable") from exc
    if insights is None:
        raise HTTPException(status_code=404, detail="Game not found")
    return insights


@app.get("/api/games/mlb/{game_id}/props")
async def mlb_game_props(
    game_id: str,
    date_param: str | None = Query(None, alias="date"),
    refresh: bool = Query(False),
    include_all_markets: bool = Query(
        False,
        description="Fetch extended prop markets (RBIs, pitcher ER, etc.) for this game.",
    ),
    bookmaker: str | None = Query(
        DEFAULT_DISPLAY_BOOKMAKER,
        description="Sportsbook key (default DraftKings; consensus = median across major books).",
    ),
):
    game_date = (
        date_type.fromisoformat(date_param) if date_param else date_type.today()
    )
    try:
        payload = await asyncio.to_thread(
            build_game_props,
            game_id,
            game_date=game_date,
            refresh=refresh,
            bookmaker=bookmaker,
            include_all_markets=include_all_markets,
        )
    except Exception as exc:
        logger.exception("MLB props failed for game %s", game_id)
        raise HTTPException(
            status_code=503, detail="Player props temporarily unavailable"
        ) from exc
    if payload is None:
        raise HTTPException(status_code=404, detail="Game not found")
    return payload


@app.get("/api/props/bookmakers")
async def prop_bookmakers():
    return {"bookmakers": list_prop_bookmakers()}


@app.get("/api/props/markets")
async def prop_markets():
    return {"markets": list_prop_market_types()}


@app.get("/api/props/debug")
async def props_debug(
    date_param: str | None = Query(None, alias="date"),
    game_id: str | None = Query(None),
    bookmaker: str | None = Query(DEFAULT_DISPLAY_BOOKMAKER),
    limit: int = Query(500, ge=1, le=1000),
):
    game_date = (
        date_type.fromisoformat(date_param) if date_param else date_type.today()
    )
    return build_prop_debug_report(
        game_date,
        game_id=game_id,
        bookmaker=bookmaker,
        limit=limit,
    )


@app.get("/mlb/props/debug")
async def mlb_props_debug_page():
    return _html_page("prop_debug.html")


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
    side: str | None = Query(
        None,
        description="Filter by bet side: over, under, or both (default both).",
    ),
    actionable_only: bool = Query(False),
    very_strong_only: bool = Query(False),
    include_alternates: bool = Query(False),
    sort: str = Query(
        "score",
        description="score, hit_l5, hit_l10, risk_asc, risk_desc",
    ),
    risk: str | None = Query(
        None,
        description="low, medium, high, or low_medium",
    ),
    min_score: int | None = Query(None, ge=0, le=100),
    min_hit_l5: float | None = Query(None, ge=0, le=1),
    min_hit_l10: float | None = Query(None, ge=0, le=1),
    limit: int = Query(200, ge=1, le=500),
    scan: bool = Query(False),
    refresh: bool = Query(False),
):
    game_date = (
        date_type.fromisoformat(date_param) if date_param else date_type.today()
    )
    if refresh:
        scan = True
    search_kwargs = dict(
        bookmaker=bookmaker,
        market_type=market_type,
        min_odds=min_odds,
        line_kind=line_kind,
        line_value=line_value,
        side=side,
        actionable_only=actionable_only,
        limit=limit,
        scan=scan,
        refresh=refresh,
        include_alternates=include_alternates,
        very_strong_only=very_strong_only,
        sort=sort,
        risk=risk,
        min_score=min_score,
        min_hit_l5=min_hit_l5,
        min_hit_l10=min_hit_l10,
    )
    # Filter/sort-only requests use cached pool; scan runs on refresh or empty pool.
    result = search_daily_props(game_date, **search_kwargs)
    if not result.get("props") and not scan and not refresh:
        result = search_daily_props(
            game_date,
            **{**search_kwargs, "scan": True, "refresh": False},
        )
        result["auto_scanned"] = True
    return result


@app.post("/api/parlay/props/eval")
async def prop_parlay_eval(body: PropParlayEvalRequest):
    legs = [leg.model_dump() for leg in body.legs]
    return evaluate_prop_parlay(legs)


@app.post("/api/parlay/props/optimize")
async def prop_parlay_optimize(body: PropParlayOptimizeRequest):
    legs = [leg.model_dump() for leg in body.legs]
    return suggest_prop_slip_swap(legs)


@app.post("/api/parlay/props/build")
async def prop_parlay_build(body: PropParlayBuildRequest):
    game_date = (
        date_type.fromisoformat(body.date) if body.date else date_type.today()
    )
    return build_auto_prop_parlay(
        body.leg_count,
        target_american=body.target_american,
        bookmaker=body.bookmaker,
        game_date=game_date,
    )


@app.post("/api/props/slip/export")
async def prop_slip_export(body: PropSlipExportRequest):
    if not prop_slip_public_enabled():
        raise HTTPException(status_code=404, detail="Prop slip export is not enabled")
    legs = [leg.model_dump() for leg in body.legs]
    return export_slip_for_bookmaker(
        legs,
        body.bookmaker,
        refresh_links=body.refresh_links,
    )


@app.get("/api/props/cache-meta")
async def props_cache_meta():
    return get_props_cache_meta()


@app.post("/api/props/slate/refresh")
async def props_slate_refresh(
    date_param: str | None = Query(None, alias="date"),
    bookmaker: str | None = Query(
        None,
        description="Sportsbook key (default PROP_SLATE_BOOKMAKER / DraftKings).",
    ),
    force: bool = Query(
        False,
        description="Re-fetch all games even when cache is fresh.",
    ),
    include_alternates: bool = Query(False),
):
    """Pull full-market props for every game on the slate (resumable if quota stops)."""
    game_date = (
        date_type.fromisoformat(date_param) if date_param else date_type.today()
    )
    return refresh_props_slate(
        game_date,
        bookmaker=bookmaker,
        force=force,
        include_alternates=include_alternates or None,
    )


@app.get("/api/props/scan-state")
async def props_scan_state():
    from app.services.props_mlb import _load_scan_state

    return _load_scan_state()


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
    cache_only: bool = Query(
        False,
        description="Return cached slate props only — no scan, refresh, or auto-scan.",
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
    if not cache_only and cache_meta.get("requires_refresh"):
        scan = True
        refresh = False
    result = build_daily_top_props(
        game_date,
        limit=limit,
        scan=scan,
        refresh=refresh,
        bookmaker=bookmaker,
    )
    if (
        not cache_only
        and not result.get("top_props")
        and not scan
        and not refresh
    ):
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


@app.get("/api/performance/summary")
async def performance_summary(days: int = Query(30, ge=1, le=365)):
    summary = summarize_prop_tracker(days=days)
    clv = summarize_mlb_clv(days=days)
    return {
        "prop_tracker": summary,
        "clv": clv,
        "charts": {
            "model_vs_market": model_vs_market_chart(),
            "performance_trend": performance_trend_chart(days=days),
        },
    }


@app.get("/api/performance/picks")
async def performance_picks(
    limit: int = Query(50, ge=1, le=200),
    days: int = Query(30, ge=0, le=365),
    line_strength: str | None = Query(None, pattern="^(strong|moderate|weak)$"),
):
    return {
        "picks": list_recent_picks(
            limit=limit,
            days=days,
            line_strength=line_strength,
        )
    }


@app.get("/api/players/{sport}/{player_id}/prop-context")
async def player_prop_context(
    sport: str,
    player_id: str,
    market_type: str = Query(..., min_length=3),
    line: float = Query(...),
    side: str = Query("over", pattern="^(over|under)$"),
    season: int | None = Query(None),
    game_id: str | None = Query(None),
):
    if sport not in ("mlb", "nba", "cfb"):
        raise HTTPException(status_code=400, detail="Unsupported sport")
    canonical = market_type
    if canonical.endswith("_alternate"):
        canonical = canonical[: -len("_alternate")]
    try:
        result = get_player_prop_context(
            sport,
            player_id,
            market_type=canonical,
            line=line,
            side=side,
            season=season,
            game_id=game_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if result.get("status") == "error":
        raise HTTPException(
            status_code=400,
            detail=result.get("message") or "Could not load prop context",
        )
    return result


@app.get("/api/players/{sport}/{player_id}/profile")
async def player_profile(sport: str, player_id: str):
    if sport not in ("mlb", "nba", "cfb"):
        raise HTTPException(status_code=400, detail="Unsupported sport")
    return get_player_profile(sport, player_id)


@app.get("/api/players/{sport}/lookup")
async def player_lookup(sport: str, name: str = Query(..., min_length=1)):
    pid = resolve_player_id_for_name(sport, name)
    if pid is None:
        raise HTTPException(status_code=404, detail="Player not found")
    return {"player_id": pid, "sport": sport}


@app.get("/api/players/{sport}/by-name/{player_name}/id")
async def player_id_by_name(sport: str, player_name: str):
    pid = resolve_player_id_for_name(sport, player_name)
    if pid is None:
        raise HTTPException(status_code=404, detail="Player not found")
    return {"player_id": pid, "sport": sport}


def _require_user_session(request: Request) -> dict:
    session = get_user_session(request)
    if not session:
        raise HTTPException(status_code=401, detail="Sign in required")
    return session


@app.get("/api/user/teams/follows")
async def user_team_follows_list(request: Request):
    session = _require_user_session(request)
    conn = get_connection()
    try:
        return {"follows": list_follows(conn, session["user_id"])}
    finally:
        conn.close()


@app.post("/api/user/teams/follow")
async def user_team_follow(request: Request, body: TeamFollowRequest):
    session = _require_user_session(request)
    conn = get_connection()
    try:
        return follow_team(conn, session["user_id"], body.sport, body.team_id)
    finally:
        conn.close()


@app.delete("/api/user/teams/follow")
async def user_team_unfollow(
    request: Request,
    sport: str = Query(..., pattern="^(mlb|nba|cfb)$"),
    team_id: str = Query(..., min_length=1, max_length=32),
):
    session = _require_user_session(request)
    conn = get_connection()
    try:
        return unfollow_team(conn, session["user_id"], sport, team_id)
    finally:
        conn.close()


@app.get("/api/user/alerts")
async def user_alert_prefs_get(request: Request):
    session = _require_user_session(request)
    conn = get_connection()
    try:
        return get_alert_prefs(conn, session["user_id"])
    finally:
        conn.close()


@app.post("/api/user/alerts")
async def user_alert_prefs_set(request: Request, body: AlertPrefsRequest):
    session = _require_user_session(request)
    conn = get_connection()
    try:
        return set_alert_prefs(
            conn,
            session["user_id"],
            daily_digest=body.daily_digest,
            digest_hour_et=body.digest_hour_et,
        )
    finally:
        conn.close()


@app.get("/api/user/players/follows")
async def user_player_follows_list(request: Request):
    session = _require_user_session(request)
    conn = get_connection()
    try:
        follows = list_player_follows(conn, session["user_id"])
        return {"follows": follows, "feed": build_player_feed(follows)}
    finally:
        conn.close()


@app.get("/api/user/players/feed")
async def user_player_feed(request: Request):
    session = _require_user_session(request)
    conn = get_connection()
    try:
        follows = list_player_follows(conn, session["user_id"])
        return build_player_feed(follows)
    finally:
        conn.close()


@app.post("/api/user/players/follow")
async def user_player_follow(request: Request, body: PlayerFollowRequest):
    session = _require_user_session(request)
    conn = get_connection()
    try:
        return follow_player(
            conn,
            session["user_id"],
            sport=body.sport,
            player_id=body.player_id,
            player_name=body.player_name,
            team_id=body.team_id,
        )
    finally:
        conn.close()


@app.delete("/api/user/players/follow")
async def user_player_unfollow(
    request: Request,
    sport: str = Query(..., pattern="^(mlb|nba|cfb)$"),
    player_id: str = Query(..., min_length=1, max_length=32),
):
    session = _require_user_session(request)
    conn = get_connection()
    try:
        return unfollow_player(conn, session["user_id"], sport, player_id)
    finally:
        conn.close()


@app.get("/my-team")
async def my_team_page():
    return _html_page("my_team.html")


@app.get("/privacy")
async def privacy_page():
    return _html_page("privacy.html")


@app.get("/terms")
async def terms_page():
    return _html_page("terms.html")


@app.get("/methodology")
async def methodology_page():
    return _html_page("methodology.html")


@app.get("/performance")
async def performance_page():
    return _html_page("performance.html")


@app.get("/preview/mlb/{game_id}")
async def mlb_preview_page(game_id: str):
    return _html_page("preview.html")


@app.get("/parlay")
async def parlay_page():
    return _html_page("prop_slip.html")


@app.get("/teams/{sport}/{team_id}")
async def team_page(sport: str, team_id: str):
    if sport not in ("mlb", "nba", "cfb"):
        raise HTTPException(status_code=404, detail="Not found")
    return _html_page("team.html")


@app.get("/manifest.webmanifest")
async def web_manifest():
    return FileResponse(
        STATIC_DIR / "manifest.webmanifest",
        media_type="application/manifest+json",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/sw.js")
async def service_worker():
    return FileResponse(
        STATIC_DIR / "sw.js",
        media_type="application/javascript",
        headers={
            "Cache-Control": "no-cache, must-revalidate",
            "Service-Worker-Allowed": "/",
        },
    )


@app.get("/install")
async def install_page():
    return _html_page("install.html")


@app.get("/offline")
async def offline_page():
    return _html_page("offline.html")


SSR_PAGE_TIMEOUT_SECONDS = 15.0
SSR_PAGE_CACHE_TTL = 120


async def _ssr_page_data(
    cache_key: str,
    builder,
) -> dict[str, Any]:
    try:
        return await asyncio.wait_for(
            get_or_build(cache_key, SSR_PAGE_CACHE_TTL, builder),
            timeout=SSR_PAGE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        logger.error("SSR page build timed out: %s", cache_key)
        raise HTTPException(
            status_code=503,
            detail="Page load timed out — please retry in a moment.",
        ) from exc


@app.get("/")
async def home(date_param: str | None = Query(None, alias="date")):
    game_date = (
        date_type.fromisoformat(date_param) if date_param else date_type.today()
    )
    page_data = await _ssr_page_data(
        f"home:{game_date.isoformat()}",
        lambda: build_home_page_data(game_date),
    )
    return render_static_page(STATIC_DIR, "index.html", page_data)


@app.get("/mlb")
async def mlb_slate(date_param: str | None = Query(None, alias="date")):
    game_date = (
        date_type.fromisoformat(date_param) if date_param else date_type.today()
    )
    page_data = await _ssr_page_data(
        f"slate:{game_date.isoformat()}",
        lambda: build_mlb_slate_page_data(game_date),
    )
    return render_static_page(STATIC_DIR, "mlb_slate.html", page_data)


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


@app.get("/ufc")
async def ufc_slate():
    return FileResponse(STATIC_DIR / "ufc_slate.html")


@app.get("/ufc/board")
async def ufc_board():
    return FileResponse(STATIC_DIR / "ufc_board.html")


@app.get("/ufc/game/{fight_id}")
async def ufc_fight_page(fight_id: str):
    return FileResponse(STATIC_DIR / "ufc_fight.html")


@app.get("/api/games/ufc/{fight_id}/insights")
async def ufc_fight_insights(
    fight_id: str,
    date_param: str | None = Query(None, alias="date"),
    use_cache: bool = Query(False),
    refresh: bool = Query(False),
):
    from app.services.ufc_fight_insights import build_ufc_fight_insights

    game_date = date_type.fromisoformat(date_param) if date_param else None
    insights = build_ufc_fight_insights(
        fight_id,
        game_date=game_date,
        use_cache=use_cache,
        refresh=refresh,
    )
    if insights is None:
        raise HTTPException(status_code=404, detail="Fight not found")
    return insights


@app.get("/api/games/ufc/{fight_id}")
async def ufc_fight_detail(
    fight_id: str,
    date_param: str | None = Query(None, alias="date"),
):
    game_date = date_type.fromisoformat(date_param) if date_param else None
    detail = get_ufc_fight(fight_id, game_date)
    if detail is None:
        raise HTTPException(status_code=404, detail="Fight not found")
    return detail


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
async def mlb_game_page(
    game_id: str,
    date_param: str | None = Query(None, alias="date"),
    use_cache: bool = Query(True),
    refresh: bool = Query(False),
    bookmaker: str | None = Query(DEFAULT_DISPLAY_BOOKMAKER),
):
    game_date = (
        date_type.fromisoformat(date_param) if date_param else date_type.today()
    )
    cache_key = (
        f"game:{game_id}:{game_date.isoformat()}:"
        f"{bookmaker or DEFAULT_DISPLAY_BOOKMAKER}:{use_cache}:{refresh}"
    )

    async def _build():
        return await build_mlb_game_page_data(
            game_id,
            game_date,
            use_cache=use_cache,
            refresh=refresh,
            bookmaker=bookmaker,
        )

    if refresh:
        page_data = await _build()
    else:
        page_data = await _ssr_page_data(cache_key, _build)
    if page_data is None:
        raise HTTPException(status_code=404, detail="Game not found")
    return render_static_page(STATIC_DIR, "game.html", page_data)


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
async def mlb_props_page(
    request: Request,
    date_param: str | None = Query(None, alias="date"),
    bookmaker: str | None = Query(DEFAULT_DISPLAY_BOOKMAKER),
    market_type: str | None = Query(None),
    min_odds: int | None = Query(None),
    line_kind: str | None = Query(None),
    line_value: float | None = Query(None),
    side: str | None = Query(None),
    actionable_only: str | None = Query(None),
    very_strong_only: str | None = Query(None),
    include_alternates: str | None = Query(None),
    sort: str = Query("score"),
    risk: str | None = Query(None),
    min_score: int | None = Query(None),
    min_hit_l5: str | None = Query(None),
    min_hit_l10: str | None = Query(None),
    refresh: str | None = Query(None),
):
    game_date = (
        date_type.fromisoformat(date_param) if date_param else date_type.today()
    )
    qp = request.query_params
    page_data = await build_mlb_props_page_data(
        game_date,
        bookmaker=bookmaker,
        market_type=market_type or None,
        min_odds=min_odds,
        line_kind=line_kind or "main",
        line_value=line_value,
        side=side or "both",
        actionable_only=_bool_query(actionable_only or qp.get("actionable_only")),
        very_strong_only=_bool_query(very_strong_only or qp.get("very_strong_only")),
        include_alternates=_bool_query(
            include_alternates or qp.get("include_alternates")
        ),
        sort=sort,
        risk=risk or None,
        min_score=min_score,
        min_hit_l5=_pct_query(min_hit_l5),
        min_hit_l10=_pct_query(min_hit_l10),
        refresh=_bool_query(refresh),
    )
    return render_static_page(STATIC_DIR, "mlb_props.html", page_data)


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
