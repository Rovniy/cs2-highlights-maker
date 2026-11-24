import pytest


def test_spikes_are_local_peaks_above_the_noise():
    from highlights.video.killfeed_sync import spikes

    activity = [1.0] * 120
    activity[30] = 20.0
    activity[31] = 12.0
    activity[90] = 15.0
    assert spikes(activity, fps=60) == [0.5, 1.5]


def test_measure_offset_needs_agreement():
    from highlights.video.killfeed_sync import measure_offset

    offset, matched = measure_offset([2.0, 5.0, 9.0], [2.2, 5.21, 9.19, 7.0])
    assert offset == pytest.approx(0.2, abs=0.02) and matched == 3
    assert measure_offset([2.0, 5.0, 9.0], [2.2, 6.0, 10.4]) == (None, 1)
    assert measure_offset([4.0], [4.1, 4.9]) == (None, 0)  # one kill, two candidate rows
    assert measure_offset([4.0], [4.1])[0] == pytest.approx(0.1)
    assert measure_offset([], [1.0]) == (None, 0)
