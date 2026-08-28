import pandas as pd
import pytest
from app.features.fcs_pregame import FEATURE_COLUMNS,build_features,canonical_team_id
from app.models.fcs_baseline import MODEL_FAMILY,MODEL_PATH,MANIFEST_PATH,validate_schema
from app.services.cfb_game_metadata import is_public_fbs_fcs_game

def games():
    rows=[]
    for i in range(8):
        rows.append({"game_id":str(i),"date":f"2023-09-{i+1:02d}","season":2023,"home_team_id":"ncaa:1","away_team_id":"ncaa:2","home_score":20+i,"away_score":10,"home_win":1,"neutral_site":int(i==0),"conference_game":1,"home_rank":None,"away_rank":None})
    return pd.DataFrame(rows)

def test_fcs_artifacts_are_strictly_separate():
    assert MODEL_FAMILY=="fcs_moneyline"
    assert MODEL_PATH.name=="fcs_baseline_model.joblib"
    assert MANIFEST_PATH.name=="active_fcs_model.json"
    assert "cfb_baseline" not in str(MODEL_PATH)

def test_stable_ids_alias_and_neutral_leakage_safe_features():
    assert canonical_team_id("Youngstown St.")==canonical_team_id("Youngstown State")
    frame=build_features(games())
    assert frame.iloc[0].home_field==0
    assert frame.iloc[0].neutral_site==1
    assert pd.isna(frame.iloc[0].season_win_pct_diff)
    assert frame.iloc[1].elo_diff>0
    assert frame.iloc[1].srs_diff>0
    assert frame.iloc[1].rank_missing==1

def test_schema_and_cross_division_ownership():
    frame=build_features(games());validate_schema(frame)
    with pytest.raises(ValueError): validate_schema(frame.drop(columns=[FEATURE_COLUMNS[0]]))
    assert is_public_fbs_fcs_game({"divisions":["fcs"],"home_conference":"NEC","away_conference":"MVFC"})
    assert not is_public_fbs_fcs_game({"divisions":["fcs"],"home_conference":"SoCon","away_conference":"SAC"})
    assert not is_public_fbs_fcs_game({"divisions":["d2"],"home_conference":"SAC","away_conference":"SAC"})
