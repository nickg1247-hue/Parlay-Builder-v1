"""Separate, disabled-by-default FCS-vs-FCS model family."""
from __future__ import annotations
import hashlib,json
from pathlib import Path
from typing import Any
import joblib,numpy as np,pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from app.config import PROJECT_ROOT
from app.features.fcs_pregame import FEATURE_COLUMNS,build_features,canonical_team_id
from app.models.platt_calibration import PlattCalibrator

MODEL_PATH=PROJECT_ROOT/"data/processed/fcs_baseline_model.joblib"
MANIFEST_PATH=PROJECT_ROOT/"data/processed/active_fcs_model.json"
REPORT_PATH=PROJECT_ROOT/"data/processed/fcs_model_evaluation.json"
HISTORY_PATH=PROJECT_ROOT/"data/processed/fcs_games.parquet"
MODEL_FAMILY="fcs_moneyline";MODEL_VERSION="fcs_v1_logistic_platt"
DISPLAY_CONFIDENCE_CAP=.89

def _metrics(y:pd.Series,p:np.ndarray)->dict[str,Any]:
    from sklearn.metrics import accuracy_score,brier_score_loss,log_loss
    y=np.asarray(y,dtype=int);p=np.clip(np.asarray(p,dtype=float),1e-6,1-1e-6);conf=np.maximum(p,1-p);pred=(p>=.5).astype(int)
    bins=[];ece=0.0
    for lo in np.arange(.5,1,.1):
        mask=(conf>=lo)&(conf<(lo+.1) if lo<.9 else conf<=1)
        if mask.any():
            acc=float((pred[mask]==y[mask]).mean());avg=float(conf[mask].mean());ece+=float(mask.mean())*abs(acc-avg);bins.append({"range":f"{lo:.1f}-{min(lo+.1,1):.1f}","games":int(mask.sum()),"accuracy":acc,"confidence":avg})
    hi=conf>=.9
    return {"games":int(len(y)),"accuracy":float(accuracy_score(y,pred)),"brier":float(brier_score_loss(y,p)),"log_loss":float(log_loss(y,p,labels=[0,1])),"ece":float(ece),"mean_confidence":float(conf.mean()),"confidence_bins":bins,"ninety_plus_games":int(hi.sum()),"ninety_plus_accuracy":float((pred[hi]==y[hi]).mean()) if hi.any() else None}

def _fit(train:pd.DataFrame,cal:pd.DataFrame)->tuple[Pipeline,PlattCalibrator]:
    pipe=Pipeline([("imputer",SimpleImputer(strategy="median",add_indicator=True)),("scale",StandardScaler()),("model",LogisticRegression(max_iter=2000,random_state=42))]);pipe.fit(train[FEATURE_COLUMNS],train.home_win)
    platt=PlattCalibrator().fit(pipe.predict_proba(cal[FEATURE_COLUMNS])[:,1],cal.home_win.to_numpy());return pipe,platt

def _predict(pipe:Pipeline,platt:PlattCalibrator,frame:pd.DataFrame)->np.ndarray:return platt.transform(pipe.predict_proba(frame[FEATURE_COLUMNS])[:,1])

def validate_schema(frame:pd.DataFrame)->None:
    missing=[c for c in FEATURE_COLUMNS if c not in frame]
    if missing: raise ValueError(f"FCS feature schema mismatch: {missing}")

def train_separate(games:pd.DataFrame)->dict[str,Any]:
    if set(games.get("cohort",pd.Series(["fcs_vs_fcs"])).dropna().unique())!={"fcs_vs_fcs"}: raise ValueError("FCS trainer accepts FCS-vs-FCS rows only")
    total_rows=len(games);known=games[games.get("neutral_site_missing",pd.Series(0,index=games.index)).fillna(1).astype(int)==0].copy()
    feat=build_features(known);validate_schema(feat)
    if any((feat.season==s).sum()==0 for s in (2023,2024,2025)): raise ValueError("FCS requires <=2022 train, 2023 calibration, 2024 OOS and 2025 locked holdout")
    reports={};final_pipe=None;final_platt=None
    for season,train_end,cal_year in ((2024,2022,2023),(2025,2023,2024)):
        train=feat[feat.season<=train_end];cal=feat[feat.season==cal_year];test=feat[feat.season==season]
        pipe,platt=_fit(train,cal);p=_predict(pipe,platt,test);model=_metrics(test.home_win,p);home=float(train.home_win.mean());baseline=np.full(len(test),home);base_metrics=_metrics(test.home_win,baseline)
        model.update({"home_win_rate":float(test.home_win.mean()),"home_baseline":base_metrics,"brier_gain":base_metrics["brier"]-model["brier"],"log_loss_gain":base_metrics["log_loss"]-model["log_loss"]})
        reports[str(season)]=model
        if season==2025:final_pipe,final_platt=pipe,platt
    neutral_known_rate=len(known)/total_rows
    score_gate=all(reports[str(s)]["brier_gain"]>=.005 and reports[str(s)]["log_loss_gain"]>=.01 and reports[str(s)]["ece"]<=.10 and (reports[str(s)]["ninety_plus_games"]==0 or reports[str(s)]["ninety_plus_accuracy"]>=.75) for s in (2024,2025))
    data_gate=all(reports[str(s)]["games"]>=100 for s in (2024,2025))
    promoted=score_gate and data_gate
    metrics={"protocol":"unknown neutral-site rows excluded; expanding window: <=2022/2023->2024; <=2023/2024 calibration->locked 2025","rows_found":total_rows,"rows_evaluable":len(known),"rows_excluded_unknown_neutral":total_rows-len(known),"seasons":reports,"promoted":bool(promoted),"score_gate_passed":bool(score_gate),"data_quality_gate_passed":bool(data_gate),"neutral_site_known_rate":neutral_known_rate,"promotion_gates":{"each_season_brier_gain":.005,"each_season_log_loss_gain":.01,"each_season_max_ece":.10,"minimum_90_plus_accuracy_when_present":.75,"minimum_evaluable_games_each_holdout":100}}
    artifact={"model_family":MODEL_FAMILY,"model_version":MODEL_VERSION,"feature_columns":FEATURE_COLUMNS,"model":final_pipe,"calibrator":final_platt,"metrics":metrics};joblib.dump(artifact,MODEL_PATH);REPORT_PATH.write_text(json.dumps(metrics,indent=2),encoding="utf-8");MANIFEST_PATH.write_text(json.dumps({"enabled":metrics["promoted"],"path":MODEL_PATH.relative_to(PROJECT_ROOT).as_posix(),"model_family":MODEL_FAMILY,"model_version":MODEL_VERSION,"metrics":metrics},indent=2),encoding="utf-8");return metrics

def load_artifact()->dict[str,Any]:
    manifest=json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if not manifest.get("enabled"): raise RuntimeError("FCS model has not passed promotion gates")
    path=PROJECT_ROOT/str(manifest["path"]).replace("\\","/")
    expected=manifest.get("artifact_sha256")
    if expected and hashlib.sha256(path.read_bytes()).hexdigest().lower()!=str(expected).lower():raise ValueError("FCS artifact checksum mismatch")
    artifact=joblib.load(path)
    if artifact.get("model_family")!=MODEL_FAMILY: raise ValueError("FCS artifact family mismatch")
    if artifact.get("model_version")!=manifest.get("model_version"):raise ValueError("FCS artifact version mismatch")
    if artifact.get("feature_columns")!=FEATURE_COLUMNS: raise ValueError("FCS artifact schema mismatch")
    return artifact

def predict_fcs(features:pd.DataFrame)->np.ndarray:
    artifact=load_artifact();validate_schema(features)
    raw=artifact["model"].predict_proba(features[FEATURE_COLUMNS])[:,1]
    probs=artifact["calibrator"].transform(raw)
    if np.any((probs<=0)|(probs>=1)): raise ValueError("Invalid FCS calibrated probability")
    return probs

def live_game_features(game:dict[str,Any],game_date:str)->pd.Series:
    """Build one pregame row from the immutable FCS history; never uses FBS state."""
    history=pd.read_parquet(HISTORY_PATH)
    known=set(history.home_team_id.astype(str))|set(history.away_team_id.astype(str))
    def owned(side:str)->str:
        name=str(game.get(f"{side}_team_model_name")or game.get(f"{side}_team")or"");source=canonical_team_id(name,game.get(f"{side}_team_id"));fallback=canonical_team_id(name)
        candidates=[value for value in (source,fallback)if value in known]
        if len(set(candidates))!=1:raise ValueError("ambiguous_or_unknown_fcs_team_id")
        return candidates[0]
    home,away=owned("home"),owned("away")
    if home not in known or away not in known or home==away:raise ValueError("ambiguous_or_unknown_fcs_team_id")
    upcoming={"game_id":str(game.get("game_id")or"live"),"date":game_date,"season":int(str(game_date)[:4]),"home_team_id":home,"away_team_id":away,"home_score":0,"away_score":0,"home_win":0,"neutral_site":int(bool(game["neutral_site"])),"neutral_site_missing":0,"conference_game":int(bool(game.get("conference_game"))),"home_rank":game.get("home_rank"),"away_rank":game.get("away_rank")}
    frame=build_features(pd.concat([history,pd.DataFrame([upcoming])],ignore_index=True))
    row=frame[frame.game_id.astype(str)==upcoming["game_id"]]
    if len(row)!=1:raise ValueError("fcs_feature_ownership_failure")
    validate_schema(row)
    return row.iloc[0]

def capped_display_probability(raw_home:float)->tuple[float,bool]:
    favorite=max(raw_home,1-raw_home)
    if favorite<=DISPLAY_CONFIDENCE_CAP:return raw_home,False
    return (DISPLAY_CONFIDENCE_CAP if raw_home>=.5 else 1-DISPLAY_CONFIDENCE_CAP),True

def diagnostic(artifact:dict[str,Any],row:pd.Series)->list[dict[str,Any]]:
    validate_schema(pd.DataFrame([row]));pipe=artifact["model"];source=pd.DataFrame([row])[FEATURE_COLUMNS];imputer=pipe.named_steps["imputer"];imputed=imputer.transform(source);scaled=pipe.named_steps["scale"].transform(imputed);coef=pipe.named_steps["model"].coef_[0];names=list(imputer.get_feature_names_out(FEATURE_COLUMNS));lookup={name:i for i,name in enumerate(names)}
    out=[]
    for name in FEATURE_COLUMNS:
        i=lookup.get(name);missing=bool(pd.isna(row[name]));weight=float(coef[i])if i is not None else 0.0;normalized=float(scaled[0,i])if i is not None else None
        out.append({"feature":name,"raw":None if missing else float(row[name]),"difference":None if missing else float(row[name]),"normalized":normalized,"sign":int(np.sign(weight)),"weight":weight,"contribution":None if normalized is None else normalized*weight,"missing":missing,"default":"training_median"if missing else None,"source":"fcs_pregame_history","freshness":"pregame"})
    return out
