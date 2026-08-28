from datetime import date
from app.services import cfbd_fcs_metadata as metadata

def test_cfbd_fcs_metadata_keeps_only_pure_fcs_exact_date(monkeypatch):
    rows=[
        {"id":1,"startDate":"2026-08-27T22:00:00Z","homeTeam":"Youngstown State","awayTeam":"Mercyhurst","homeClassification":"fcs","awayClassification":"fcs","neutralSite":False},
        {"id":2,"startDate":"2026-08-27T22:00:00Z","homeTeam":"Ohio State","awayTeam":"Youngstown State","homeClassification":"fbs","awayClassification":"fcs","neutralSite":False},
        {"id":3,"startDate":"2026-08-28T22:00:00Z","homeTeam":"Towson","awayTeam":"Maine","homeClassification":"fcs","awayClassification":"fcs","neutralSite":True},
    ]
    class Response:
        def raise_for_status(self):pass
        def json(self):return rows
    monkeypatch.setenv("CFBD_API_KEY","test-key")
    monkeypatch.setattr(metadata.httpx,"get",lambda *a,**k:Response())
    result=metadata.fetch_cfbd_fcs_metadata(date(2026,8,27),week=1)
    assert len(result)==1
    assert result[0]["cfbd_game_id"]=="1"
    assert result[0]["neutral_site_known"] is True
    assert result[0]["neutral_site_source"]=="collegefootballdata"
