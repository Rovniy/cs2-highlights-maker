import pytest

from highlights.analysis.selection import apply_picks, parse_numbers


def test_pick_and_exclude_keep_table_numbers():
    assert parse_numbers(["1,3-5", "4,7"]) == [1, 3, 4, 5, 7]
    rows = list("abcdefg")
    assert apply_picks(rows, [1, 3, 5], [3]) == [(1, "a"), (5, "e")]
    assert apply_picks(rows, [], [2]) == [(1, "a"), *[(n, rows[n - 1]) for n in range(3, 8)]]
    for bad in ("x", "0", "5-3", "2-"):
        with pytest.raises(ValueError):
            parse_numbers([bad])
    with pytest.raises(ValueError):
        apply_picks(rows, [9], [])
