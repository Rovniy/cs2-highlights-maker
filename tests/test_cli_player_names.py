"""Tests for the --player-name STEAMID=NAME option parsing."""

import click
import pytest

import highlights.cli as cli


def test_single_entry():
    assert cli._parse_player_names(("76561198000000000=Ace",)) == {"76561198000000000": "Ace"}


def test_multiple_entries():
    result = cli._parse_player_names(("111=Alice", "222=Bob"))
    assert result == {"111": "Alice", "222": "Bob"}


def test_name_containing_equals_kept_whole():
    assert cli._parse_player_names(("111=a=b=c",)) == {"111": "a=b=c"}


def test_whitespace_stripped():
    assert cli._parse_player_names(("  111  =  Alice  ",)) == {"111": "Alice"}


def test_duplicate_id_last_wins():
    assert cli._parse_player_names(("111=First", "111=Second")) == {"111": "Second"}


def test_empty_tuple():
    assert cli._parse_player_names(()) == {}


@pytest.mark.parametrize(
    "value",
    [
        "no-equals-sign",
        "abc=Alice",
        "111=",
        "=Alice",
    ],
)
def test_invalid_values_raise(value):
    with pytest.raises(click.BadParameter):
        cli._parse_player_names((value,))
