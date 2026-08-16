"""Preseason conference-favorite blend and tie-breaks."""

from app.services.cfb_preseason import (
    blend_scores,
    pick_conference_favorite,
    preseason_strength,
)


def test_close_race_prefers_higher_returning_production():
    components = {
        "Miami": {"sp": 21.6, "fpi": 11.2, "talent": 868.0, "elo": 1503.0, "ret": 0.47, "coach_chg": 0.0},
        "Clemson": {"sp": 13.3, "fpi": 13.9, "talent": 925.0, "elo": 1569.0, "ret": 0.77, "coach_chg": 0.0},
        "SMU": {"sp": 17.5, "fpi": 13.8, "talent": 788.0, "elo": 1568.0, "ret": 0.92, "coach_chg": 0.0},
        "Louisville": {"sp": 10.0, "fpi": 8.0, "talent": 700.0, "elo": 1520.0, "ret": 0.40, "coach_chg": 0.0},
    }
    scores = {"Miami": 1.42, "Clemson": 1.33, "SMU": 1.23, "Louisville": 0.20}
    assert pick_conference_favorite(list(components), scores, components) == "Clemson"


def test_sp_plus_outlier_beats_talent_gap():
    components = {
        "Ohio State": {"sp": 30.1, "fpi": 24.8, "talent": 974.0, "elo": 1628.0, "ret": 0.31, "coach_chg": 0.0},
        "Oregon": {"sp": 25.9, "fpi": 21.1, "talent": 941.0, "elo": 1648.0, "ret": 0.19, "coach_chg": 0.0},
        "Indiana": {"sp": 32.4, "fpi": 18.7, "talent": 645.0, "elo": 1522.0, "ret": 0.25, "coach_chg": 0.0},
        "Penn State": {"sp": 18.0, "fpi": 16.0, "talent": 880.0, "elo": 1580.0, "ret": 0.40, "coach_chg": 0.0},
    }
    scores = blend_scores(components)
    assert pick_conference_favorite(list(components), scores, components) == "Indiana"


def test_sp_plus_outlier_does_not_steal_below_threshold():
    components = {
        "Georgia": {"sp": 24.3, "fpi": 24.8, "talent": 1007.0, "elo": 1623.0, "ret": 0.60, "coach_chg": 0.0},
        "Alabama": {"sp": 25.0, "fpi": 24.9, "talent": 1018.0, "elo": 1606.0, "ret": 0.60, "coach_chg": 0.0},
        "Texas": {"sp": 24.1, "fpi": 23.2, "talent": 954.0, "elo": 1590.0, "ret": 0.47, "coach_chg": 0.0},
        "Ole Miss": {"sp": 18.0, "fpi": 16.0, "talent": 850.0, "elo": 1550.0, "ret": 0.40, "coach_chg": 0.0},
    }
    scores = {"Georgia": 1.36, "Texas": 1.11, "Alabama": 0.94, "Ole Miss": 0.10}
    assert pick_conference_favorite(list(components), scores, components) == "Georgia"


def test_preseason_strength_bumps_conference_favorite(monkeypatch):
    from app.services import cfb_preseason as mod

    components = {
        "Indiana": {"sp": 32.4, "fpi": 18.7, "talent": 645.0, "elo": 1522.0, "ret": 0.25, "coach_chg": 0.0},
        "Ohio State": {"sp": 30.1, "fpi": 24.8, "talent": 974.0, "elo": 1628.0, "ret": 0.31, "coach_chg": 0.0},
        "Oregon": {"sp": 25.9, "fpi": 21.1, "talent": 941.0, "elo": 1648.0, "ret": 0.19, "coach_chg": 0.0},
        "Purdue": {"sp": 0.0, "fpi": -8.0, "talent": 500.0, "elo": 1400.0, "ret": 0.40, "coach_chg": 0.0},
        "Georgia": {"sp": 24.1, "fpi": 22.1, "talent": 1003.0, "elo": 1653.0, "ret": 0.37, "coach_chg": 0.0},
        "Texas": {"sp": 16.2, "fpi": 26.1, "talent": 973.0, "elo": 1624.0, "ret": 0.28, "coach_chg": 0.0},
        "Alabama": {"sp": 14.8, "fpi": 23.9, "talent": 993.0, "elo": 1619.0, "ret": 0.43, "coach_chg": 0.0},
        "Vanderbilt": {"sp": 5.0, "fpi": 4.0, "talent": 700.0, "elo": 1480.0, "ret": 0.50, "coach_chg": 0.0},
    }
    monkeypatch.setattr(mod, "load_preseason_components", lambda *a, **k: components)
    team_conf = {
        "Indiana": "big_ten",
        "Ohio State": "big_ten",
        "Oregon": "big_ten",
        "Purdue": "big_ten",
        "Georgia": "sec",
        "Texas": "sec",
        "Alabama": "sec",
        "Vanderbilt": "sec",
    }
    strength = preseason_strength(2025, team_conf, elo={t: c["elo"] for t, c in components.items()})
    assert strength["Indiana"] > strength["Ohio State"]
    assert strength["Georgia"] > strength["Texas"]
