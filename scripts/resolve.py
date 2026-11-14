"""Resolve への接続と、時刻の変換・行の長さ・止めて置く区間 (scripts/common.py から読み込み直す部品)。

接続 (import_dvr・connect_resolve・connect_or_exit) と、Resolve のメディアプールと書き出しを読む小さな部品
(source_fps・clip_resolution・render_jobs)。時刻は 'h:mm:ss' と秒の変換 (tc_to_sec)、タイムコードとフレームの変換、
構成表の行ごとの位置とフレーム数 (row_spans・row_clips)、止めて置く区間 (hold_*)、速さを変えたクリップの合成の時刻 (comp_frame)、
現れ方のフレーム (appear_keys・appear_frames)。
"""
import math
import os
import re
import sys
import time
import unicodedata

from settings import config


# ---------- Resolve への接続 ----------
def import_dvr():
    """DaVinciResolveScript を読み込む。場所は設定から (環境変数 RESOLVE_SCRIPT_API / RESOLVE_SCRIPT_LIB が優先)。"""
    cfg = config()
    api = cfg.require("resolve_script_api", "Resolve Studio が入っているかを確認する")
    lib = cfg.require("resolve_script_lib")
    os.environ["RESOLVE_SCRIPT_API"] = api
    os.environ["RESOLVE_SCRIPT_LIB"] = lib
    mod = os.path.join(api, "Modules")
    if mod not in sys.path:
        sys.path.append(mod)
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(os.path.dirname(lib))
    import DaVinciResolveScript as dvr
    return dvr


def connect_resolve(tries=1, wait=5, quiet=False):
    """起動中の Resolve に接続して Resolve オブジェクトを返す。つながらなければ None。

    接続する前に、resolve_lock.py run で包まれていてロックの持ち主がこの処理か (resolve_lock.require_lock) を確認し、
    違えば警告を出す (起動中の Resolve は 1 つで、同時に動く処理が同じものを操作するため)。config の
    resolve.require_lock が true なら、違うときは接続せずに終わる。"""
    import resolve_lock   # resolve_lock が common を読むので、ここで読む
    resolve_lock.require_lock()
    dvr = import_dvr()
    for i in range(max(1, tries)):
        r = dvr.scriptapp("Resolve")
        if r:
            return r
        if not quiet:
            print(f"接続できない ({i + 1}/{tries})", file=sys.stderr)
        if i + 1 < tries:
            time.sleep(wait)
    return None


# 接続できないときの文。connect_or_exit・sb2resolve.py・check_final.py が同じ文で出す
NO_RESOLVE = "Resolve に接続できない"
RESOLVE_HINT = "Resolve Studio が起動しているか、環境設定 > システム > 一般 > 「外部スクリプトに使用」が「ローカル」かを確認する"


def connect_or_exit(tries=1):
    r = connect_resolve(tries)
    if not r:
        sys.exit(f"{NO_RESOLVE}。{RESOLVE_HINT}")
    return r


# ---------- 時刻 ----------
def tc_to_sec(v):
    """'h:mm:ss' / 'mm:ss' / 'mm:ss.s' / 秒 (数) を秒に。"""
    if isinstance(v, (int, float)):
        return float(v)
    s = 0.0
    for p in str(v).split(":"):
        s = s * 60 + float(p)
    return s


def timecode_to_frame(tc, fps):
    """Resolve のタイムコード 'HH:MM:SS:FF' をフレームの数に。fps は整数 (30 など)。ドロップフレームは扱わない。"""
    h, m, s, f = (int(x) for x in str(tc).replace(";", ":").split(":"))
    return ((h * 60 + m) * 60 + s) * int(fps) + f


def frame_to_timecode(n, fps):
    """フレームの数を 'HH:MM:SS:FF' に (timecode_to_frame の逆)。"""
    s, f = divmod(int(n), int(fps))
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}:{f:02d}"


def sec_to_mss(t):
    """秒を 'm:ss.s' に。先に 0.1 秒に丸める (59.96 秒が 0:60.0 にならないように)。"""
    m, s = divmod(round(float(t) * 10), 600)
    return f"{m}:{s / 10:04.1f}"


def hold_seconds_ok(h):
    """行の hold の seconds が読めるか (dict で、seconds が 0 より大きい数)。hold_frames と hold_problems が同じ判定を使う。"""
    return isinstance(h, dict) and isinstance(h.get("seconds"), (int, float)) and h["seconds"] > 0


def hold_frames(sb, row):
    """行の後ろに映像を止めて置くフレーム数 (行の hold.seconds。無ければ 0)。"""
    h = row.get("hold")
    if not hold_seconds_ok(h):
        return 0   # 無いか読めない hold (読めないものは点検と照らし合わせで誤り・unknown にする)
    return round(float(h["seconds"]) * float(sb["fps"]))


def row_spans(sb):
    """構成表の行ごとの、書き出しでの (始まりのフレーム, フレーム数)。行の秒数は行の seconds、無ければ seconds_per_row。
    seconds_per_row が "source" なら、seconds の無い行は使う範囲の長さ (end − start) にする (等倍)。
    行に hold があれば、止めて置く分 (hold_frames) だけ後ろの行がずれる (止めた区間は行のフレーム数に入れない)。"""
    fps, per = float(sb["fps"]), sb["seconds_per_row"]
    out, at = [], 0
    for r in sb["rows"]:
        sec = r["seconds"] if "seconds" in r else (
            tc_to_sec(r["end"]) - tc_to_sec(r["start"]) if per == "source" else per)
        n = round(float(sec) * fps)
        out.append((at, n))
        at += n + hold_frames(sb, r)
    return out


def row_clips(sb):
    """構成表の行ごとの、素材のファイル (行の source、無ければ構成表の source)・使う範囲 (秒)・書き出しでの位置と
    フレーム数 (row_spans)・止めて置くフレーム数 (hold_frames)。組む側 (sb2resolve.plan)・照らし合わせ
    (check_final.expected_rows)・ほかの形式 (sb2nle.load) は、構成表の行をここで読む。

    ここは構成表の読み方だけで、速さや止めたクリップの位置など照らし合わせで確認する値は決めない
    (組む側の誤りを照らし合わせが同じように持たないように、それぞれで計算する)。
    """
    out = []
    for i, (r, (at, n)) in enumerate(zip(sb["rows"], row_spans(sb))):
        out.append({"row": i + 1, "source": r.get("source") or sb["source"], "start": tc_to_sec(r["start"]),
                    "end": tc_to_sec(r["end"]), "rec": at, "frames": n, "hold_frames": hold_frames(sb, r)})
    return out


# 肩テロップの文の速さの表記。「N 倍速」(N は整数か小数。前に / や数字が付く「1/4 倍速」は読まない) か「等倍」
_BADGE_SPEED = re.compile(r"(?<![0-9./])([0-9]+(?:\.[0-9]+)?)\s*倍速|等倍")
SAME_SPEED_TOL = 0.01   # 肩テロップに「等倍」と書いた行の、実際の速さと 1 の差の許し幅 (1 %)


def badge_speed_label(text):
    """肩テロップの文 (行の badge。badge.prefix は付けない) の速さの表記を読む。全角の数字と小数点も読む (NFKC で直す)。
    {"text": 表示の文 ("9 倍速"・"等倍"), "speed": 倍率, "places": 小数の桁 (「等倍」は None)} を返す。
    表記が無ければ None (速さを比べない)。表記が 2 つ以上あれば最初のもの。

    読むだけで、実際の速さと合うかは決めない (組む側の sb2resolve.badge_speed_problem と照らし合わせの
    check_final.check_badge_speed がそれぞれで決める)。"""
    if not isinstance(text, str):
        return None
    m = _BADGE_SPEED.search(unicodedata.normalize("NFKC", text))
    if not m:
        return None
    if m.group(1) is None:
        return {"text": "等倍", "speed": 1.0, "places": None}
    num = m.group(1)
    return {"text": f"{num} 倍速", "speed": float(num), "places": len(num.split(".")[1]) if "." in num else 0}


def hold_problems(h, length=None):
    """行の hold の書式の誤り (文の list)。length は素材の長さ (秒、分からなければ None)。"""
    out = []
    if not hold_seconds_ok(h):
        return ["hold.seconds は 0 より大きい秒"]
    at = h.get("at", "end")
    if at != "end":
        try:
            t = tc_to_sec(at)
        except (TypeError, ValueError):
            return ["hold.at は end か素材の時刻"]
        if t < 0 or (length is not None and t >= length):
            out.append("hold.at が素材の範囲の外")
    return out


def hold_source_frame(row, sfps, frames, speed=1.0):
    """止めて見せる素材のフレーム番号。at が end (既定) ならその行で最後に映るフレーム、時刻ならその時刻のフレーム。

    行で最後に映るのは、行の頭の素材のフレーム + 最後の書き出しのフレームの合成の時刻 (comp_frame(frames - 1, speed))。
    倍速の行では round(end × fps) − 1 より前になる (止めたときに画が飛ばないように、映っていたフレームで止める)。
    """
    at = (row.get("hold") or {}).get("at", "end")
    if at == "end":
        return round(tc_to_sec(row["start"]) * sfps) + comp_frame(frames - 1, speed)
    return round(tc_to_sec(at) * sfps)


def hold_time(row, sfps, frames, speed=1.0):
    """止めた瞬間を、行のクリップの合成の時刻 (0〜comp_frame(frames - 1)) で表す。画に付く位置をその瞬間で止めるのに使う。"""
    t = hold_source_frame(row, sfps, frames, speed) - round(tc_to_sec(row["start"]) * sfps)
    return min(max(t, 0), comp_frame(frames - 1, speed))


def appear_keys(start, length):
    """行の頭から start フレームで現れ始め、length フレームで現れ終わる割合 (0〜1) のキー [(フレーム, 割合)]。"""
    if start <= 0 and length <= 0:
        return [(0, 1.0)]
    if length <= 0:
        return [(0, 0.0), (start - 1, 0.0), (start, 1.0)] if start > 1 else [(0, 0.0), (start, 1.0)]
    keys = [(0, 0.0)] + ([(start, 0.0)] if start > 0 else []) + [(start + length, 1.0)]
    return keys


def appear_frames(spec, defaults, length_key, fps):
    """focus と callout の現れ方の (現れ始めるフレーム, 現れるのにかけるフレーム数)。どちらも行の頭からの書き出しのフレーム。
    spec は構成表の項目、defaults は config の既定 (start と length_key の秒)。"""
    c = dict(defaults) | spec
    return round(float(c["start"]) * fps), round(float(c[length_key]) * fps)


def comp_frame(f, speed=1.0):
    """行の頭からの書き出しのフレーム f を、そのクリップの Fusion の合成の中の時刻 (フレーム) にする。

    速さを変えたクリップでは、合成の中の時刻が素材の速さで進む (4 倍速のクリップの書き出しの 15 フレーム目は合成の 60)。
    割り切れない倍速では切り捨てた時刻の画になる (1.5 倍速の 15 フレーム目は合成の 22)。どちらも 21.1.1 の実機
    (tests/resolve/sb2resolve_4k.py)。speed は倍率 (速さ % ÷ 100)。素材とタイムラインのフレームレートが違うときは確認していない。
    """
    return math.floor(f * speed + 1e-6)


# ---------- 素材 ----------
def source_fps(mpi, fallback):
    """メディアプールの項目の素材のフレームレート (クリップ属性 FPS)。読めなければ fallback。

    AppendToTimeline の startFrame / endFrame は素材のフレームなので、秒からの換算は素材のフレームレートで行う。
    """
    try:
        f = float(str(mpi.GetClipProperty("FPS")).split()[0])
    except (ValueError, IndexError):
        return float(fallback)
    return f if f > 0 else float(fallback)


def clip_resolution(mpi):
    """メディアプールの項目の解像度 (クリップ属性 Resolution の "3840x2160")。読めなければ (None, None)。"""
    try:
        w, h = str(mpi.GetClipProperty("Resolution")).lower().split("x")
        return int(w), int(h)
    except (ValueError, AttributeError):
        return None, None


# ---------- 書き出し ----------
def render_jobs(proj, jobs, interval, limit):
    """書き出しの作業を始めて終わるまで待つ。待つ間隔と上限 (秒) は呼ぶ側が渡す。

    返す値: started (StartRendering の戻り値)、done (上限までに終わり、全部の作業が Complete)、
    busy (返すときにまだ書き出し中か。上限で待つのをやめたときは真のことがある)、status (作業ごとの GetRenderJobStatus)、seconds。
    """
    t0 = time.time()
    started = proj.StartRendering(jobs, isInteractiveMode=False)
    busy = proj.IsRenderingInProgress()
    while busy and time.time() - t0 < limit:
        time.sleep(interval)
        busy = proj.IsRenderingInProgress()
    status = {j: proj.GetRenderJobStatus(j) for j in jobs}
    done = bool(started) and not busy and all((s or {}).get("JobStatus") == "Complete" for s in status.values())
    return {"started": started, "done": done, "busy": bool(busy), "status": status, "seconds": round(time.time() - t0, 1)}
