from app.models.nfl_confidence import (
    apply_categories,
    assign_category,
    category_for_proba,
    category_label,
    category_proof,
    favorite_pct,
    fit_category_cuts,
)


def _g(fav: float, correct: int) -> dict:
    return {"home_pct": fav, "away_pct": round(100 - fav, 1), "correct": correct}


def test_favorite_pct_uses_the_favorite():
    assert favorite_pct(62, 38) == 62
    assert favorite_pct(41, 59) == 59


def test_cuts_respect_floors():
    games = []
    games += [_g(52, 1 if i % 2 == 0 else 0) for i in range(40)]
    games += [_g(64, 1) for _ in range(24)]
    games += [_g(64, 0) for _ in range(16)]
    games += [_g(80, 1) for _ in range(18)]
    games += [_g(80, 0) for _ in range(6)]
    games += [_g(92, 1) for _ in range(19)]
    games += [_g(92, 0) for _ in range(1)]
    cuts = fit_category_cuts(games)
    labeled = apply_categories(games, cuts)
    proof = {row["category"]: row for row in category_proof(labeled, cuts)}
    assert proof["soft"]["meets_floor"] is True
    assert proof["soft"]["hit_pct"] >= 60
    assert proof["hard"]["meets_floor"] is True
    assert proof["hard"]["hit_pct"] >= 75
    assert proof["lock"]["meets_floor"] is True
    assert proof["lock"]["hit_pct"] >= 95
    assert assign_category(52, 48, cuts) == "toss-up"


def test_lock_absent_when_tail_is_not_95():
    games = [_g(70, 1 if i < 20 else 0) for i in range(40)]
    cuts = fit_category_cuts(games)
    assert cuts["cuts"]["lock"] is None
    proof = {row["category"]: row for row in category_proof(games, cuts)}
    assert proof["lock"]["games"] == 0
    assert proof["lock"]["meets_floor"] is False
    assert proof["lock"]["best_tail"] is not None
    assert "95%" in (proof["lock"]["note"] or "")


def test_interior_lock_when_extreme_upset_breaks_the_tail():
    games = []
    games += [_g(52, 1 if i % 2 == 0 else 0) for i in range(40)]
    games += [_g(64, 1) for _ in range(24)]
    games += [_g(64, 0) for _ in range(16)]
    games += [_g(76, 1) for _ in range(20)]
    games += [_g(76, 0) for _ in range(5)]
    games += [_g(82, 1) for _ in range(10)]
    games += [_g(88, 1), _g(88, 0)]
    cuts = fit_category_cuts(games)
    lock = cuts["cuts"]["lock"]
    assert lock is not None
    assert lock["lo"] >= 80
    assert lock["hi"] <= 88
    assert assign_category(82, 18, cuts) == "lock"
    assert assign_category(88, 12, cuts) == "hard"
    labeled = apply_categories(games, cuts)
    proof = {row["category"]: row for row in category_proof(labeled, cuts)}
    assert proof["lock"]["meets_floor"] is True
    assert proof["lock"]["hit_pct"] >= 95


def test_live_cuts_label_62_percent_soft():
    assert category_for_proba(0.62, game_type="regular") == "soft"
    assert category_for_proba(0.52, game_type="regular") == "toss-up"
    assert category_label("lock") == "Lock"


def test_preseason_cannot_be_lock_or_hard():
    games = [_g(82, 1) for _ in range(10)]
    cuts = fit_category_cuts(games)
    assert assign_category(82, 18, cuts, game_type="regular") == "lock"
    assert assign_category(82, 18, cuts, game_type="preseason") == "soft"
