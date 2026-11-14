"""簡易版のパラメーターを測る (scripts/sb2ffmpeg_bench.py) のテスト。FFmpeg は流さない。"""
import json
import os

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="sb2ffmpeg_bench.py は Windows だけで動く")

import settings  # noqa: E402

B = pytest.importorskip("sb2ffmpeg_bench")


def test_candidates_split_logical_cpus_by_jobs():
    assert B.candidates(8) == [(1, "auto"), (1, "8,1,1"), (2, "auto"), (2, "4,1,1")]
    assert B.candidates(1) == [(1, "auto"), (1, "1,1,1"), (2, "auto"), (2, "1,1,1")]


def test_run_order_alternates_direction():
    assert B.run_order(3, 2) == [(0, 0), (0, 1), (0, 2), (1, 2), (1, 1), (1, 0)]


def test_best_skips_candidates_with_failures():
    s = [{"jobs": 1, "threads": "auto", "failed": 0, "median_seconds": 50},
         {"jobs": 2, "threads": "auto", "failed": 1, "median_seconds": 30},
         {"jobs": 2, "threads": "4,1,1", "failed": 0, "median_seconds": 40}]
    assert B.best(s)["threads"] == "4,1,1"
    assert B.best([dict(s[1])]) is None


def test_apply_writes_only_sb2ffmpeg_section(tmp_path, monkeypatch):
    local = tmp_path / "local.json"
    local.write_text(json.dumps({"paths": {"work_dir": "x"}, "sb2ffmpeg": {"jobs": 9}}), encoding="utf-8")
    monkeypatch.setenv("RESOLVE_CONFIG", str(local))
    monkeypatch.setattr(settings, "_CFG", None)
    assert B.apply({"jobs": 1, "threads": "4,1,1"}) == str(local)
    got = json.loads(local.read_text(encoding="utf-8"))
    assert got == {"paths": {"work_dir": "x"}, "sb2ffmpeg": {"jobs": 1, "threads": "4,1,1"}}
    monkeypatch.setattr(settings, "_CFG", None)


def test_measure_stops_the_clock_when_the_command_ends():
    """すぐ終わるコマンドは、1 秒の待ちを足さずに測る。終了コードと 1 行目を残す。"""
    import sys
    m = B.measure([sys.executable, "-c", "print('done'); raise SystemExit(3)"])
    assert m["seconds"] < 1 and m["rc"] == 3 and m["first_line"] == "done"


def test_memory_and_cpu_readings_are_sane():
    total, avail, commit = B.memory()
    assert 0 < avail <= total and commit > 0
    idle, all_ = B.cpu_times()
    assert 0 < idle <= all_
