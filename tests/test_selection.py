from conftest import make_demo, make_kill, make_round

from highlights.analysis.moments import cluster_moments
from highlights.analysis.scoring import score_moments
from highlights.analysis.selection import select_sequences
from highlights.config import RecordingConfig, SelectionConfig


def _prepare(kills, rounds):
    demo = make_demo(kills, rounds)
    moments = cluster_moments(kills, demo.tickrate)
    score_moments(moments, demo)
    return demo, moments


def test_min_kills_filter():
    rounds = [make_round(0, 0, 100000), make_round(1, 100000, 200000)]
    kills = [
        make_kill(2000),  # single kill, no specials -> not eligible
        make_kill(110000, round_index=1, attacker="333"),
        make_kill(110100, round_index=1, attacker="333", victim="444"),
    ]
    demo, moments = _prepare(kills, rounds)
    sequences = select_sequences(moments, demo, SelectionConfig(), RecordingConfig())
    assert len(sequences) == 1
    assert sequences[0].moments[0].steamid == "333"


def test_player_filter_relaxes_min_kills():
    rounds = [make_round(0, 0, 100000)]
    kills = [make_kill(2000, attacker="111")]
    demo, moments = _prepare(kills, rounds)
    sequences = select_sequences(
        moments, demo, SelectionConfig(), RecordingConfig(), player_steamids=["111"]
    )
    assert len(sequences) == 1


def test_top_n_keeps_best_by_score():
    rounds = [make_round(i, i * 100000, (i + 1) * 100000) for i in range(3)]
    kills = []
    # Round 1: 2k. Round 2: ace (should win). Round 3: 2k.
    kills += [make_kill(5000 + i * 100, round_index=0, victim=str(i)) for i in range(2)]
    kills += [make_kill(105000 + i * 100, round_index=1, victim=str(i)) for i in range(5)]
    kills += [make_kill(205000 + i * 100, round_index=2, victim=str(i)) for i in range(2)]
    demo, moments = _prepare(kills, rounds)
    sequences = select_sequences(moments, demo, SelectionConfig(top=1), RecordingConfig())
    assert len(sequences) == 1
    assert sequences[0].round_index == 1
    assert sequences[0].moments[0].kill_count == 5


def test_overlapping_moments_merge_with_camera_switches():
    rounds = [make_round(0, 0, 100000)]
    kills = [
        make_kill(5000, attacker="111", victim="b1"),
        make_kill(5200, attacker="111", victim="b2"),
        make_kill(5400, attacker="333", victim="b3"),
        make_kill(5600, attacker="333", victim="b4"),
    ]
    demo, moments = _prepare(kills, rounds)
    sequences = select_sequences(moments, demo, SelectionConfig(), RecordingConfig())
    assert len(sequences) == 1
    seq = sequences[0]
    assert len(seq.cameras) == 2
    assert seq.cameras[0].steamid == "111"
    assert seq.cameras[1].steamid == "333"
    assert seq.cameras[0].tick == seq.start_tick
    assert seq.start_tick < seq.cameras[1].tick <= 5400


def test_clip_bounds_clamped_to_freeze_end():
    rounds = [make_round(0, 0, 100000)]  # freeze_end = 1000
    kills = [make_kill(1100), make_kill(1200, victim="333")]
    demo, moments = _prepare(kills, rounds)
    sequences = select_sequences(moments, demo, SelectionConfig(), RecordingConfig())
    # lead-in of 4s (256 ticks) would start before freeze end -> clamped.
    assert sequences[0].start_tick == 1000


def test_sequences_numbered_chronologically():
    rounds = [make_round(0, 0, 100000), make_round(1, 100000, 200000)]
    kills = [
        make_kill(105000, round_index=1, attacker="333"),
        make_kill(105100, round_index=1, attacker="333", victim="444"),
        make_kill(5000),
        make_kill(5100, victim="555"),
    ]
    demo, moments = _prepare(kills, rounds)
    sequences = select_sequences(moments, demo, SelectionConfig(), RecordingConfig())
    assert [s.number for s in sequences] == [1, 2]
    assert sequences[0].start_tick < sequences[1].start_tick


def test_players_filter_includes_only_listed():
    rounds = [make_round(i, i * 100000, (i + 1) * 100000) for i in range(3)]
    kills = [
        make_kill(5000, round_index=0, attacker="111"),
        make_kill(105000, round_index=1, attacker="222"),
        make_kill(205000, round_index=2, attacker="333"),
    ]
    demo, moments = _prepare(kills, rounds)
    sequences = select_sequences(
        moments, demo, SelectionConfig(), RecordingConfig(), player_steamids=["111", "333"]
    )
    steamids = {m.steamid for s in sequences for m in s.moments}
    assert steamids == {"111", "333"}


def test_players_filter_empty_means_no_filter():
    rounds = [make_round(0, 0, 100000)]
    kills = [make_kill(2000, attacker="111")]
    demo, moments = _prepare(kills, rounds)
    sequences = select_sequences(
        moments, demo, SelectionConfig(), RecordingConfig(), player_steamids=[]
    )
    assert len(sequences) == 0

    kills = [make_kill(2000, attacker="111"), make_kill(2100, attacker="111", victim="333")]
    demo, moments = _prepare(kills, rounds)
    sequences = select_sequences(
        moments, demo, SelectionConfig(), RecordingConfig(), player_steamids=[]
    )
    assert len(sequences) == 1
    assert sequences[0].moments[0].steamid == "111"


def test_players_filter_unlisted_excluded_even_if_best():
    rounds = [make_round(0, 0, 100000), make_round(1, 100000, 200000)]
    kills = [
        make_kill(5000 + i * 100, round_index=0, attacker="444", victim=str(i)) for i in range(5)
    ]
    kills += [make_kill(105000, round_index=1, attacker="111")]
    demo, moments = _prepare(kills, rounds)
    sequences = select_sequences(
        moments, demo, SelectionConfig(), RecordingConfig(), player_steamids=["111"]
    )
    assert len(sequences) == 1
    assert {m.steamid for m in sequences[0].moments} == {"111"}
