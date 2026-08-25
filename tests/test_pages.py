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
    assert "home-landing" in text
    assert "home-v2" in text
    assert 'id="home-landing"' in text
    assert 'id="hl-performance"' in text
    assert 'id="hl-live-summary"' in text
    assert 'id="hl-sports"' in text
    assert 'id="hl-intel"' in text
    assert 'id="hl-games"' in text
    assert 'id="hl-picks"' in text
    assert 'id="news-list"' not in text
    assert "home-landing.css" in text
    assert "ntg-system.css" in text
    assert "home-landing.js" in text
    assert "See the edge" in text
    assert "before the line moves." in text
    assert "Explore today's slate" in text
    assert "Today on NTG" in text
    assert "Today's Intelligence" in text
    assert "Live &amp; Upcoming" in text
    assert "Inside the NTG Engine" in text
    assert "Your NTG" in text
    assert "Learn &amp; Improve" not in text
    assert "Edge by Sport" not in text
    assert "Why NTG Has the Edge" not in text
    assert "Data. Models. Edges." not in text
    assert 'id="today-glance"' not in text
    assert 'id="best-bets"' not in text
    assert "Prop Parlay Builder" not in text
    assert 'id="daily-board"' not in text


def test_mlb_slate_page():
    response = client.get("/mlb")
    assert response.status_code == 200
    text = response.text
    assert "MLB" in text
    assert 'href="/"' in text
    assert 'id="ntg-page-data"' in text
    assert "mlb_slate" in text
    assert "app.js" in text
    assert "ntg-system.css" in text
    assert "Today's model outlook" in text
    assert 'id="slate-date-input"' in text
    assert 'id="slate-date-go"' in text


def test_mlb_board_page(auth_env):
    _login()
    response = client.get("/mlb/board")
    assert response.status_code == 200
    text = response.text
    assert "MLB Daily Board" in text
    assert 'href="/sandbox"' in text
    assert "Run live" in text
    assert "Model picks" in text
    assert "+EV singles" in text
    assert "model-picks-table" in text
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
    assert "model_confidence" in row
    assert "model_pick_action" in row
    assert "ev_pick_team" in row
    assert "ml_picks_disagree" in row
    assert row["model_pick_side"] in ("home", "away")


def test_game_page_loads(cached_mlb_game):
    game_date, game_id = cached_mlb_game
    response = client.get(f"/mlb/game/{game_id}?date={game_date}")
    assert response.status_code == 200
    text = response.text
    assert "matchup-header" in text
    assert "game-matchup-board" in text
    assert 'id="ntg-page-data"' in text
    assert "mlb_game" in text
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
    assert 'class="sport-pills"' in text
    assert "app.js" in text
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
    assert "ntg-shell" in text
    assert "ntg-system.css" in text
    assert 'href="/mlb/board"' in text
    assert 'href="/mlb/board/demo"' in text
    assert 'href="/mlb/lab"' in text
    assert 'href="/nba/board"' in text
    assert 'href="/nba/board/factors"' in text


def test_updates_page():
    response = client.get("/updates")
    assert response.status_code == 200
    text = response.text
    assert "Site updates" in text
    assert "ntg-system.css" in text
    assert "site_updates.json" in text or "updates-list" in text
    assert "app.js" in text


def test_site_updates_json():
    response = client.get("/static/site_updates.json")
    assert response.status_code == 200
    data = response.json()
    assert data.get("version")
    assert isinstance(data.get("history"), list)


def test_nba_game_page():
    response = client.get("/nba/game/401766458")
    assert response.status_code == 200
    assert "nba_game.js" in response.text
    assert "game-matchup-board" in response.text


def test_mlb_props_empty_filter_query_is_ok(monkeypatch):
    async def fake_page_data(*args, **kwargs):
        return {
            "kind": "player_props",
            "sport": "mlb",
            "date": "2026-08-19",
            "propsSearch": {"props": [], "total_matched": 0},
            "markets": [],
            "bookmakers": [],
            "tracker": {},
            "filters": {},
            "status": {},
            "tickerScores": {},
        }

    monkeypatch.setattr("app.main.build_player_props_page_data", fake_page_data)
    response = client.get(
        "/mlb/props?min_odds=&line_value=&min_score=&market_type=&min_hit_l5=&min_hit_l10="
    )
    assert response.status_code == 200
    assert "Player props" in response.text
    assert 'data-prop-sport="mlb"' in response.text
    assert 'data-prop-sport="nfl"' in response.text
    assert 'id="props-filter-drawer"' in response.text
    assert 'id="props-open-filters"' in response.text
    assert 'hidden aria-label="Prop filters"' not in response.text
    assert 'id="props-search-results"' in response.text
    assert 'id="pp-top-edge"' in response.text
    assert 'id="pp-opportunities"' in response.text
    assert 'id="pp-all"' in response.text
    assert "props-page.css" in response.text


def test_unified_props_page_nfl_selector(monkeypatch):
    async def fake_page_data(sport, *args, **kwargs):
        return {
            "kind": "player_props",
            "sport": "nfl",
            "date": "2026-08-19",
            "propsSearch": {"props": [], "total_matched": 0, "empty_reason": "no_offers"},
            "markets": [],
            "bookmakers": [],
            "tracker": {},
            "filters": {},
            "status": {},
            "tickerScores": {},
        }

    monkeypatch.setattr("app.main.build_player_props_page_data", fake_page_data)
    response = client.get("/props?sport=nfl")
    assert response.status_code == 200
    assert "Player props" in response.text
    assert 'name="sport"' in response.text
    assert 'id="filter-position"' in response.text


def test_signin_page():
    response = client.get("/signin")
    assert response.status_code == 200
    text = response.text
    assert "ntg-auth" in text
    assert "ntg-system.css" in text
    assert "Sign in" in text


def test_backtest_saved_endpoint(auth_env):
    _login()
    response = client.get("/api/backtest/saved")
    assert response.status_code == 200
    body = response.json()
    assert "moneyline" in body
    assert "totals" in body
