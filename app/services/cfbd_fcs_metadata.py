"""CollegeFootballData metadata for current FCS games (primary site-status source)."""
from __future__ import annotations
import os
from datetime import date
from typing import Any
import httpx
from app.ingest.cfb import CFBD_BASE_URL

def fetch_cfbd_fcs_metadata(game_date:date,*,week:int)->list[dict[str,Any]]:
    key=(os.getenv("CFBD_API_KEY")or"").strip()
    if not key:raise RuntimeError("CFBD_API_KEY is not configured")
    response=httpx.get(f"{CFBD_BASE_URL}/games",params={"year":game_date.year,"seasonType":"regular","division":"fcs","week":week},headers={"Authorization":f"Bearer {key}"},timeout=60)
    response.raise_for_status();rows=response.json();out=[]
    for row in rows if isinstance(rows,list)else[]:
        if str(row.get("startDate")or"")[:10]!=game_date.isoformat():continue
        home_class=str(row.get("homeClassification")or"").lower();away_class=str(row.get("awayClassification")or"").lower()
        if home_class!="fcs"or away_class!="fcs":continue
        if "neutralSite"not in row:continue
        out.append({"cfbd_game_id":str(row.get("id")or""),"date":game_date.isoformat(),"home_team":str(row.get("homeTeam")or""),"away_team":str(row.get("awayTeam")or""),"home_division":home_class,"away_division":away_class,"neutral_site":int(bool(row["neutralSite"])),"neutral_site_known":True,"neutral_site_missing":False,"neutral_site_source":"collegefootballdata"})
    return out
