from highlights.demo.parser import _normalize_side, _normalize_winner


def test_side_from_team_number_text_and_missing_values():
    assert _normalize_side(3) == "CT" and _normalize_side(2.0) == "T"
    assert _normalize_side("TERRORIST") == "T" and _normalize_side("CT") == "CT"
    assert _normalize_side(None) == ""


def test_round_without_a_winner_is_empty_not_an_error():
    # demoparser2 gives NaN for a round that never ended (demo cut off mid-round)
    assert _normalize_winner(float("nan")) == ""
