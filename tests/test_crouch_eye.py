import pytest
from conftest import make_demo, make_round

from highlights import spatial
from highlights.models import CameraAction, Sequence
from highlights.recording.dyncams import EYE_HEIGHT, eye_pose_at


def _sequence():
    return Sequence(
        number=1, start_tick=1000, end_tick=1640, cameras=[CameraAction(1000, "111")],
        label="x", score=1.0, round_index=0,
    )


def _positions(duck):
    sample = {"xyz": {"x": 10.0, "y": 20.0, "z": 100.0}, "yaw": 30.0, "pitch": 0.0, "duck_amount": duck}
    return {tick: {"111": sample} for tick in range(1000, 1700)}, [], None


@pytest.mark.parametrize("duck, eye_z", [(0.0, 164.0), (1.0, 146.0), (0.5, 155.0), (None, 164.0)])
def test_the_eye_height_follows_the_crouch(monkeypatch, duck, eye_z):
    monkeypatch.setattr(spatial, "_query_positions", lambda path, ticks: _positions(duck))
    demo = make_demo([], [make_round(0, 0, 5000)])
    sequence = _sequence()
    tracks = spatial.sample_pov_tracks(demo, {1: (sequence, [(0.0, 5.0)])})
    eye = eye_pose_at(tracks[1]["111"], 2.0)
    assert eye.pos[2] == pytest.approx(eye_z)  # feet 100 + (64 standing, 46 crouched)
    assert EYE_HEIGHT == 64.0
