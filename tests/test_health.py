from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["sport"] == "mlb"
    assert body["phase"] == "1"
    assert "mlb_games_count" in body
    assert "mlb_date_range" in body
