from fastapi.testclient import TestClient
import pytest

from app.main import app

client = TestClient(app)


@pytest.fixture
def auth_env(monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "testadmin")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-secret")
    monkeypatch.setenv("ADMIN_SESSION_SECRET", "unit-test-session-secret")
    client.cookies.clear()
    yield
    client.cookies.clear()


def _login():
    return client.post(
        "/api/auth/login",
        json={"username": "testadmin", "password": "test-secret"},
    )


def test_home_page():
    response = client.get("/")
    assert response.status_code == 200
    text = response.text
    assert "NTG Sports" in text
    assert 'href="/mlb"' in text
    assert 'href="/nba"' in text
    assert 'href="/mlb/board"' not in text
    assert 'id="today-glance"' in text
    assert 'id="best-bets"' in text
    assert 'id="hero-chips"' in text
    assert 'id="news-list"' in text
    assert "/api/status/refresh" in text
    assert "live-ticker" in text


def test_mlb_slate_page():
    response = client.get("/mlb")
    assert response.status_code == 200
    text = response.text
    assert "MLB" in text
    assert 'href="/"' in text
    assert 'href="/mlb/board"' not in text
    assert "/api/scores/today" in text
    assert "app.js" in text


def test_mlb_board_page(auth_env):
    _login()
    response = client.get("/mlb/board")
    assert response.status_code == 200
    text = response.text
    assert "MLB Daily Board" in text
    assert 'href="/sandbox"' in text
    assert "Run live" in text
    assert "mlb.js" in text


def test_mlb_board_demo_page(auth_env):
    _login()
    response = client.get("/mlb/board/demo")
    assert response.status_code == 200
    text = response.text
    assert "MLB pick preview" in text
    assert "Model winner vs +EV pick" in text
    assert "mlb_board_demo.js" in text
    assert 'href="/sandbox"' in text


def test_slate_includes_model_and_ev_pick_fields(auth_env):
    _login()
    response = client.get(
        "/api/daily?date=2025-08-15&use_cache=true&skip_totals=false"
    )
    assert response.status_code == 200
    body = response.json()
    if not body.get("slate"):
        pytest.skip("No demo slate games in test environment")
    row = body["slate"][0]
    assert "model_pick_team" in row
    assert "model_pick_prob" in row
    assert "ev_pick_team" in row
    assert "ml_picks_disagree" in row
    assert row["model_pick_side"] in ("home", "away")


def test_game_page_loads():
    response = client.get("/mlb/game/824269")
    assert response.status_code == 200
    text = response.text
    assert "matchup-header" in text
    assert "game-matchup-board" in text
    assert "app.js" in text
    assert "game.js" in text
    assert "game-page-bg" in text
    assert "game-page-wash" in text


def test_game_js_uses_render_matchup_header():
    from pathlib import Path

    text = Path(__file__).resolve().parent.parent.joinpath("static/game.js").read_text(
        encoding="utf-8"
    )
    assert "renderMatchupHeader" in text
    assert "renderMatchupBoard" in text
    assert "market_cards" in text


def test_nba_slate_page():
    response = client.get("/nba")
    assert response.status_code == 200
    text = response.text
    assert "NBA" in text
    assert "/api/scores/today" in text
    assert 'href="/mlb"' in text
    assert 'href="/nba/board"' not in text


def test_nba_board_page(auth_env):
    _login()
    response = client.get("/nba/board")
    assert response.status_code == 200
    text = response.text
    assert "NBA Daily Board" in text
    assert 'href="/sandbox"' in text
    assert 'id="run-live-btn"' in text
    assert 'id="run-demo-btn"' in text
    assert "nba_board.js" in text


def test_sandbox_hub_page(auth_env):
    _login()
    response = client.get("/sandbox")
    assert response.status_code == 200
    text = response.text
    assert "Sandbox" in text
    assert 'href="/mlb/board"' in text
    assert 'href="/mlb/board/demo"' in text
    assert 'href="/mlb/lab"' in text
    assert 'href="/nba/board"' in text
    assert 'href="/nba/board/factors"' in text


def test_nba_game_page():
    response = client.get("/nba/game/401766458")
    assert response.status_code == 200
    assert "nba_game.js" in response.text
    assert "game-matchup-board" in response.text


def test_backtest_saved_endpoint(auth_env):
    _login()
    response = client.get("/api/backtest/saved")
    assert response.status_code == 200
    body = response.json()
    assert "moneyline" in body
    assert "totals" in body
