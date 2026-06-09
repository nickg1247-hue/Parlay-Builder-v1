from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_home_page():
    response = client.get("/")
    assert response.status_code == 200
    text = response.text
    assert "NTG Sports" in text
    assert 'href="/mlb"' in text
    assert 'href="/nba"' in text
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
    assert 'href="/mlb/board"' in text
    assert "/api/scores/today" in text
    assert "app.js" in text


def test_mlb_board_page():
    response = client.get("/mlb/board")
    assert response.status_code == 200
    text = response.text
    assert "MLB Daily Board" in text
    assert 'href="/mlb"' in text
    assert "Run live" in text
    assert "mlb.js" in text


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
    assert 'href="/nba/board"' in text


def test_nba_board_page():
    response = client.get("/nba/board")
    assert response.status_code == 200
    text = response.text
    assert "NBA Daily Board" in text
    assert 'href="/nba"' in text
    assert 'id="run-live-btn"' in text
    assert 'id="run-demo-btn"' in text
    assert "nba_board.js" in text


def test_nba_game_page():
    response = client.get("/nba/game/401766458")
    assert response.status_code == 200
    assert "nba_game.js" in response.text
    assert "game-matchup-board" in response.text


def test_backtest_saved_endpoint():
    response = client.get("/api/backtest/saved")
    assert response.status_code == 200
    body = response.json()
    assert "moneyline" in body
    assert "totals" in body
