"""Premium gate redaction tests."""

from app.services.premium_gate import AccessTier, redact_home_summary, redact_pick_payload


def test_redact_pick_payload_props_shape():
    payload = {
        "props": [{"player": "Judge", "edge": 0.06, "line": 1.5}],
        "top_props": [{"player": "Judge", "edge": 0.06}],
    }
    out = redact_pick_payload(payload, AccessTier.VISITOR)
    assert out["props"] == []
    assert out["premium_required"] is True


def test_visitor_home_summary_hides_picks():
    payload = {
        "board_available": True,
        "plus_ev_singles": 3,
        "plus_ev_totals": 1,
        "top_singles": [{"team": "NYY", "edge": 0.04, "confidence": "High"}],
        "slate_by_game_id": {
            "1": {"matchup": "A @ B", "ev_pick_edge": 0.05, "plus_ev_single": True}
        },
    }
    out = redact_home_summary(payload, AccessTier.VISITOR)
    assert out["top_singles"] == []
    assert out["premium_required"] is True
    assert "plus_ev_singles" not in out
    assert "ev_pick_edge" not in out["slate_by_game_id"]["1"]


def test_premium_home_summary_passthrough():
    payload = {
        "top_singles": [{"team": "NYY", "edge": 0.04}],
        "plus_ev_singles": 1,
    }
    out = redact_home_summary(payload, AccessTier.PREMIUM)
    assert out["top_singles"][0]["edge"] == 0.04
    assert out["premium_required"] is False
