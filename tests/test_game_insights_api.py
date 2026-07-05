"""Game insights API must stay reachable for client fallback."""

from fastapi.testclient import TestClient

from app.main import app


def test_mlb_game_insights_not_gated():
    client = TestClient(app)
    res = client.get("/api/games/mlb/822716/insights?date=2026-07-04&use_cache=true")
    assert res.status_code != 403, res.text
    if res.status_code == 200:
        data = res.json()
        assert data.get("game_id") == "822716"
