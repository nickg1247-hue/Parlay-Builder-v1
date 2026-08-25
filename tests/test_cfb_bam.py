"""Leakage-safe, immutable CFB BAM evaluation tests."""
from dataclasses import FrozenInstanceError
import math
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.services.cfb_bam import (
    BAMOOSRecord, build_bam_report, freeze_oos_records, metric_summary,
    reliability_summary, tier_for_probability, wilson_interval,
)

def row(game, season, probability, won, source="walk_forward_oos"):
    return {"game_id":game,"season":season,"predicted_probability":probability,"won":won,"source":source}

@pytest.mark.parametrize(("probability","key"),[(.50,"bam_1"),(.599999,"bam_1"),(.60,"bam_2"),(.749999,"bam_2"),(.75,"bam_3"),(.899999,"bam_3"),(.90,"bam_4"),(1.0,"bam_4")])
def test_tier_boundaries_and_endpoint_inclusivity(probability,key):
    assert tier_for_probability(probability).key == key

@pytest.mark.parametrize("probability",[.499999,1.000001])
def test_tier_rejects_outside_range(probability):
    with pytest.raises(ValueError): tier_for_probability(probability)

def test_metrics_brier_guarded_logloss_goal_and_calibration_gaps():
    records=(BAMOOSRecord("a",2023,1.0,0,1.0,0),BAMOOSRecord("b",2023,.75,1,.75,1))
    got=metric_summary(records,.60)
    assert got["brier_score"] == round((1+.25**2)/2,4)
    assert math.isfinite(got["log_loss"])
    assert got["accuracy_pct"] == 50
    assert got["goal_gap_points"] == -10
    assert got["confidence_calibration_gap_points"] == -37.5

def test_wilson_and_empty_tier_and_small_sample():
    lo,hi=wilson_interval(5,10)
    assert 0 < lo < .5 < hi < 1
    report=build_bam_report([row("a",2023,.60,1)])
    tiers=report["overall"]["tiers"]
    assert tiers[0]["count"] == 0 and tiers[0]["accuracy_pct"] is None
    assert tiers[1]["small_sample"] is True

def test_reliability_bins_and_ece():
    records=(BAMOOSRecord("a",2023,.50,1,.50,1),BAMOOSRecord("b",2023,.60,0,.60,0),BAMOOSRecord("c",2023,1.0,1,1.0,1))
    got=reliability_summary(records)
    expected=(abs(1-.5)+abs(0-.6)+abs(1-1.0))/3*100
    assert got["ece_pct"] == round(expected,2)
    assert len(got["bins"]) == 10
    assert sum(b["count"] for b in got["bins"]) == 3

def test_records_are_immutable_and_inputs_are_copied():
    source=row("a",2023,.8,1)
    records=freeze_oos_records([source]); source["won"]=0
    assert records[0].won == 1
    with pytest.raises(FrozenInstanceError): records[0].won=0

def test_aggregation_rejects_non_generated_records_and_ignores_training_only():
    with pytest.raises(ValueError): build_bam_report([row("x",2023,.8,1,"production")])
    got=build_bam_report([row("train",2022,.9,1),row("test",2023,.9,0)])
    assert got["overall"]["metrics"]["count"] == 1
    assert got["training_only_seasons"] == [2022]

def test_bam_routes(monkeypatch):
    client=TestClient(app)
    page=client.get("/cfb/bam-progress")
    assert page.status_code == 200 and "CFB BAM progress" in page.text
    saved={"status":"ok","bam":{"status":"ok","overall":{},"by_season":[]}}
    monkeypatch.setattr("app.main.load_saved_cfb_backtest_report",lambda:saved)
    assert client.get("/api/cfb/bam-progress").json()["status"] == "ok"

def test_bam_missing_route(monkeypatch):
    client=TestClient(app)
    monkeypatch.setattr("app.main.load_saved_cfb_backtest_report",lambda:{"status":"missing","error":"none"})
    assert client.get("/api/cfb/bam-progress").json()["status"] == "missing"

