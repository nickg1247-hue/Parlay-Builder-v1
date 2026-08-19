from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["sport"] == "mlb"
    assert body["phase"] == "5"
    assert "mlb_games_count" in body
    assert "mlb_date_range" in body
    assert "market_eval_status" in body
    assert "parlay_status" in body


def test_api_health():
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in ("ok", "degraded")
    assert body["database"] in ("connected", "error")
    assert "version" in body
    assert "timestamp" in body
    assert "odds" not in body
    assert "mlb_games_count" not in body
