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
                            "link": "https://sportsbook.draftkings.com/addToBetslip?outcomes=dk-judge-over",
                        },
                        {
                            "name": "Under",
                            "description": "Aaron Judge",
                            "price": -105,
                            "point": 1.5,
                            "link": "https://sportsbook.draftkings.com/addToBetslip?outcomes=dk-judge-under",
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
                            "link": "https://sportsbook.fanduel.com/addToBetslip?selectionId=fd-judge-over",
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
    assert all(r["complete_market"] for r in rows)
    rows = props_mlb._parse_event_props(FAKE_EVENT, bookmaker_key="fanduel")
    assert len(rows) == 1
    assert rows[0]["over_odds"] == -120
    assert rows[0]["complete_market"] is True


def test_consensus_excludes_stitched_one_sided_books():
    event = {
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
                        ],
                    }
                ],
            },
            {
                "key": "fanduel",
                "markets": [
                    {
                        "key": "batter_hits",
                        "outcomes": [
                            {
                                "name": "Under",
                                "description": "Aaron Judge",
                                "price": -100,
                                "point": 1.5,
                            },
                        ],
                    }
                ],
            },
        ],
    }
    assert props_mlb._parse_event_props(event) == []
    dk_rows = props_mlb._parse_event_props(event, bookmaker_key="draftkings")
    assert dk_rows == []


def test_collapse_keeps_real_main_line_not_ladder():
    """Odds API embeds ladder lines in batter_hits — keep only the real main line."""
    event = {
        "bookmakers": [
            {
                "key": "draftkings",
                "markets": [
                    {
                        "key": "batter_hits",
                        "outcomes": [
                            {
                                "name": "Over",
                                "description": "Carter Jensen",
                                "price": -220,
                                "point": 0.5,
                            },
                            {
                                "name": "Under",
                                "description": "Carter Jensen",
                                "price": +170,
                                "point": 0.5,
                            },
                            {
                                "name": "Over",
                                "description": "Carter Jensen",
                                "price": +350,
                                "point": 2.5,
                            },
                        ],
                    }
                ],
            },
            {
                "key": "hardrockbet",
                "markets": [
                    {
                        "key": "batter_hits",
                        "outcomes": [
                            {
                                "name": "Over",
                                "description": "Carter Jensen",
                                "price": +125,
                                "point": 2.5,
                            },
                        ],
                    }
                ],
            },
        ],
    }
    dk_rows = props_mlb._parse_event_props(event, bookmaker_key="draftkings")
    assert len(dk_rows) == 1
    assert dk_rows[0]["line"] == 0.5
    assert dk_rows[0]["complete_market"] is True
    consensus = props_mlb._parse_event_props(event)
    assert len(consensus) == 1
    assert consensus[0]["line"] == 0.5
    assert all(r["line"] != 2.5 for r in consensus)


def test_draftkings_excludes_obscure_book_total_bases_ladder():
    event = {
        "bookmakers": [
            {
                "key": "fliff",
                "markets": [
                    {
                        "key": "batter_total_bases",
                        "outcomes": [
                            {
                                "name": "Under",
                                "description": "Nick Loftin",
                                "price": -140,
                                "point": 4.5,
                            },
                        ],
                    }
                ],
            },
            {
                "key": "draftkings",
                "markets": [
                    {
                        "key": "batter_total_bases",
                        "outcomes": [
                            {
                                "name": "Over",
                                "description": "Nick Loftin",
                                "price": -115,
                                "point": 0.5,
                            },
                            {
                                "name": "Under",
                                "description": "Nick Loftin",
                                "price": -105,
                                "point": 0.5,
                            },
                        ],
                    }
                ],
            },
        ],
    }
    dk_rows = props_mlb._parse_event_props(event, bookmaker_key="draftkings")
    assert len(dk_rows) == 1
    assert dk_rows[0]["line"] == 0.5
    assert all(r["line"] != 4.5 for r in dk_rows)
    consensus = props_mlb._parse_event_props(event)
    assert len(consensus) == 1
    assert consensus[0]["line"] == 0.5
    assert all(r["line"] != 4.5 for r in consensus)


def test_wipe_props_bet_cache_clears_repo(isolated_props, monkeypatch):
    import json
    from datetime import date

    monkeypatch.setattr(props_mlb, "PROPS_CACHE_GENERATION", "test-gen")
    (isolated_props / "824097.draftkings.json").write_text(
        json.dumps({"props": [{"player": "A"}]}), encoding="utf-8"
    )
    (isolated_props / "slate_2026-06-19.draftkings.json").write_text("{}", encoding="utf-8")
    meta = props_mlb.wipe_props_bet_cache()
    assert meta["requires_refresh"] is True
    assert meta["generation"] == "test-gen"
    assert not (isolated_props / "824097.draftkings.json").exists()
    assert props_mlb.get_props_cache_meta()["requires_refresh"] is True


def test_ensure_props_cache_generation_wipes_on_version_change(isolated_props, monkeypatch):
    monkeypatch.setattr(props_mlb, "PROPS_CACHE_GENERATION", "v2")
    props_mlb._write_json(
        props_mlb.PROPS_CACHE_META_PATH,
        {"generation": "v1", "requires_refresh": False},
    )
    (isolated_props / "822723.json").write_text("{}", encoding="utf-8")
    wiped = props_mlb.ensure_props_cache_generation()
    assert wiped is not None
    assert not (isolated_props / "822723.json").exists()
    assert props_mlb.get_props_cache_meta()["generation"] == "v2"


def test_resolve_bookmaker_defaults_to_draftkings():
    assert props_mlb._resolve_bookmaker(None) == "draftkings"
    assert props_mlb._resolve_bookmaker("") == "draftkings"
    assert props_mlb._resolve_bookmaker("consensus") == "consensus"


def test_revalidate_pick_list_drops_unpublished_cached_line():
    import json
    from datetime import date
    from pathlib import Path

    raw_path = Path("data/processed/props_repository/raw_events/824097.2026-06-19.json")
    if not raw_path.exists():
        pytest.skip("raw event fixture missing")
    fake = {
        "game_id": "824097",
        "player": "Carter Jensen",
        "market_type": "batter_hits",
        "line": 2.5,
        "recommended_side": "under",
        "recommended_odds": -110,
        "over_odds": 120,
        "under_odds": -110,
        "complete_market": True,
        "offered_books": ["draftkings"],
        "actionable": True,
    }
    real = {
        "game_id": "824097",
        "player": "Carter Jensen",
        "market_type": "batter_hits",
        "line": 0.5,
        "recommended_side": "under",
        "recommended_odds": 176,
        "over_odds": -238,
        "under_odds": 176,
        "complete_market": True,
        "offered_books": ["draftkings"],
        "actionable": True,
    }
    out = props_mlb._revalidate_pick_list(
        [fake, real],
        "draftkings",
        date(2026, 6, 19),
    )
    assert len(out) == 1
    assert out[0]["line"] == 0.5


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
            "over_odds": -110,
            "under_odds": -105,
            "complete_market": True,
            "primary_line": True,
            "recommended_side": "over",
            "confidence_tier": "very_strong",
            "line_strength": "very_strong",
            "prop_score": 87,
            "score": 87,
        },
        {
            "actionable": True,
            "recommended_hit_rate": 0.7,
            "recommended_odds": -110,
            "over_odds": -110,
            "under_odds": -105,
            "complete_market": True,
            "primary_line": True,
            "line_strength": "strong",
            "confidence_tier": "strong",
            "prop_score": 82,
            "score": 82,
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
        {"confidence_tier": "elite", "line_strength": "elite", "actionable": True, "prop_score": 92, "score": 92, "recommended_side": "over", "hit_rate_over_l10": 1.0},
        {"confidence_tier": "very_strong", "line_strength": "very_strong", "actionable": True, "prop_score": 87, "score": 87, "recommended_side": "over", "hit_rate_over_l10": 1.0, "hit_rate_over_l5": 1.0, "hit_rate_over_season": 1.0},
        {"confidence_tier": "strong", "line_strength": "strong", "actionable": True, "prop_score": 82, "score": 82, "recommended_side": "over", "hit_rate_over_l10": 0.8, "hit_rate_over_l5": 0.7, "hit_rate_over_season": 0.75},
    ]
    elite, very, regular = props_mlb._split_slate_props(picks)
    assert len(elite) == 1
    assert len(very) == 1
    assert len(regular) == 1
    payload = props_mlb._daily_props_payload(
        game_date=date(2026, 6, 16),
        limit=10,
        picks=picks,
        source="test",
        elite_props=elite,
        very_strong_props=very,
        top_props=regular,
    )
    assert payload["total_elite"] == 1
    assert payload["total_very_strong"] == 1
    assert len(payload["elite_props"]) == 1
    assert len(payload["very_strong_props"]) == 1
    assert len(payload["top_props"]) == 1


def test_sample_props_for_scoring_includes_low_count_markets():
    props = [
        {"market_type": "batter_hits", "player": f"P{i}", "line": 0.5} for i in range(100)
    ]
    props += [
        {"market_type": "batter_rbis", "player": f"R{i}", "line": 0.5} for i in range(20)
    ]
    sampled = props_mlb._sample_props_for_scoring(props, 80)
    assert len(sampled) == 80
    assert any(p["market_type"] == "batter_rbis" for p in sampled)


def test_enrich_props_no_cap_keeps_all_markets():
    props = [
        {"market_type": "batter_hits", "player": f"P{i}", "line": 0.5, "over_odds": -110, "under_odds": -110}
        for i in range(100)
    ]
    props += [
        {"market_type": "batter_rbis", "player": f"R{i}", "line": 0.5, "over_odds": -110, "under_odds": -110}
        for i in range(15)
    ]
    with patch("app.services.props_mlb.score_prop", side_effect=lambda **kw: {"actionable": False}):
        with patch("app.services.props_mlb.warm_scoring_cache"):
            enriched = props_mlb._enrich_props(
                props,
                season=2026,
                away_pitcher=None,
                home_pitcher=None,
                away_team_id=None,
                home_team_id=None,
                max_lines=None,
            )
    assert len(enriched) == 115
    assert any(p["market_type"] == "batter_rbis" for p in enriched)


def test_markets_for_fetch_extended_includes_pitcher_markets():
    extended = props_mlb._markets_for_fetch(include_alternates=False, include_all_markets=True)
    assert "batter_rbis" in extended
    assert "pitcher_hits_allowed" in extended
    assert "pitcher_outs" in extended


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


def test_prop_rank_key_prop_score_first():
    lower = {"prop_score": 80, "edge_pct": 5, "recommended_side": "over", "hit_rate_over_l10": 0.9}
    higher = {"prop_score": 90, "edge_pct": 3, "recommended_side": "over", "hit_rate_over_l10": 0.6}
    ranked = sorted([lower, higher], key=props_mlb.prop_rank_key)
    assert ranked[0] is higher
    assert ranked[1] is lower


def test_prop_rank_key_tie_break_edge_then_form():
    same_score_a = {
        "prop_score": 85,
        "edge_pct": 6,
        "recommended_side": "over",
        "hit_rate_over_l10": 0.8,
    }
    same_score_b = {
        "prop_score": 85,
        "edge_pct": 9,
        "recommended_side": "over",
        "hit_rate_over_l10": 0.7,
    }
    ranked = sorted([same_score_a, same_score_b], key=props_mlb.prop_rank_key)
    assert ranked[0] is same_score_b
    assert ranked[1] is same_score_a


def test_prop_is_bettable_requires_listed_side_odds():
    assert props_mlb.prop_is_bettable(
        {
            "actionable": True,
            "recommended_side": "over",
            "recommended_odds": -110,
            "over_odds": -110,
            "under_odds": -105,
            "complete_market": True,
            "recommended_hit_rate": 0.7,
        }
    )
    assert not props_mlb.prop_is_bettable(
        {
            "actionable": True,
            "recommended_side": "over",
            "recommended_odds": -110,
            "over_odds": -110,
            "under_odds": -105,
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
            "complete_market": True,
            "recommended_hit_rate": 0.7,
        }
    )
    assert not props_mlb.prop_is_bettable(
        {
            "actionable": True,
            "recommended_side": "under",
            "recommended_odds": -110,
            "over_odds": -110,
            "under_odds": -105,
            "complete_market": True,
            "recommended_hit_rate": 0.7,
            "stale_cache": True,
        }
    )


def test_load_best_slate_props_same_day_only(isolated_props):
    import json
    from datetime import date, datetime, timezone

    slate_path = isolated_props / "slate_2026-06-16.json"
    slate_path.write_text(
        json.dumps(
            {
                "date": "2026-06-16",
                "cached_at": datetime.now(timezone.utc).isoformat(),
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
                        "complete_market": True,
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
    from datetime import date, datetime, timezone

    (isolated_props / "slate_2026-06-17.draftkings.json").write_text(
        json.dumps(
            {
                "date": "2026-06-17",
                "cached_at": datetime.now(timezone.utc).isoformat(),
                "all_props": [
                    {
                        "recommended_hit_rate": 0.75,
                        "recommended_odds": 100,
                        "over_odds": 100,
                        "under_odds": -120,
                        "rank_score": 75,
                        "actionable": True,
                        "game_id": "9",
                        "player": "B",
                        "market_type": "batter_hits",
                        "line": 0.5,
                        "recommended_side": "over",
                        "complete_market": True,
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
    assert out["bookmaker"] == "draftkings"


def test_load_best_slate_props_rejects_stale_cache(isolated_props):
    import json
    from datetime import date, datetime, timedelta, timezone

    stale_at = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    (isolated_props / "slate_2026-06-17.draftkings.json").write_text(
        json.dumps(
            {
                "date": "2026-06-17",
                "cached_at": stale_at,
                "all_props": [{"player": "A", "game_id": "1", "actionable": True}],
            }
        ),
        encoding="utf-8",
    )
    picks, source, _ = props_mlb._load_best_slate_props(date(2026, 6, 17), "draftkings")
    assert source == "stale_slate_cache"
    assert picks == []


def test_load_cached_game_props_rejects_wrong_date(isolated_props):
    import json
    from datetime import date, datetime, timezone

    path = isolated_props / "777001.draftkings.json"
    path.write_text(
        json.dumps(
            {
                "date": "2026-06-16",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "props": [{"player": "A", "market_type": "batter_hits", "line": 0.5}],
            }
        ),
        encoding="utf-8",
    )
    assert props_mlb._load_cached_game_props(
        "777001", "draftkings", game_date=date(2026, 6, 17)
    ) is None
    assert props_mlb._load_cached_game_props(
        "777001", "draftkings", game_date=date(2026, 6, 16)
    ) is not None


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


def test_parse_event_props_preserves_deeplinks():
    rows = props_mlb._parse_event_props(FAKE_EVENT, bookmaker_key="draftkings")
    judge = next(r for r in rows if r["player"] == "Aaron Judge")
    assert judge["over_link"] == "https://sportsbook.draftkings.com/addToBetslip?outcomes=dk-judge-over"


def test_build_combined_parlay_deeplink():
    legs = [
        {"deeplink": "https://sportsbook.draftkings.com/addToBetslip?outcomes=dk-a"},
        {"deeplink": "https://sportsbook.draftkings.com/addToBetslip?outcomes=dk-b"},
    ]
    url = props_mlb.build_combined_parlay_deeplink(legs, "draftkings")
    assert url is not None
    assert "outcomes=dk-a" in url
    assert "outcomes=dk-b" in url
    assert props_mlb.build_combined_parlay_deeplink(legs, "fanduel") is None


def test_merge_deeplinks_into_props():
    rows = props_mlb._parse_event_props(FAKE_EVENT, bookmaker_key="draftkings")
    cached = [{**row, "score": 80} for row in rows]
    for prop in cached:
        prop.pop("over_link", None)
        prop.pop("under_link", None)
    merged = props_mlb._merge_deeplinks_into_props(cached, rows)
    judge = next(r for r in merged if r["player"] == "Aaron Judge")
    assert judge["over_link"] == "https://sportsbook.draftkings.com/addToBetslip?outcomes=dk-judge-over"
    assert judge["score"] == 80


@patch("app.services.props_mlb.build_game_props")
def test_export_slip_parlay_deeplink_for_multi_leg(mock_build):
    props = props_mlb._parse_event_props(FAKE_EVENT, bookmaker_key="draftkings")
    for row in props:
        if row["player"] == "Gerrit Cole":
            row["over_link"] = "https://sportsbook.draftkings.com/addToBetslip?outcomes=dk-cole-over"
    mock_build.return_value = {"props": props}
    legs = [
        {
            "game_id": "777001",
            "player": "Aaron Judge",
            "market_type": "batter_hits",
            "side": "over",
            "line": 1.5,
            "american_odds": -115,
        },
        {
            "game_id": "777001",
            "player": "Gerrit Cole",
            "market_type": "pitcher_strikeouts",
            "side": "over",
            "line": 5.5,
            "american_odds": 110,
        },
    ]
    out = props_mlb.export_slip_for_bookmaker(legs, "draftkings", refresh_links=False)
    assert out["open_strategy"] == "parlay"
    assert out["parlay_deeplink"] is not None
    assert "outcomes=dk-judge-over" in out["parlay_deeplink"]
    assert "outcomes=dk-cole-over" in out["parlay_deeplink"]


def test_load_game_props_for_export_uses_cached_props_off_schedule(isolated_props):
    link_rows = props_mlb._parse_event_props(FAKE_EVENT, bookmaker_key="draftkings")
    cache_path = isolated_props / "777001.draftkings.json"
    props_mlb._write_json(
        cache_path,
        {
            "game_id": "777001",
            "date": "2026-06-19",
            "props": link_rows,
            "status": "ok",
        },
    )
    leg = {"game_id": "777001", "game_date": "2026-06-19"}
    loaded = props_mlb._load_game_props_for_export(
        "777001", "draftkings", refresh_links=False, leg=leg
    )
    assert len(loaded) == 2
    assert props_mlb._props_have_deeplinks(loaded)


@patch("app.services.props_mlb._load_raw_event")
@patch("app.services.props_mlb.build_game_props")
def test_export_backfills_deeplinks_from_raw_event(mock_build, mock_raw):
    link_rows = props_mlb._parse_event_props(FAKE_EVENT, bookmaker_key="draftkings")
    cached = [{**row, "score": 70} for row in link_rows]
    for prop in cached:
        prop.pop("over_link", None)
        prop.pop("under_link", None)
    mock_build.return_value = {"props": cached, "date": "2026-06-16"}
    mock_raw.return_value = {"event": FAKE_EVENT, "fetched_at": "2026-06-16T12:00:00+00:00"}
    slip_leg = {
        "game_id": "777001",
        "player": "Aaron Judge",
        "market_type": "batter_hits",
        "market_label": "Hits",
        "side": "over",
        "line": 1.5,
        "american_odds": -115,
    }
    out = props_mlb.export_slip_for_bookmaker([slip_leg], "draftkings", refresh_links=False)
    assert out["can_open_in_book"] is True
    assert "draftkings.com" in out["legs"][0]["deeplink"]
    mock_build.assert_called_once()


@patch("app.services.props_mlb.get_mlb_game")
@patch("app.services.props_mlb._find_raw_event_any_date", return_value=None)
@patch("app.services.props_mlb._load_cached_game_props", return_value=None)
@patch("app.services.props_mlb.build_game_props")
def test_export_refreshes_props_when_cache_lacks_links(
    mock_build, _mock_cache, _mock_raw_any, mock_game
):
    mock_game.return_value = {"game": {"game_id": "777001"}}
    link_rows = props_mlb._parse_event_props(FAKE_EVENT, bookmaker_key="draftkings")
    cached = [{**row} for row in link_rows]
    for prop in cached:
        prop.pop("over_link", None)
        prop.pop("under_link", None)

    def _build(game_id, bookmaker=None, refresh=False, **kwargs):
        if refresh:
            return {"props": link_rows}
        return {"props": cached, "date": "2026-06-16"}

    mock_build.side_effect = _build
    slip_leg = {
        "game_id": "777001",
        "player": "Aaron Judge",
        "market_type": "batter_hits",
        "market_label": "Hits",
        "side": "over",
        "line": 1.5,
        "american_odds": -115,
        "game_date": "2026-06-16",
    }
    out = props_mlb.export_slip_for_bookmaker([slip_leg], "draftkings")
    assert out["can_open_in_book"] is True
    assert any(c.kwargs.get("refresh") for c in mock_build.call_args_list)


@patch("app.services.props_mlb.build_game_props")
def test_export_slip_for_bookmaker_reprices_at_target_book(mock_build):
    mock_build.return_value = {
        "props": props_mlb._parse_event_props(FAKE_EVENT, bookmaker_key="fanduel"),
    }
    slip_leg = {
        "id": "777001|Aaron Judge|batter_hits|1.5|over",
        "game_id": "777001",
        "matchup": "Boston Red Sox @ New York Yankees",
        "player": "Aaron Judge",
        "market_type": "batter_hits",
        "market_label": "Hits",
        "side": "over",
        "line": 1.5,
        "american_odds": -115,
    }
    out = props_mlb.export_slip_for_bookmaker([slip_leg], "fanduel")
    assert out["bookmaker"] == "fanduel"
    assert out["missing_count"] == 0
    assert out["legs"][0]["american_odds"] == -120
    assert out["legs"][0]["deeplink"] == "https://sportsbook.fanduel.com/addToBetslip?selectionId=fd-judge-over"
    assert out["can_open_in_book"] is True
    assert out["open_strategy"] == "single"
    assert "FanDuel" in out["export_text"]
    assert "DraftKings: Parlay" not in out["export_text"]
    assert "FanDuel:" in out["export_text"]


@patch("app.services.props_mlb.build_game_props")
def test_export_slip_for_bookmaker_marks_missing_legs(mock_build):
    mock_build.return_value = {"props": []}
    slip_leg = {
        "game_id": "777001",
        "player": "Aaron Judge",
        "market_type": "batter_hits",
        "market_label": "Hits",
        "side": "over",
        "line": 1.5,
        "american_odds": -115,
    }
    out = props_mlb.export_slip_for_bookmaker([slip_leg], "draftkings")
    assert out["missing_count"] == 1
    assert out["legs"][0]["available_at_book"] is False
    assert "NOT AT BOOK" in out["export_text"]


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
    assert payload["bookmaker"] == "draftkings"
    assert (isolated_props / "777001.draftkings.json").exists()

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
    assert mock_props.call_args.kwargs.get("bookmakers") == "draftkings"

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


@patch("app.services.prop_engine.evaluate._search_player_id", return_value=592450)
@patch("app.services.prop_engine.evaluate._season_game_log_values")
@patch("app.services.prop_engine.evaluate._alltime_game_log_values")
def test_score_prop_recommends_over_on_hot_form(mock_alltime, mock_logs, _pid):
    mock_logs.return_value = tuple([2, 2, 1, 3, 2, 2, 1, 2, 3, 2])
    mock_alltime.return_value = mock_logs.return_value
    result = prop_scoring.score_prop(
        player="Aaron Judge",
        market_type="batter_hits",
        line=1.5,
        over_odds=-110,
        under_odds=+120,
        season=2026,
    )
    assert result["prop_score_over"] is not None
    assert result["model_projection"] is not None
    assert result["hit_rate_over_l10"] >= 0.5
    assert result["sample_games_season"] == 10
    if result["actionable"]:
        assert result["recommended_side"] == "over"
        assert result["score"] is not None


@patch("app.services.prop_engine.evaluate._search_player_id", return_value=592450)
@patch("app.services.prop_engine.evaluate._season_game_log_values")
@patch("app.services.prop_engine.evaluate._alltime_game_log_values")
def test_score_prop_very_strong_on_perfect_l5_l10_season(mock_alltime, mock_logs, _pid):
    mock_logs.return_value = tuple([2, 2, 2, 2, 2, 2, 2, 2, 2, 2])
    mock_alltime.return_value = mock_logs.return_value
    result = prop_scoring.score_prop(
        player="Aaron Judge",
        market_type="batter_hits",
        line=1.5,
        over_odds=-115,
        under_odds=+130,
        season=2026,
    )
    assert result["hit_rate_over_l5"] == 1.0
    assert result["hit_rate_over_l10"] == 1.0
    assert result["hit_rate_over_season"] == 1.0
    assert result["prop_score_over"] >= result["prop_score_under"]


@patch("app.services.prop_engine.evaluate._search_player_id", return_value=592450)
@patch("app.services.prop_engine.evaluate._season_game_log_values")
@patch("app.services.prop_engine.evaluate._alltime_game_log_values")
def test_score_prop_not_very_strong_when_l10_imperfect(mock_alltime, mock_logs, _pid):
    mock_logs.return_value = tuple([2, 2, 2, 2, 2, 2, 2, 2, 1, 2])
    mock_alltime.return_value = mock_logs.return_value
    result = prop_scoring.score_prop(
        player="Aaron Judge",
        market_type="batter_hits",
        line=1.5,
        over_odds=-115,
        under_odds=+105,
        season=2026,
    )
    assert result["hit_rate_over_l10"] == 0.9
    assert result["confidence_tier"] != "elite"


@patch("app.services.prop_engine.evaluate._search_player_id", return_value=592450)
@patch("app.services.prop_engine.evaluate._season_game_log_values")
@patch("app.services.prop_engine.evaluate._alltime_game_log_values")
def test_refresh_prop_line_strength_upgrades_stale_strong_label(mock_alltime, mock_logs, _pid):
    mock_logs.return_value = tuple([2, 2, 2, 2, 2, 2, 2, 2, 2, 2])
    mock_alltime.return_value = mock_logs.return_value
    scored = prop_scoring.score_prop(
        player="Aaron Judge",
        market_type="batter_hits",
        line=1.5,
        over_odds=-115,
        under_odds=+130,
        season=2026,
    )
    stale = {**scored, "line_strength": "strong", "line_strength_label": "Strong line", "confidence_tier": "very_strong"}
    fixed = prop_scoring.refresh_prop_line_strength(stale)
    assert fixed["line_strength"] == "very_strong"
    assert fixed["line_strength_label"] == "Very strong"


def test_form_score_is_average_hit_rate_percent():
    assert prop_scoring._compute_rank_score(hit_rate=0.8, l5=0.8, season=0.75) == round(
        (0.8 + 0.8 + 0.75) / 3 * 100.0, 1
    )


def test_recent_game_window_uses_most_recent_games():
    from app.services.prop_engine.utils import recent_game_window

    values = [0] + [2] * 14
    assert recent_game_window(values, 5) == [2, 2, 2, 2, 2]
    assert recent_game_window(values, 10) == [2] * 10


@patch("app.services.prop_engine.evaluate._search_player_id", return_value=592450)
@patch("app.services.prop_engine.evaluate._season_game_log_values")
@patch("app.services.prop_engine.evaluate._alltime_game_log_values")
def test_score_prop_l5_l10_use_most_recent_games(mock_alltime, mock_logs, _pid):
    mock_logs.return_value = tuple([0] + [2] * 14)
    mock_alltime.return_value = mock_logs.return_value
    result = prop_scoring.score_prop(
        player="Aaron Judge",
        market_type="batter_hits",
        line=1.5,
        over_odds=-115,
        under_odds=+105,
        season=2026,
    )
    assert result["hit_rate_over_l5"] == 1.0
    assert result["hit_rate_over_l10"] == 1.0
    assert result["hit_rate_over_season"] == round(14 / 15, 3)


@patch("app.services.prop_engine.evaluate._search_player_id", return_value=592450)
@patch("app.services.prop_engine.evaluate._season_game_log_values")
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
