from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_home_page():
    response = client.get("/")
    assert response.status_code == 200
    text = response.text
    assert "Parlay Builder v1" in text
    assert 'href="/mlb"' in text
    assert "Coming soon" in text
    assert "<script" not in text.lower()


def test_mlb_page():
    response = client.get("/mlb")
    assert response.status_code == 200
    text = response.text
    assert "MLB Daily Board" in text
    assert 'href="/"' in text
    assert 'href="/mlb/lab"' in text
    assert "Run live" in text
    assert "Click Run live or Demo to load the board." in text
    assert "Model accuracy (30 days)" in text
    assert "mlb.js" in text


def test_backtest_saved_endpoint():
    response = client.get("/api/backtest/saved")
    assert response.status_code == 200
    body = response.json()
    assert "moneyline" in body
    assert "totals" in body
