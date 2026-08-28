from pathlib import Path

from app.ingest import fcs_history


def _game(game_id, home, away, *, conference="Big Sky", status="Final"):
    return {"game": {"gameID": game_id, "startDate": "2025-09-06T18:00:00Z", "status": status,
        "home": {"names": {"short": home}, "conference": conference, "score": "24"},
        "away": {"names": {"short": away}, "conference": conference, "score": "17"}}}


def test_ingest_dedupes_weeks_and_keeps_native_fcs_only(monkeypatch, tmp_path):
    monkeypatch.setattr(fcs_history, "SEASONS", (2025,))
    monkeypatch.setattr(fcs_history, "WEEKS", tuple(range(6)))
    monkeypatch.setattr(fcs_history, "PARQUET", tmp_path / "games.parquet")
    monkeypatch.setattr(fcs_history, "MANIFEST", tmp_path / "manifest.json")
    def fake_fetch(season, division, week, refresh=False):
        if division == "fcs":
            return {"games": [_game(f"n{week}", "Montana", "Weber State")]}
        if division == "fbs" and week == 0:
            return {"games": [_game("cross", "Oregon", "Montana", conference="Big Sky")]}
        return {"games": []}
    monkeypatch.setattr(fcs_history, "_fetch", fake_fetch)
    frame, manifest = fcs_history.ingest()
    assert len(frame) == 6
    assert frame.game_id.is_unique
    assert manifest["fetch_failures"] == []
    assert set(frame.cohort) == {"fcs_vs_fcs"}
    assert set(frame.source) == {"ncaa"}


def test_fetch_uses_cache_without_network(monkeypatch, tmp_path):
    monkeypatch.setattr(fcs_history, "CACHE_DIR", tmp_path)
    cached = tmp_path / "2025_fcs_01.json"
    cached.write_text('{"games": []}', encoding="utf-8")
    monkeypatch.setattr(fcs_history.httpx, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network used")))
    assert fcs_history._fetch(2025, "fcs", 1) == {"games": []}

def test_espn_neutral_parser_requires_explicit_field():
    payload={"events":[{"id":"1","competitions":[{"neutralSite":True,"competitors":[
        {"homeAway":"home","score":"21","team":{"displayName":"Montana Grizzlies"}},
        {"homeAway":"away","score":"14","team":{"displayName":"Weber State Wildcats"}}]}]}]}
    records=fcs_history._espn_records(payload)
    assert records[0]["neutral_site"]==1
    del payload["events"][0]["competitions"][0]["neutralSite"]
    assert fcs_history._espn_records(payload)==[]
