"""CFB team logo lookup tests."""

from app.models.cfb_baseline import load_games
from app.services.cfb_team_logos import enrich_game_logos, lookup_team_logo, refresh_cfb_logo_map


def test_lookup_common_fbs_schools():
    refresh_cfb_logo_map(force=True)
    for name in ("Alabama", "Ohio State", "Georgia", "App State", "Ole Miss", "Pitt"):
        meta = lookup_team_logo(name)
        assert meta is not None, name
        assert meta["logo_url"]
        assert meta["team_id"]


def test_enrich_ingest_style_names():
    refresh_cfb_logo_map(force=True)
    games = load_games()
    teams = set(games["home_team"].tolist() + games["away_team"].tolist())
    missing = []
    for team in teams:
        enriched = enrich_game_logos(
            {
                "home_team": team,
                "home_logo_url": None,
                "home_team_id": None,
            }
        )
        if not enriched.get("home_logo_url"):
            missing.append(team)
    assert len(missing) <= 5, f"missing logos: {missing[:20]}"
