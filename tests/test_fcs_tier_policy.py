import pytest
from app.models.fcs_tier_policy import cap_home_probability,load_tier_policy,tier_decision,tier_is_validated,tier_key_for_probability

@pytest.mark.parametrize("prob,key",[(.50,"toss_up"),(.549999,"toss_up"),(.55,"lean"),(.699999,"lean"),(.70,"strong"),(.849999,"strong"),(.85,"elite"),(.90,"elite")])
def test_exact_tier_boundaries(prob,key):assert tier_key_for_probability(prob)==key

def test_probability_above_ninety_is_capped_before_policy():
    shown,capped=cap_home_probability(.97)
    assert shown==.90 and capped is True and tier_key_for_probability(shown)=="elite"
    shown,capped=cap_home_probability(.03)
    assert shown==pytest.approx(.10) and capped is True

def test_sample_and_accuracy_gate_are_predeclared():
    row={"accuracy_target":.70,"by_season":{"2024":{"games":29,"accuracy":.90},"2025":{"games":40,"accuracy":.90}}}
    assert tier_is_validated(row,30)is False
    row["by_season"]["2024"]["games"]=30
    assert tier_is_validated(row,30)is True
    row["by_season"]["2025"]["accuracy"]=.69
    assert tier_is_validated(row,30)is False

def test_checked_in_policy_exposes_only_lean_and_strong():
    policy=load_tier_policy()
    assert tier_decision(.52,policy)["public_tier_label"] is None
    assert tier_decision(.60,policy)["public_tier_label"]=="Lean"
    assert tier_decision(.75,policy)["public_tier_label"]=="Strong"
    assert tier_decision(.88,policy)["public_tier_label"] is None
