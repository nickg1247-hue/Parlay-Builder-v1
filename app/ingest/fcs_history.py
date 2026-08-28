"""Authoritative, no-key NCAA FCS history ingest (2018-2025)."""
from __future__ import annotations
import json,time
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path
from typing import Any
import httpx,pandas as pd
from app.config import PROJECT_ROOT
from app.features.fcs_pregame import canonical_team_id
from app.services.cfb_game_metadata import FCS_CONFERENCES,game_identity
from app.services.scores_cfb import NCAA_SCOREBOARD,ncaa_game_record
from app.odds.cfb_team_aliases import normalize_team_name

SEASONS=tuple(range(2018,2026));WEEKS=tuple(range(21));DIVISIONS=("fbs","fcs","d2","d3")
CACHE_DIR=PROJECT_ROOT/"data/processed/fcs_ncaa_cache";PARQUET=PROJECT_ROOT/"data/processed/fcs_games.parquet";MANIFEST=PROJECT_ROOT/"data/processed/fcs_games_manifest.json"
ESPN_CACHE_DIR=PROJECT_ROOT/"data/processed/fcs_espn_cache"
ESPN_SCOREBOARD="https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard"
SCHEMA=["game_id","source_game_id","date","season","week","home_team_id","away_team_id","home_team","away_team","home_score","away_score","home_win","neutral_site","neutral_site_missing","conference_game","home_conference","away_conference","home_rank","away_rank","cohort","source","provenance"]

def _path(s:int,d:str,w:int)->Path:return CACHE_DIR/f"{s}_{d}_{w:02d}.json"
def _fetch(s:int,d:str,w:int,refresh:bool=False)->dict[str,Any]:
 p=_path(s,d,w)
 if p.exists()and not refresh:return json.loads(p.read_text(encoding="utf-8"))
 url=NCAA_SCOREBOARD.format(division=d,season=s,week=w);last=None
 for n in range(7):
  try:
   r=httpx.get(url,timeout=40);r.raise_for_status();payload=r.json();p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(payload),encoding="utf-8");return payload
  except Exception as e:last=e;time.sleep(.75*(n+1))
 raise RuntimeError(f"NCAA fetch failed {s}/{d}/{w}")from last

def _team_key(name:str)->str:return canonical_team_id(name).removeprefix("name:")
def _same_team(left:str,right:str)->bool:
 a=normalize_team_name(left).casefold();b=normalize_team_name(right).casefold()
 return a==b or a.startswith(b+" ") or b.startswith(a+" ")

def _espn_fetch_date(day:str,refresh:bool=False)->dict[str,Any]:
 p=ESPN_CACHE_DIR/f"{day}.json"
 if p.exists()and not refresh:
  cached=json.loads(p.read_text(encoding="utf-8"))
  # ESPN silently caps this endpoint at 25 when a limit parameter is present.
  if len(cached.get("events")or[])!=25:return cached
 last=None
 for n in range(7):
  try:
   r=httpx.get(ESPN_SCOREBOARD,params={"dates":day.replace("-",""),"groups":81},timeout=40);r.raise_for_status();payload=r.json();p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(payload),encoding="utf-8");return payload
  except Exception as e:last=e;time.sleep(.75*(n+1))
 raise RuntimeError(f"ESPN fetch failed {day}")from last

def _espn_records(payload:dict[str,Any])->list[dict[str,Any]]:
 out=[]
 for event in payload.get("events")or[]:
  for comp in event.get("competitions")or[]:
   sides={str(c.get("homeAway")):c for c in comp.get("competitors")or[]}
   if "home"not in sides or "away"not in sides or "neutralSite"not in comp:continue
   def side(which):
    c=sides[which];t=c.get("team")or{};return {"name":t.get("displayName")or t.get("shortDisplayName")or t.get("name")or"","score":c.get("score")}
   h,a=side("home"),side("away")
   try:hs,aws=int(h["score"]),int(a["score"])
   except (TypeError,ValueError):continue
   out.append({"event_id":str(event.get("id")or comp.get("id")or""),"home":normalize_team_name(h["name"]),"away":normalize_team_name(a["name"]),"home_score":hs,"away_score":aws,"neutral_site":int(bool(comp["neutralSite"]))})
 return out

def _enrich_neutral(df:pd.DataFrame,refresh:bool=False)->tuple[pd.DataFrame,dict[str,Any]]:
 enriched=df.copy();matched=conflicts=0;fail=[]
 for day,indexes in enriched.groupby("date").groups.items():
  try:records=_espn_records(_espn_fetch_date(str(day),refresh))
  except Exception as e:fail.append({"date":str(day),"error":str(e)});continue
  for idx in indexes:
   row=enriched.loc[idx];hn=normalize_team_name(str(row.home_team));an=normalize_team_name(str(row.away_team))
   candidates=[r for r in records if r["home_score"]==int(row.home_score)and r["away_score"]==int(row.away_score)and _same_team(r["home"],hn)and _same_team(r["away"],an)]
   if len(candidates)==1:
    enriched.at[idx,"neutral_site"]=candidates[0]["neutral_site"];enriched.at[idx,"neutral_site_missing"]=0;enriched.at[idx,"provenance"]+=f";espn_event:{candidates[0]['event_id']}";matched+=1
   elif len(candidates)>1:conflicts+=1
 return enriched,{"matched":matched,"unknown":int(enriched.neutral_site_missing.sum()),"conflicts":conflicts,"fetch_failures":fail}
def ingest(*,refresh:bool=False)->tuple[pd.DataFrame,dict[str,Any]]:
 records=[];fail=[]
 for s in SEASONS:
  for d in DIVISIONS:
   for w in WEEKS:
    try:
     for raw in _fetch(s,d,w,refresh).get("games")or[]:
      r=ncaa_game_record(raw,d);r.update(season=s,week=w);records.append(r)
    except Exception as e:fail.append({"season":s,"division":d,"week":w,"error":str(e)})
 by=defaultdict(list)
 for r in records:
  if r.get("game_id"):by[str(r["game_id"])].append(r)
 division_appearances=defaultdict(Counter)
 for copies in by.values():
  for r in copies:
   division_appearances[(r["season"],r["division"])].update({_team_key(r["home_team"]),_team_key(r["away_team"])})
 out=[];ex=Counter();aliases=[]
 for gid,copies in by.items():
  b=copies[0];s=int(b["season"]);divs={r["division"]for r in copies};identities={game_identity(r)for r in copies}
  if len(identities)!=1:ex["ambiguous_identity"]+=1;aliases.append({"game_id":gid,"identities":[list(x)for x in identities]});continue
  if "fcs"not in divs:continue
  if b.get("status")!="Final":ex["nonfinal_or_cancelled"]+=1;continue
  hs,aws=b.get("home_score"),b.get("away_score")
  if hs is None or aws is None:ex["missing_score"]+=1;continue
  if hs==aws:ex["tie"]+=1;continue
  ht,at=_team_key(b["home_team"]),_team_key(b["away_team"])
  # Cross-division games appear on both scoreboards. Native ownership therefore
  # requires season-long presence instead of one scoreboard appearance.
  foreign={team for d in ("fbs","d2","d3") for team,count in division_appearances[(s,d)].items() if count>=5}
  fcs_native={team for team,count in division_appearances[(s,"fcs")].items() if count>=5}
  if divs!={"fcs"}or ht in foreign or at in foreign or ht not in fcs_native or at not in fcs_native:ex["cross_division"]+=1;continue
  hc=str(b.get("home_conference")or"").strip().lower();ac=str(b.get("away_conference")or"").strip().lower()
  if hc not in FCS_CONFERENCES or ac not in FCS_CONFERENCES:ex["unverified_fcs_ownership"]+=1;continue
  out.append({"game_id":f"ncaa:{gid}","source_game_id":gid,"date":str(b["date"])[:10],"season":s,"week":int(b.get("week")or 0),"home_team_id":canonical_team_id(b["home_team"],b.get("home_team_id")),"away_team_id":canonical_team_id(b["away_team"],b.get("away_team_id")),"home_team":b["home_team"],"away_team":b["away_team"],"home_score":int(hs),"away_score":int(aws),"home_win":int(hs>aws),"neutral_site":0,"neutral_site_missing":1,"conference_game":int(bool(hc and hc==ac)),"home_conference":b.get("home_conference")or"","away_conference":b.get("away_conference")or"","home_rank":b.get("home_rank"),"away_rank":b.get("away_rank"),"cohort":"fcs_vs_fcs","source":"ncaa","provenance":f"scoreboard/football/fcs/{s}/{int(b.get('week')or 0):02d}/all-conf"})
 df=pd.DataFrame(out,columns=SCHEMA).drop_duplicates("game_id").sort_values(["date","game_id"]).reset_index(drop=True)
 df,enrichment=_enrich_neutral(df,refresh)
 if df.empty or df.game_id.duplicated().any()or not set(df.cohort)=={"fcs_vs_fcs"}:raise ValueError("Invalid FCS dataset")
 manifest={"generated_at":datetime.now(timezone.utc).isoformat(),"source":"NCAA scoreboard API (no key), ESPN neutral-site enrichment (no key)","seasons":list(SEASONS),"schema":SCHEMA,"rows":len(df),"season_counts":{str(k):int(v)for k,v in df.groupby("season").size().items()},"raw_listings":len(records),"unique_source_games":len(by),"exclusions":dict(ex),"fetch_failures":fail,"identity_conflicts":aliases[:25],"neutral_site_enrichment":enrichment,"neutral_site_missing":int(df.neutral_site_missing.sum())}
 PARQUET.parent.mkdir(parents=True,exist_ok=True);df.to_parquet(PARQUET,index=False);MANIFEST.write_text(json.dumps(manifest,indent=2),encoding="utf-8");return df,manifest
