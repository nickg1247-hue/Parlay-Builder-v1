"""Light smoke load test — keeps CI fast."""

from datetime import date

from scripts.stress_test_site import run_stress


def test_stress_site_smoke():
    code = run_stress(
        sessions=3,
        workers=4,
        games_per_session=3,
        ticker_burst_n=10,
        game_date=date(2026, 6, 6),
    )
    assert code == 0
