"""Leakage-safe FCS-native pregame features with explicit missingness."""
from __future__ import annotations
from collections import defaultdict
import re
import pandas as pd

FEATURE_COLUMNS = ["elo_diff","srs_diff","season_win_pct_diff","last5_win_pct_diff","margin_diff","sos_diff","home_field","neutral_site","conference_game","rank_diff","rank_missing"]

def canonical_team_id(name: str, source_id: str | int | None = None) -> str:
    if source_id not in (None, ""): return f"ncaa:{source_id}"
    text=re.sub(r"[^a-z0-9]+","-",str(name).lower().replace("st.","state")).strip("-")
    if not text: raise ValueError("FCS team requires a stable source id or name")
    return f"name:{text}"

def build_features(games: pd.DataFrame) -> pd.DataFrame:
    required={"game_id","date","season","home_team_id","away_team_id","home_score","away_score","home_win","neutral_site"}
    missing=required-set(games.columns)
    if missing: raise ValueError(f"Missing FCS columns: {sorted(missing)}")
    df=games.copy();df["date"]=pd.to_datetime(df.date);df=df.sort_values(["date","game_id"]).reset_index(drop=True)
    elo=defaultdict(lambda:1500.0);srs=defaultdict(float);hist=defaultdict(list);rows=[]
    for g in df.itertuples(index=False):
        h,a=str(g.home_team_id),str(g.away_team_id);season=int(g.season);neutral=int(g.neutral_site)
        hh=[x for x in hist[h] if x[0]==season];ah=[x for x in hist[a] if x[0]==season]
        def winpct(x): return sum(v[1] for v in x)/len(x) if x else None
        def margin(x): return sum(v[2] for v in x)/len(x) if x else None
        hw,aw=winpct(hh),winpct(ah);hl,al=winpct(hh[-5:]),winpct(ah[-5:]);hm,am=margin(hh),margin(ah)
        hr=getattr(g,"home_rank",None);ar=getattr(g,"away_rank",None);rank_missing=int(pd.isna(hr) or pd.isna(ar))
        rows.append({"game_id":str(g.game_id),"date":g.date,"season":season,"home_team_id":h,"away_team_id":a,"home_win":int(g.home_win),"elo_diff":elo[h]-elo[a],"srs_diff":srs[h]-srs[a],"season_win_pct_diff":None if hw is None or aw is None else hw-aw,"last5_win_pct_diff":None if hl is None or al is None else hl-al,"margin_diff":None if hm is None or am is None else hm-am,"sos_diff":None,"home_field":0 if neutral else 1,"neutral_site":neutral,"conference_game":int(getattr(g,"conference_game",0) or 0),"rank_diff":None if rank_missing else float(ar)-float(hr),"rank_missing":rank_missing})
        hs,aws=float(g.home_score),float(g.away_score);actual=int(g.home_win);expected=1/(1+10**((elo[a]-elo[h]-(0 if neutral else 55))/400));delta=20*(actual-expected);elo[h]+=delta;elo[a]-=delta
        resid=max(-28,min(28,hs-aws))-(srs[h]-srs[a]);srs[h]+=.2*resid;srs[a]-=.2*resid
        hist[h].append((season,actual,hs-aws));hist[a].append((season,1-actual,aws-hs))
    return pd.DataFrame(rows)
