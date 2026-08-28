"""Separate, disabled-by-default FCS-vs-FCS model family."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
import joblib,numpy as np,pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from app.config import PROJECT_ROOT
from app.features.fcs_pregame import FEATURE_COLUMNS,build_features
from app.models.platt_calibration import PlattCalibrator

MODEL_PATH=PROJECT_ROOT/"data/processed/fcs_baseline_model.joblib"
MANIFEST_PATH=PROJECT_ROOT/"data/processed/active_fcs_model.json"
REPORT_PATH=PROJECT_ROOT/"data/processed/fcs_model_evaluation.json"
MODEL_FAMILY="fcs_moneyline";MODEL_VERSION="fcs_v1_logistic_platt"

def validate_schema(frame:pd.DataFrame)->None:
    missing=[c for c in FEATURE_COLUMNS if c not in frame]
    if missing: raise ValueError(f"FCS feature schema mismatch: {missing}")

def train_separate(games:pd.DataFrame)->dict[str,Any]:
    if set(games.get("cohort",pd.Series(["fcs_vs_fcs"])).dropna().unique())!={"fcs_vs_fcs"}: raise ValueError("FCS trainer accepts FCS-vs-FCS rows only")
    feat=build_features(games);validate_schema(feat);base=feat[feat.season<=2023];cal=feat[feat.season==2024];hold=feat[feat.season==2025]
    if min(len(base),len(cal),len(hold))==0: raise ValueError("FCS requires <=2023 train, 2024 calibration, 2025 locked holdout")
    pipe=Pipeline([("imputer",SimpleImputer(strategy="median",add_indicator=True)),("scale",StandardScaler()),("model",LogisticRegression(max_iter=2000,random_state=42))]);pipe.fit(base[FEATURE_COLUMNS],base.home_win)
    platt=PlattCalibrator().fit(pipe.predict_proba(cal[FEATURE_COLUMNS])[:,1],cal.home_win.to_numpy());p=platt.transform(pipe.predict_proba(hold[FEATURE_COLUMNS])[:,1]);home=float(hold.home_win.mean())
    from sklearn.metrics import accuracy_score,brier_score_loss,log_loss
    metrics={"games":len(hold),"accuracy":float(accuracy_score(hold.home_win,p>=.5)),"brier":float(brier_score_loss(hold.home_win,p)),"log_loss":float(log_loss(hold.home_win,p)),"home_accuracy":home}
    metrics["promoted"]=bool(metrics["log_loss"]<-(home*np.log(home)+(1-home)*np.log(1-home))-.01 and metrics["brier"]<home*(1-home)-.005)
    artifact={"model_family":MODEL_FAMILY,"model_version":MODEL_VERSION,"feature_columns":FEATURE_COLUMNS,"model":pipe,"calibrator":platt,"metrics":metrics};joblib.dump(artifact,MODEL_PATH);REPORT_PATH.write_text(json.dumps(metrics,indent=2),encoding="utf-8");MANIFEST_PATH.write_text(json.dumps({"enabled":metrics["promoted"],"path":str(MODEL_PATH.relative_to(PROJECT_ROOT)),"model_family":MODEL_FAMILY,"model_version":MODEL_VERSION,"metrics":metrics},indent=2),encoding="utf-8");return metrics

def load_artifact()->dict[str,Any]:
    manifest=json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if not manifest.get("enabled"): raise RuntimeError("FCS model has not passed promotion gates")
    artifact=joblib.load(PROJECT_ROOT/manifest["path"])
    if artifact.get("model_family")!=MODEL_FAMILY: raise ValueError("FCS artifact family mismatch")
    if artifact.get("feature_columns")!=FEATURE_COLUMNS: raise ValueError("FCS artifact schema mismatch")
    return artifact

def predict_fcs(features:pd.DataFrame)->np.ndarray:
    artifact=load_artifact();validate_schema(features)
    raw=artifact["model"].predict_proba(features[FEATURE_COLUMNS])[:,1]
    probs=artifact["calibrator"].transform(raw)
    if np.any((probs<=0)|(probs>=1)): raise ValueError("Invalid FCS calibrated probability")
    return probs

def diagnostic(artifact:dict[str,Any],row:pd.Series)->list[dict[str,Any]]:
    validate_schema(pd.DataFrame([row]));pipe=artifact["model"];imputed=pipe.named_steps["imputer"].transform(pd.DataFrame([row])[FEATURE_COLUMNS]);scaled=pipe.named_steps["scale"].transform(imputed);coef=pipe.named_steps["model"].coef_[0]
    return [{"feature":name,"raw":None if pd.isna(row[name]) else float(row[name]),"difference":None if pd.isna(row[name]) else float(row[name]),"normalized":float(scaled[0,i]),"sign":int(np.sign(coef[i])),"weight":float(coef[i]),"contribution":float(scaled[0,i]*coef[i]),"missing":bool(pd.isna(row[name])),"default":None,"source":"fcs_pregame","freshness":"pregame"} for i,name in enumerate(FEATURE_COLUMNS)]
