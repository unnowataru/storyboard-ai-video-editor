"""壁の時計の時刻を録画の中の時刻に直すスクリプト (scripts/wallclock_to_tc.py) のテスト。Resolve は要らない。"""

import pytest


import wallclock_to_tc as W


# 録画ツールの時刻の記録の形 (壁の時計 / 録画の時刻 / 操作 / メモ、タブ区切り。
# 形式は docs/recording.md)。5 分録って 5 分止め、10 分録る
LOG = ("2026-10-01 10:00:00\t00:00:00.000\tstart\t\n"
       "2026-10-01 10:05:00\t00:05:00.000\tpause\tx\n"
       "2026-10-01 10:10:00\t00:05:00.000\tresume\t\n"
       "2026-10-01 10:20:00\t00:15:00.000\tstop\t\n")


def _log(tmp_path, text=LOG):
    p = tmp_path / "rec-timeline.log"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as e:
        W.main(["--help"])
    assert e.value.code == 0
    assert "HH:MM:SS" in capsys.readouterr().out


def test_converts_times(tmp_path, capsys):
    W.main([_log(tmp_path), "10:01:00", "10:07:00", "10:12:30"])
    assert capsys.readouterr().out.splitlines() == [
        "10:01:00 → 1:00.0",
        "10:07:00 → 録画に入っていない (一時停止中か録画の外)",
        "10:12:30 → 7:30.0",
    ]


def test_status_line_starts_a_span(tmp_path, capsys):
    # 人が 9:57 に始めた録画が進んでいることを 10:00 に確認した (status の行)。その後、一時停止・再開・停止した
    log = ("2026-10-01 10:00:00\t00:03:00.000\tstatus\t\n"
           "2026-10-01 10:05:00\t00:08:00.000\tpause\t\n"
           "2026-10-01 10:10:00\t00:08:00.000\tresume\t\n"
           "2026-10-01 10:20:00\t\tstop rec/2026-10-01 09-57-00.mp4\t\n")
    W.main([_log(tmp_path, log), "10:01:00", "10:07:00", "10:12:30"])
    assert capsys.readouterr().out.splitlines() == [
        "10:01:00 → 4:00.0",
        "10:07:00 → 録画に入っていない (一時停止中か録画の外)",
        "10:12:30 → 10:30.0",
    ]


def test_no_action_lines(tmp_path):
    p = _log(tmp_path, "")
    with pytest.raises(SystemExit) as e:
        W.main([p, "10:00:00"])
    assert e.value.code == f"記録に操作の行が無い: {p}"


def test_needs_a_time(tmp_path):
    # 時刻を 1 つも渡さなければ引数の誤り (2)
    with pytest.raises(SystemExit) as e:
        W.main([_log(tmp_path)])
    assert e.value.code == 2
