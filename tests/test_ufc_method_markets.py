"""Tests for UFC method/round market parsing and prop edges."""

from __future__ import annotations

from app.odds.ufc_method_markets import (
    extract_goes_distance_odds,
    extract_method_props_from_bookmakers,
    method_prop_edges,
    model_over_rounds_prob,
    round_totals_edge,
)

SAMPLE_BOOKMAKERS = [
    {
        "key": "draftkings",
        "markets": [
            {
                "key": "method_of_victory",
                "outcomes": [
                    {"name": "Johnny Walker by KO/TKO", "price": 450},
                    {"name": "Johnny Walker by Submission", "price": 900},
                    {"name": "Magomed Ankalaev by KO/TKO", "price": 220},
                    {"name": "Magomed Ankalaev by Decision", "price": 180},
                ],
            },
            {
                "key": "totals",
                "outcomes": [
                    {"name": "Over", "price": -110, "point": 2.5},
                    {"name": "Under", "price": -110, "point": 2.5},
                ],
            },
            {
                "key": "fight_goes_distance",
                "outcomes": [
                    {"name": "Yes", "price": 150},
                    {"name": "No", "price": -175},
                ],
            },
        ],
    },
    {
        "key": "fanduel",
        "markets": [
            {
                "key": "method_of_victory",
                "outcomes": [
                    {"name": "Johnny Walker by KO/TKO", "price": 475},
                    {"name": "Magomed Ankalaev by Decision", "price": 170},
                ],
            }
        ],
    },
]


def test_extract_method_props_from_bookmakers():
    props = extract_method_props_from_bookmakers(
        SAMPLE_BOOKMAKERS,
        home="Magomed Ankalaev",
        away="Johnny Walker",
    )
    assert props["fighterA_KO_TKO"] == 475  # median of 450, 475
    assert props["fighterB_KO_TKO"] == 220
    assert props["fighterB_Decision"] == 180  # median of 180, 170


def test_extract_goes_distance_odds():
    dist = extract_goes_distance_odds(SAMPLE_BOOKMAKERS)
    assert dist["goes_distance_yes"] == 150
    assert dist["goes_distance_no"] == -175


def test_method_prop_edges_flags_plus_ev():
    model = {
        "fighterA_KO_TKO": 0.28,
        "fighterB_Decision": 0.35,
    }
    market = {
        "fighterA_KO_TKO": 450,
        "fighterB_Decision": 180,
    }
    edges = method_prop_edges(model, market, min_edge=0.08)
    assert edges
    ko = next(p for p in edges if p["method_key"] == "fighterA_KO_TKO")
    assert ko["plus_ev"] is True
    assert ko["edge"] >= 0.08


def test_round_totals_edge():
    win_methods = {
        "fighterA_Decision": 0.2,
        "fighterB_Decision": 0.25,
        "fighterA_KO_TKO": 0.15,
        "fighterB_KO_TKO": 0.2,
        "fighterA_Submission": 0.05,
        "fighterB_Submission": 0.15,
    }
    over_p = model_over_rounds_prob(win_methods, totals_line=2.5)
    row = round_totals_edge(
        totals_line=2.5,
        over_odds=-110,
        under_odds=-110,
        model_over_prob=over_p,
        min_edge=0.0,
    )
    assert row is not None
    assert row["market"] == "rounds_total"
    assert row["side"] in ("over", "under")
