"""UFC fighter name normalization tests."""

from app.odds.ufc_fighter_aliases import fighter_match_key, normalize_fighter_name


def test_normalize_fighter_name_strips_whitespace():
    assert normalize_fighter_name("  Jon  Jones  ") == "Jon Jones"


def test_fighter_match_key_ignores_punctuation():
    a = fighter_match_key("José Aldo")
    b = fighter_match_key("Jose Aldo")
    assert a == b


def test_fighters_match_last_name():
    from app.odds.ufc_fighter_aliases import fighters_match

    assert fighters_match("Jon Jones", "Jonathan Dwight Jones") is True
    assert fighters_match("Max Holloway", "Alexander Volkanovski") is False
