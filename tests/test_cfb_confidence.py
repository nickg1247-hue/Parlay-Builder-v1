"""CFB confidence categories use a separate cuts file from NFL."""

from app.models.cfb_confidence import (
    DEFAULT_CUTS,
    category_for_proba,
    category_label,
    fit_category_cuts,
    save_category_cuts,
)


def test_default_category_labels():
    assert category_label(category_for_proba(0.52)) == "Toss-up"
    assert category_for_proba(0.70) in ("soft", "hard", "lock", "toss-up")


def test_fit_and_save_cfb_cuts(tmp_path, monkeypatch):
    from app.models import cfb_confidence

    cuts_path = tmp_path / "cfb_confidence_cuts.json"
    monkeypatch.setattr(cfb_confidence, "CUTS_JSON", cuts_path)
    cfb_confidence.load_category_cuts.cache_clear()

    games = []
    for i in range(40):
        games.append({"home_pct": 52.0, "away_pct": 48.0, "correct": i % 2})
    for i in range(40):
        games.append({"home_pct": 72.0, "away_pct": 28.0, "correct": 1 if i < 28 else 0})
    for i in range(20):
        games.append({"home_pct": 88.0, "away_pct": 12.0, "correct": 1 if i < 16 else 0})

    cuts = fit_category_cuts(games)
    save_category_cuts(cuts)
    assert cuts_path.exists()
    assert "cuts" in cuts
    assert DEFAULT_CUTS["cuts"]["toss-up"]["hi"] == 58.0
