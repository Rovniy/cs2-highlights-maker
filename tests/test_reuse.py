from types import SimpleNamespace

from conftest import make_demo, make_kill, make_round

from highlights.models import Moment


def test_reuse_records_only_new_or_changed_sequences(tmp_path, monkeypatch):
    from highlights import batch
    from highlights.analysis.selection import build_sequences
    from highlights.config import Config

    demo = make_demo([make_kill(5000), make_kill(30000)], [make_round(0, 0, 20000), make_round(1, 25000, 45000)])
    moments = [Moment("111", "p", "CT", 0, [demo.kills[0]]), Moment("111", "p", "CT", 1, [demo.kills[1]])]
    config = Config()
    sequences = build_sequences(moments, demo, config.selection, config.recording, merge=False)
    job = SimpleNamespace(label="d", demo=demo, work_dir=tmp_path)
    recorded_calls = []

    def fake_record(d, todo, cfg, work_dir):
        recorded_calls.append([s.name for s in todo])
        for s in todo:
            (work_dir / s.name).mkdir(parents=True, exist_ok=True)
            (work_dir / s.name / "video.mp4").write_bytes(b"x" * 2048)

    def fake_verify(seqs, work_dir):
        return [SimpleNamespace(sequence=s) for s in seqs if (work_dir / s.name / "video.mp4").is_file()]

    # The real recorder module is Windows-only (winreg); only these two functions are used here.
    monkeypatch.setitem(
        __import__("sys").modules,
        "highlights.recording.recorder",
        SimpleNamespace(record=fake_record, verify_outputs=fake_verify),
    )
    batch.record_or_reuse(job, sequences, config, reuse=False)
    batch.record_or_reuse(job, sequences, config, reuse=True)
    config.recording.fps = 120  # changes every recording
    batch.record_or_reuse(job, sequences[:1], config, reuse=True)
    assert recorded_calls == [["1-sequence", "2-sequence"], ["1-sequence"]]
