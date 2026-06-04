"""Cross-game MLB parlay EV ranker (independence assumption v1)."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any

import pandas as pd

from app.config import PROJECT_ROOT
from app.odds.mlb_odds_free import ODDS_2025_CSV
from app.odds.odds_math import (
    joint_probability,
    market_probs_from_american,
    parlay_decimal_payout,
    parlay_ev,
)
from app.odds.team_aliases import is_valid_american_odds, normalize_team_name
from app.odds.the_odds_api import fetch_mlb_moneylines
from app.parlay.slate import build_slate_dataframe, build_slate_from_history

logger = logging.getLogger(__name__)

PARLAYS_OUTPUT = PROJECT_ROOT / "data" / "processed" / "mlb_parlays_today.json"

from app.models.constants import DEFAULT_MIN_EDGE
DEFAULT_MAX_PARLAYS = 5
DEFAULT_MIN_LEGS = 2
DEFAULT_MAX_LEGS = 4


@dataclass
class ParlayLeg:
    game_id: str
    date: str
    matchup: str
    side: str
    team: str
    model_prob: float
    market_prob: float
    american_odds: int
    leg_edge: float


@dataclass
class RankedParlay:
    legs: list[ParlayLeg]
    num_legs: int
    model_joint_prob: float
    market_joint_prob: float
    decimal_payout: float
    ev: float
    edge_vs_market: float


def _parse_odds_api_events(events: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for event in events:
        home = normalize_team_name(event.get("home_team", ""))
        away = normalize_team_name(event.get("away_team", ""))
        commence = event.get("commence_time", "")[:10]
        home_prices: list[int] = []
        away_prices: list[int] = []
        for book in event.get("bookmakers", []):
            for market in book.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                prices = {normalize_team_name(o["name"]): int(o["price"]) for o in market["outcomes"]}
                if home in prices and away in prices:
                    if is_valid_american_odds(prices[home]) and is_valid_american_odds(
                        prices[away]
                    ):
                        home_prices.append(prices[home])
                        away_prices.append(prices[away])
        if not home_prices:
            continue
        home_ml = int(pd.Series(home_prices).median())
        away_ml = int(pd.Series(away_prices).median())
        rows.append(
            {
                "date": commence,
                "home_team": home,
                "away_team": away,
                "home_ml": home_ml,
                "away_ml": away_ml,
                "odds_source": "the_odds_api",
            }
        )
    return pd.DataFrame(rows)


def _load_cached_odds(game_date: date) -> pd.DataFrame:
    if not ODDS_2025_CSV.exists():
        return pd.DataFrame()
    odds = pd.read_csv(ODDS_2025_CSV)
    odds["date"] = pd.to_datetime(odds["date"]).dt.strftime("%Y-%m-%d")
    target = game_date.isoformat()
    day = odds[odds["date"] == target].copy()
    day["odds_source"] = "historical_cache"
    return day


def attach_market_odds(
    slate: pd.DataFrame,
    game_date: date,
    use_cache: bool = False,
) -> tuple[pd.DataFrame, str]:
    odds_df = pd.DataFrame()
    source = "none"

    if not use_cache:
        events = fetch_mlb_moneylines()
        if events:
            odds_df = _parse_odds_api_events(events)
            source = "the_odds_api"

    if odds_df.empty and (use_cache or ODDS_2025_CSV.exists()):
        odds_df = _load_cached_odds(game_date)
        if not odds_df.empty:
            source = "historical_cache"

    if odds_df.empty:
        return slate, source

    slate = slate.copy()
    slate["date_key"] = pd.to_datetime(slate["date"]).dt.strftime("%Y-%m-%d")
    odds_df = odds_df.copy()
    odds_df["home_team"] = odds_df["home_team"].map(normalize_team_name)
    odds_df["away_team"] = odds_df["away_team"].map(normalize_team_name)
    odds_df["date_key"] = pd.to_datetime(odds_df["date"]).dt.strftime("%Y-%m-%d")
    merged = slate.merge(
        odds_df,
        on=["date_key", "home_team", "away_team"],
        how="left",
        suffixes=("", "_odds"),
    )
    return merged, source


def _candidate_legs(games: pd.DataFrame) -> list[ParlayLeg]:
    legs: list[ParlayLeg] = []
    for row in games.itertuples(index=False):
        if pd.isna(row.home_ml) or pd.isna(row.away_ml):
            continue
        if not is_valid_american_odds(row.home_ml) or not is_valid_american_odds(
            row.away_ml
        ):
            continue
        market_home, market_away = market_probs_from_american(
            int(row.home_ml), int(row.away_ml)
        )
        matchup = f"{row.away_team} @ {row.home_team}"
        options = [
            (
                "home",
                row.home_team,
                float(row.model_prob_home),
                market_home,
                int(row.home_ml),
            ),
            (
                "away",
                row.away_team,
                float(row.model_prob_away),
                market_away,
                int(row.away_ml),
            ),
        ]
        side, team, model_p, market_p, am = max(
            options, key=lambda x: x[2] - x[3]
        )
        leg_edge = model_p - market_p
        if leg_edge <= 0:
            continue
        legs.append(
            ParlayLeg(
                game_id=str(row.game_id),
                date=str(row.date_key if hasattr(row, "date_key") else row.date),
                matchup=matchup,
                side=side,
                team=team,
                model_prob=model_p,
                market_prob=market_p,
                american_odds=am,
                leg_edge=leg_edge,
            )
        )
    return legs


def rank_parlays(
    legs: list[ParlayLeg],
    min_legs: int = DEFAULT_MIN_LEGS,
    max_legs: int = DEFAULT_MAX_LEGS,
    min_edge: float = DEFAULT_MIN_EDGE,
    max_parlays: int = DEFAULT_MAX_PARLAYS,
) -> list[RankedParlay]:
    ranked: list[RankedParlay] = []
    for n in range(min_legs, max_legs + 1):
        for combo in combinations(legs, n):
            game_ids = [leg.game_id for leg in combo]
            if len(set(game_ids)) < n:
                continue
            model_probs = [leg.model_prob for leg in combo]
            market_probs = [leg.market_prob for leg in combo]
            american = [leg.american_odds for leg in combo]
            model_joint = joint_probability(model_probs)
            market_joint = joint_probability(market_probs)
            decimal = parlay_decimal_payout(american)
            ev = parlay_ev(model_joint, decimal)
            if ev < min_edge:
                continue
            ranked.append(
                RankedParlay(
                    legs=list(combo),
                    num_legs=n,
                    model_joint_prob=model_joint,
                    market_joint_prob=market_joint,
                    decimal_payout=decimal,
                    ev=ev,
                    edge_vs_market=model_joint - market_joint,
                )
            )
    ranked.sort(key=lambda p: p.ev, reverse=True)
    return ranked[:max_parlays]


def format_console_table(parlays: list[RankedParlay]) -> str:
    if not parlays:
        return "No parlays met the edge threshold."
    lines = [
        f"{'Rank':<5} {'Legs':<5} {'EV':>8} {'Model P':>9} {'Mkt P':>9} {'Payout':>7}  Picks",
        "-" * 72,
    ]
    for i, parlay in enumerate(parlays, 1):
        picks = " | ".join(
            f"{leg.team} ({leg.american_odds:+d})" for leg in parlay.legs
        )
        lines.append(
            f"{i:<5} {parlay.num_legs:<5} {parlay.ev:>7.1%} "
            f"{parlay.model_joint_prob:>8.1%} {parlay.market_joint_prob:>8.1%} "
            f"{parlay.decimal_payout:>6.2f}x  {picks}"
        )
    return "\n".join(lines)


def run_parlay_ranker(
    game_date: date | None = None,
    min_edge: float = DEFAULT_MIN_EDGE,
    max_parlays: int = DEFAULT_MAX_PARLAYS,
    min_legs: int = DEFAULT_MIN_LEGS,
    max_legs: int = DEFAULT_MAX_LEGS,
    use_cache: bool = False,
) -> dict[str, Any]:
    game_date = game_date or date.today()
    if use_cache:
        slate = build_slate_from_history(game_date)
        if slate.empty:
            slate = build_slate_dataframe(game_date)
    else:
        slate = build_slate_dataframe(game_date)
    if slate.empty:
        return {
            "date": game_date.isoformat(),
            "error": "No scheduled MLB games found for this date.",
            "parlays": [],
        }

    merged, odds_source = attach_market_odds(slate, game_date, use_cache=use_cache)
    if odds_source == "none":
        return {
            "date": game_date.isoformat(),
            "error": (
                "No odds available. Set ODDS_API_KEY in .env for live lines, or run "
                "with --use-cache after scripts/load_mlb_odds_free.py for historical dates."
            ),
            "games_on_slate": len(slate),
            "parlays": [],
        }

    with_odds = merged[merged["home_ml"].notna()].copy()
    legs = _candidate_legs(with_odds)
    parlays = rank_parlays(
        legs,
        min_legs=min_legs,
        max_legs=max_legs,
        min_edge=min_edge,
        max_parlays=max_parlays,
    )

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date": game_date.isoformat(),
        "odds_source": odds_source,
        "games_on_slate": len(slate),
        "games_with_odds": len(with_odds),
        "candidate_legs": len(legs),
        "min_edge": min_edge,
        "parlays": [
            {
                **{k: v for k, v in asdict(p).items() if k != "legs"},
                "legs": [asdict(leg) for leg in p.legs],
            }
            for p in parlays
        ],
    }
    PARLAYS_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    PARLAYS_OUTPUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def run_parlay_ranker_with_message(**kwargs) -> str:
    result = run_parlay_ranker(**kwargs)
    if result.get("error"):
        return result["error"]
    parlays = [
        RankedParlay(
            legs=[ParlayLeg(**leg) for leg in p["legs"]],
            num_legs=p["num_legs"],
            model_joint_prob=p["model_joint_prob"],
            market_joint_prob=p["market_joint_prob"],
            decimal_payout=p["decimal_payout"],
            ev=p["ev"],
            edge_vs_market=p["edge_vs_market"],
        )
        for p in result.get("parlays", [])
    ]
    header = (
        f"Date: {result['date']} | Odds: {result['odds_source']} | "
        f"Slate: {result['games_on_slate']} games | "
        f"With odds: {result['games_with_odds']} | "
        f"Candidate legs: {result['candidate_legs']}"
    )
    return header + "\n\n" + format_console_table(parlays)
