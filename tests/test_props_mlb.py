"""Player props service tests."""

from datetime import date
from unittest.mock import patch

import pytest

from app.odds import odds_repository as repo
from app.services import prop_scoring, props_mlb


FAKE_EVENT = {
    "id": "evt123",
    "home_team": "New York Yankees",
    "away_team": "Boston Red Sox",
    "commence_time": "2026-06-16T23:05:00Z",
    "bookmakers": [
        {
            "key": "draftkings",
            "markets": [
                {
                    "key": "batter_hits",
                    "outcomes": [
                        {
                            "name": "Over",
                            "description": "Aaron Judge",
                            "price": -115,
                            "point": 1.5,
                        },
                        {
                            "name": "Under",
                            "description": "Aaron Judge",
                            "price": -105,
                            "point": 1.5,
                        },
                    ],
                },
                {
                    "key": "pitcher_strikeouts",
                    "outcomes": [
                        {
                            "name": "Over",
                            "description": "Gerrit Cole",
                            "price": +110,
                            "point": 5.5,
                        },
                        {
                            "name": "Under",
                            "description": "Gerrit Cole",
                            "price": -130,
                            "point": 5.5,
                        },
                    ],
                },
            ],
        },
        {
            "key": "fanduel",
            "markets": [
                {
                    "key": "batter_hits",
                    "outcomes": [
                        {
                            "name": "Over",
                            "description": "Aaron Judge",
                            "price": -120,
                            "point": 1.5,
                        },
                        {
                            "name": "Under",
                            "description": "Aaron Judge",
                            "price": -100,
                            "point": 1.5,
                        },
                    ],
                },
            ],
        },
    ],
}


@pytest.fixture
def isolated_props(tmp_path, monkeypatch):
    props_dir = tmp_path / "props_repository"
    props_dir.mkdir(parents=True, exist_ok=True)
    (props_dir / "events").mkdir(exist_ok=True)
    monkeypatch.setattr(props_mlb, "PROPS_DIR", props_dir)
    monkeypatch.setattr(props_mlb, "EVENTS_DIR", props_dir / "events")
    repo.reset_fetch_locks_for_tests()
    prop_scoring.clear_prop_scoring_cache()
    yield props_dir


def test_parse_event_props_median_odds():
    rows = props_mlb._parse_event_props(FAKE_EVENT)
    assert len(rows) == 2
    judge = next(r for r in rows if r["player"] == "Aaron Judge")
    assert judge["market_type"] == "batter_hits"
    assert judge["line"] == 1.5
    assert judge["over_odds"] == -118
    assert judge["under_odds"] == -102


def test_parse_event_props_single_book():
    rows = props_mlb._parse_event_props(FAKE_EVENT, bookmaker_key="draftkings")
    assert len(rows) == 2
    rows = props_mlb._parse_event_props(FAKE_EVENT, bookmaker_key="fanduel")
    assert len(rows) == 1
    assert rows[0]["over_odds"] == -120


def test_normalize_bookmaker_aliases():
    assert props_mlb._normalize_bookmaker("caesars") == "williamhill_us"
    assert props_mlb._normalize_bookmaker("pointsbetus") == "consensus"
    assert props_mlb._bookmaker_label("williamhill_us") == "Caesars"


def test_books_with_prop_markets():
    assert props_mlb._books_with_prop_markets(FAKE_EVENT) == ["draftkings", "fanduel"]


def test_game_pick_lists_splits_very_strong_from_top_picks():
    props = [
        {
            "actionable": True,
            "recommended_hit_rate": 1.0,
            "recommended_odds": -110,
            "line_strength": "very_strong",
            "score": 100,
        },
        {
            "actionable": True,
            "recommended_hit_rate": 0.7,
            "recommended_odds": -110,
            "line_strength": "strong",
            "score": 70,
            "hit_rate_over_l10": 0.7,
            "hit_rate_over_l5": 0.6,
            "hit_rate_over_season": 0.65,
            "recommended_side": "over",
        },
    ]
    lists = props_mlb._game_pick_lists(props)
    assert len(lists["very_strong_picks"]) == 1
    assert len(lists["top_picks"]) == 1
    assert lists["total_very_strong"] == 1


def test_split_slate_props_and_daily_payload():
    picks = [
        {"line_strength": "very_strong", "recommended_side": "over", "hit_rate_over_l10": 1.0, "hit_rate_over_l5": 1.0, "hit_rate_over_season": 1.0},
        {"line_strength": "strong", "recommended_side": "over", "hit_rate_over_l10": 0.8, "hit_rate_over_l5": 0.7, "hit_rate_over_season": 0.75},
    ]
    very, regular = props_mlb._split_slate_props(picks)
    assert len(very) == 1
    assert len(regular) == 1
    payload = props_mlb._daily_props_payload(
        game_date=date(2026, 6, 16),
        limit=10,
        picks=picks,
        source="test",
    )
    assert payload["total_very_strong"] == 1
    assert len(payload["very_strong_props"]) == 1
    assert len(payload["top_props"]) == 1
    assert payload["very_strong_props"][0]["line_strength"] == "very_strong"


def test_filter_prop_markets_excludes_runs_by_default():
    from app.odds.the_odds_api import DEFAULT_MLB_PROP_MARKETS

    rows = [
        {"market_type": "batter_runs_scored", "player": "A"},
        {"market_type": "batter_hits", "player": "B"},
    ]
    out = props_mlb._filter_prop_markets(rows, markets_requested=DEFAULT_MLB_PROP_MARKETS)
    assert len(out) == 1
    assert out[0]["market_type"] == "batter_hits"


def test_parse_event_props_alternate_line_kind():
    event = {
        **FAKE_EVENT,
        "bookmakers": [
            {
                "key": "draftkings",
                "markets": [
                    {
                        "key": "batter_hits_alternate",
                        "outcomes": [
                            {
                                "name": "Over",
                                "description": "Aaron Judge",
                                "price": 150,
                                "point": 2.5,
                            },
                            {
                                "name": "Under",
                                "description": "Aaron Judge",
                                "price": -190,
                                "point": 2.5,
                            },
                        ],
                    }
                ],
            }
        ],
    }
    rows = props_mlb._parse_event_props(event, bookmaker_key="draftkings")
    alt = next(r for r in rows if r["player"] == "Aaron Judge" and r["line"] == 2.5)
    assert alt["market_type"] == "batter_hits"
    assert alt["line_kind"] == "alternate"


def test_passes_prop_search_filters_min_odds_and_line_kind():
    prop = {
        "market_type": "batter_hits",
        "line_kind": "main",
        "line": 1.5,
        "recommended_odds": -150,
        "actionable": True,
    }
    assert props_mlb._passes_prop_search_filters(
        prop,
        market_type="batter_hits",
        min_odds=-200,
        line_kind="main",
        line_value=None,
        actionable_only=False,
    )
    assert not props_mlb._passes_prop_search_filters(
        prop,
        market_type="batter_hits",
        min_odds=-100,
        line_kind="main",
        line_value=None,
        actionable_only=False,
    )


def test_prop_rank_key_hit_rate_first():
    lower_l10 = {
        "recommended_side": "over",
        "recommended_hit_rate": 0.8,
        "hit_rate_over_l5": 0.8,
        "hit_rate_over_l10": 0.8,
        "hit_rate_over_season": 0.75,
    }
    higher_l10 = {
        "recommended_side": "over",
        "recommended_hit_rate": 1.0,
        "hit_rate_over_l5": 1.0,
        "hit_rate_over_l10": 1.0,
        "hit_rate_over_season": 0.9,
    }
    ranked = sorted([lower_l10, higher_l10], key=props_mlb.prop_rank_key)
    assert ranked[0] is higher_l10
    assert ranked[1] is lower_l10


def test_prop_rank_key_tie_break_l5_then_season():
    same_l10_a = {
        "recommended_side": "over",
        "recommended_hit_rate": 0.8,
        "hit_rate_over_l5": 0.6,
        "hit_rate_over_l10": 0.8,
        "hit_rate_over_season": 0.7,
    }
    same_l10_b = {
        "recommended_side": "over",
        "recommended_hit_rate": 0.8,
        "hit_rate_over_l5": 0.9,
        "hit_rate_over_l10": 0.8,
        "hit_rate_over_season": 0.65,
    }
    ranked = sorted([same_l10_a, same_l10_b], key=props_mlb.prop_rank_key)
    assert ranked[0] is same_l10_b
    assert ranked[1] is same_l10_a


def test_prop_is_bettable_requires_listed_side_odds():
    assert props_mlb.prop_is_bettable(
        {
            "actionable": True,
            "recommended_side": "over",
            "recommended_odds": -110,
            "over_odds": -110,
            "recommended_hit_rate": 0.7,
        }
    )
    assert not props_mlb.prop_is_bettable(
        {
            "actionable": True,
            "recommended_side": "over",
            "recommended_odds": -110,
            "over_odds": None,
            "under_odds": -105,
            "recommended_hit_rate": 0.7,
        }
    )
    assert not props_mlb.prop_is_bettable(
        {
            "actionable": True,
            "recommended_side": "under",
            "recommended_odds": -110,
            "over_odds": -110,
            "recommended_hit_rate": 0.7,
            "stale_cache": True,
        }
    )


def test_load_best_slate_props_same_day_only(isolated_props):
    import json
    from datetime import date

    slate_path = isolated_props / "slate_2026-06-16.json"
    slate_path.write_text(
        json.dumps(
            {
                "all_props": [
                    {
                        "recommended_hit_rate": 0.8,
                        "recommended_odds": -110,
                        "over_odds": -110,
                        "rank_score": 80,
                        "actionable": True,
                        "game_id": "1",
                        "player": "A",
                        "market_type": "batter_hits",
                        "line": 1.5,
                        "recommended_side": "over",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    picks, source, _ = props_mlb._load_best_slate_props(date(2026, 6, 17))
    assert source == "none"
    assert len(picks) == 0

    picks_today, source_today, _ = props_mlb._load_best_slate_props(date(2026, 6, 16))
    assert source_today == "slate_cache"
    assert len(picks_today) == 1


def test_build_daily_top_props_uses_today_slate_without_scan(isolated_props):
    import json
    from datetime import date

    (isolated_props / "slate_2026-06-17.json").write_text(
        json.dumps(
            {
                "all_props": [
                    {
                        "recommended_hit_rate": 0.75,
                        "recommended_odds": 100,
                        "over_odds": 100,
                        "rank_score": 75,
                        "actionable": True,
                        "game_id": "9",
                        "player": "B",
                        "market_type": "batter_hits",
                        "line": 0.5,
                        "recommended_side": "over",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with patch("app.services.props_mlb.get_mlb_schedule", return_value={"games": []}):
        out = props_mlb.build_daily_top_props(date(2026, 6, 17), limit=5, scan=False)
    assert out["total_actionable"] == 1
    assert out["source"] == "slate_cache"


def test_evaluate_prop_parlay():
    result = props_mlb.evaluate_prop_parlay(
        [
            {"american_odds": -110},
            {"american_odds": +120},
        ]
    )
    assert result["leg_count"] == 2
    assert result["decimal_payout"] > 3.0
    assert result["american_payout"] is not None
    assert result["profit_on_10"] > 0


@patch("app.services.props_mlb._probable_pitchers", return_value=(None, None))
@patch("app.services.props_mlb._enrich_props", side_effect=lambda props, **_: props)
@patch("app.services.props_mlb.fetch_mlb_event_props_if_allowed")
@patch("app.services.props_mlb.fetch_mlb_events_if_allowed")
@patch("app.services.props_mlb.get_mlb_game")
def test_build_game_props_fetches_and_caches(
    mock_game,
    mock_events,
    mock_props,
    _enrich,
    _pitchers,
    isolated_props,
):
    mock_game.return_value = {
        "game": {
            "game_id": "777001",
            "home_team": "New York Yankees",
            "away_team": "Boston Red Sox",
        }
    }
    mock_events.return_value = repo.ApiFetchResult(
        events=[{"id": "evt123", "home_team": "New York Yankees", "away_team": "Boston Red Sox"}],
        source="the_odds_api_live",
    )
    mock_props.return_value = repo.ApiFetchResult(
        events=[FAKE_EVENT],
        source="the_odds_api_live",
    )

    payload = props_mlb.build_game_props("777001", game_date=date(2026, 6, 16), refresh=True)
    assert payload is not None
    assert payload["status"] == "ok"
    assert len(payload["props"]) == 2
    assert payload["bookmaker"] == "consensus"
    assert (isolated_props / "777001.consensus.json").exists()

    mock_props.reset_mock()
    cached = props_mlb.build_game_props("777001", game_date=date(2026, 6, 16), refresh=False)
    assert cached["props"]
    mock_props.assert_not_called()

    mock_props.return_value = repo.ApiFetchResult(
        events=[FAKE_EVENT],
        source="the_odds_api_live",
    )
    dk_payload = props_mlb.build_game_props(
        "777001",
        game_date=date(2026, 6, 16),
        refresh=True,
        bookmaker="draftkings",
    )
    assert dk_payload["bookmaker"] == "draftkings"
    assert (isolated_props / "777001.draftkings.json").exists()
    assert (isolated_props / "raw_events" / "777001.2026-06-16.json").exists()
    mock_props.assert_called_once()
    assert mock_props.call_args.kwargs.get("bookmakers") is None

    mock_props.reset_mock()
    fd_payload = props_mlb.build_game_props(
        "777001",
        game_date=date(2026, 6, 16),
        refresh=False,
        bookmaker="fanduel",
    )
    assert fd_payload["bookmaker"] == "fanduel"
    assert len(fd_payload["props"]) == 1
    mock_props.assert_not_called()


@patch("app.services.prop_scoring._search_player_id", return_value=592450)
@patch("app.services.prop_scoring._season_game_log_values")
def test_score_prop_recommends_over_on_hot_form(mock_logs, _pid):
    mock_logs.return_value = tuple([2, 2, 1, 3, 2, 2, 1, 2, 3, 2])
    result = prop_scoring.score_prop(
        player="Aaron Judge",
        market_type="batter_hits",
        line=1.5,
        over_odds=-110,
        under_odds=-110,
        season=2026,
    )
    assert result["recommended_side"] == "over"
    assert result["actionable"] is True
    assert result["score"] is not None
    assert result["hit_rate_over_l10"] >= 0.5
    assert result["sample_games_season"] == 10
    assert result["line_strength"] in ("very_strong", "strong", "moderate", "weak")
    assert result["line_strength_label"]


@patch("app.services.prop_scoring._search_player_id", return_value=592450)
@patch("app.services.prop_scoring._season_game_log_values")
def test_score_prop_very_strong_on_perfect_l5_l10_season(mock_logs, _pid):
    mock_logs.return_value = tuple([2, 2, 2, 2, 2, 2, 2, 2, 2, 2])
    result = prop_scoring.score_prop(
        player="Aaron Judge",
        market_type="batter_hits",
        line=1.5,
        over_odds=-115,
        under_odds=+105,
        season=2026,
    )
    assert result["actionable"] is True
    assert result["hit_rate_over_l5"] == 1.0
    assert result["hit_rate_over_l10"] == 1.0
    assert result["hit_rate_over_season"] == 1.0
    assert result["line_strength"] == "very_strong"
    assert result["line_strength_label"] == "Very strong"
    assert "L5" in (result["line_insight"] or "")
    assert "-115" in (result["line_insight"] or "")


@patch("app.services.prop_scoring._search_player_id", return_value=592450)
@patch("app.services.prop_scoring._season_game_log_values")
def test_score_prop_not_very_strong_when_l10_imperfect(mock_logs, _pid):
    mock_logs.return_value = tuple([2, 2, 2, 2, 2, 2, 2, 2, 1, 2])
    result = prop_scoring.score_prop(
        player="Aaron Judge",
        market_type="batter_hits",
        line=1.5,
        over_odds=-115,
        under_odds=+105,
        season=2026,
    )
    assert result["hit_rate_over_l10"] == 0.9
    assert result["line_strength"] != "very_strong"


@patch("app.services.prop_scoring._search_player_id", return_value=592450)
@patch("app.services.prop_scoring._season_game_log_values")
def test_refresh_prop_line_strength_upgrades_stale_strong_label(mock_logs, _pid):
    mock_logs.return_value = tuple([2, 2, 2, 2, 2, 2, 2, 2, 2, 2])
    scored = prop_scoring.score_prop(
        player="Aaron Judge",
        market_type="batter_hits",
        line=1.5,
        over_odds=-115,
        under_odds=+105,
        season=2026,
    )
    stale = {**scored, "line_strength": "strong", "line_strength_label": "Strong line"}
    fixed = prop_scoring.refresh_prop_line_strength(stale)
    assert fixed["line_strength"] == "very_strong"
    assert fixed["line_strength_label"] == "Very strong"


def test_form_score_is_l10_hit_rate_percent():
    assert prop_scoring._compute_rank_score(hit_rate=0.8) == 80.0
    assert prop_scoring._compute_rank_score(hit_rate=1.0) == 100.0


@patch("app.services.prop_scoring._search_player_id", return_value=592450)
@patch("app.services.prop_scoring._season_game_log_values")
def test_score_prop_marks_trap_when_only_wrong_side_listed(mock_logs, _pid):
    # All zeros — under hits 100%, only over offered
    mock_logs.return_value = tuple([0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
    result = prop_scoring.score_prop(
        player="Aaron Judge",
        market_type="batter_home_runs",
        line=0.5,
        over_odds=-200,
        under_odds=None,
        season=2026,
    )
    assert result["actionable"] is False
    assert result["score"] is None
    assert "only Over is listed" in (result["actionable_reason"] or "")
