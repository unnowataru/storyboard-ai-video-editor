"""壁の時計の時刻 (例: 作業のメモやチャットの記録に残った 10:09:30) を、一時停止を挟んだ録画の中の時刻に直す。

使い方:
  python scripts/wallclock_to_tc.py rec-timeline.log 10:09:30 10:16:39 ...

rec-timeline.log は録画ツールの時刻の記録 (壁の時計 / 録画の時刻 / 操作 / メモ、タブ区切り)。
記録の形式は docs/recording.md。形式が変わったら、wallclock_to_tc.py も合わせて修正する。
一時停止中の時刻を渡すと「録画に入っていない」と出す。直した時刻は実際より 1〜2 秒早く出る (docs/recording.md)。
"""
import argparse
import datetime as dt
import sys

import common as C


def spans(lines):
    """記録の行から、録画が進んでいた時間 (壁の時計の始まり, その時の録画の時刻, 壁の時計の終わり) と、記録の日付を返す。"""
    out, cur, day = [], None, None
    for line in lines:
        p = line.rstrip("\r\n").split("\t")
        if len(p) < 3 or not p[2].strip():
            continue
        wall = dt.datetime.strptime(p[0], "%Y-%m-%d %H:%M:%S")
        day = wall.date()
        act = p[2].split()[0]
        # status は録画が進んでいることを確認した行 (録画の途中から記録を始めたときなど)。区間が開いていないときだけ始まりとして読む
        # (区間の途中の status で始まりを置き換えると、それより前の時刻を直せなくなる)
        if act in ("start", "resume") or (act == "status" and cur is None):
            cur = (wall, C.tc_to_sec(p[1]) if p[1] else 0.0)   # 録画の時刻 'hh:mm:ss.sss' を秒に
        elif act in ("pause", "stop") and cur:
            out.append((cur[0], cur[1], wall))
            cur = None
    if cur:
        out.append((cur[0], cur[1], dt.datetime.max))
    return out, day


def to_tc(sp, day, hms):
    """壁の時計の 'HH:MM:SS' を録画の中の秒に。録画に入っていなければ None。"""
    w = dt.datetime.combine(day, dt.datetime.strptime(hms, "%H:%M:%S").time())
    hit = [(a, tc0) for a, tc0, b in sp if a <= w <= b]
    return hit[0][1] + (w - hit[0][0]).total_seconds() if hit else None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log", help="録画ツールの時刻の記録 (形式は docs/recording.md)")
    ap.add_argument("times", nargs="+", help="壁の時計の時刻 'HH:MM:SS' (いくつでも)")
    a = ap.parse_args(argv)
    with open(a.log, encoding="utf-8") as f:
        sp, day = spans(f)
    if day is None:
        sys.exit(f"記録に操作の行が無い: {a.log}")
    for t in a.times:
        sec = to_tc(sp, day, t)
        print(t, "→", C.sec_to_mss(sec) if sec is not None else "録画に入っていない (一時停止中か録画の外)")


if __name__ == "__main__":
    main()
