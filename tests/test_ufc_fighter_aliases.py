"""UFC fighter name normalization tests."""

import pytest

from app.odds.ufc_fighter_aliases import (
    fighter_match_key,
    fighter_slug,
    fighters_match,
    normalize_fighter_name,
)


def test_normalize_fighter_name_strips_whitespace():
    assert normalize_fighter_name("  Jon  Jones  ") == "Jon Jones"


def test_fighter_match_key_ignores_punctuation():
    a = fighter_match_key("José Aldo")
    b = fighter_match_key("Jose Aldo")
    assert a == b


def test_fighters_match_last_name():
    assert fighters_match("Jon Jones", "Jonathan Dwight Jones") is True
    assert fighters_match("Max Holloway", "Alexander Volkanovski") is False


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("McGregor, Conor", "Conor McGregor"),
        ('"The Notorious" Conor McGregor', "Conor McGregor"),
        ("Dricus Du Plessis", "Dricus Du Plessis"),
        ("Sean O'Malley", "Sean O'Malley"),
        ("Gaston Bolaños", "Gaston Bolaños"),
        ("Waldo Cortes-Acosta", "Waldo Cortes Acosta"),
        ("Alex Volkanovski", "Alex Volkanovski"),
        ("Alexander Volkanovski", "Alex Volkanovski"),
        ("Charles Oliveira", "Charles Oliveira"),
        ("Do Bronx Charles Oliveira", "Charles Oliveira"),
    ],
)
def test_normalize_alias_edge_cases(raw, expected):
    assert normalize_fighter_name(raw) == expected


def test_fighters_match_initial_last():
    assert fighters_match("J. Miller", "Jim Miller") is True


def test_fighter_slug():
    assert fighter_slug("Conor McGregor") == "conor-mcgregor"


def test_last_first_comma_format():
    assert normalize_fighter_name("Holloway, Max") == "Max Holloway"
